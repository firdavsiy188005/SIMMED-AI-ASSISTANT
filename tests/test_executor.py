# -*- coding: utf-8 -*-
"""AgentExecutor control-flow tests.

Planner, error handler and tools are replaced with scripted fakes so every
branch (retry / skip / replan / abort / cancel / summarise) can be driven
deterministically and without network access.
"""
import threading

import pytest

import agent.executor as ex
from agent.error_handler import ErrorDecision


def S(n, tool="web_search", **params):
    return {"step": n, "tool": tool, "description": f"step {n}", "parameters": params or {"q": n}}


class Script:
    """Scripted tool results: dict tool -> list of results/exceptions."""

    def __init__(self, **per_tool):
        self.per_tool = {k: list(v) for k, v in per_tool.items()}
        self.calls = []

    def __call__(self, tool, params, speak):
        self.calls.append((tool, params))
        seq = self.per_tool.get(tool)
        r = seq.pop(0) if seq else f"{tool}-ok"
        if isinstance(r, BaseException):
            raise r
        return r


@pytest.fixture
def env(monkeypatch, fake_genai, api_key_file):
    """Wire fakes into agent.executor; return a namespace to configure them."""
    class Env:
        plans = []
        replans = []
        decisions = []
        spoken = []
        script = Script()
        fixes = []

    e = Env()
    monkeypatch.setattr(ex, "create_plan", lambda goal: e.plans.pop(0))

    def fake_replan(goal, done, failed, err):
        e.replan_args = (goal, list(done), failed, err)
        return e.replans.pop(0)

    monkeypatch.setattr(ex, "replan", fake_replan)

    def fake_analyze(step, err, attempt=1, max_attempts=2):
        d = e.decisions.pop(0) if e.decisions else ErrorDecision.REPLAN
        return {"decision": d, "reason": "why", "fix_suggestion": "", "user_message": "msg"}

    monkeypatch.setattr(ex, "analyze_error", fake_analyze)
    monkeypatch.setattr(ex, "_call_tool", lambda t, p, s: e.script(t, p, s))
    monkeypatch.setattr("time.sleep", lambda *_: None)
    fake_genai.default = "Summary sentence, sir."
    e.speak = e.spoken.append
    e.genai = fake_genai
    return e


def run(env, goal="do it", cancel=None):
    return ex.AgentExecutor().execute(goal, speak=env.speak, cancel_flag=cancel)


def test_all_steps_succeed_returns_summary(env):
    env.plans.append({"steps": [S(1), S(2, "file_controller", action="read")]})
    out = run(env)
    assert out == "Summary sentence, sir."
    assert [c[0] for c in env.script.calls] == ["web_search", "file_controller"]
    assert env.spoken[-1] == out


def test_empty_plan(env):
    env.plans.append({"steps": []})
    assert "couldn't create a valid plan" in run(env)


def test_retry_then_success(env):
    env.plans.append({"steps": [S(1)]})
    env.script = Script(web_search=[TimeoutError("t/o"), "ok"])
    env.decisions = [ErrorDecision.RETRY]
    run(env)
    assert len(env.script.calls) == 2


def test_skip_continues_with_next_step(env):
    env.plans.append({"steps": [S(1, "open_app"), S(2)]})
    env.script = Script(open_app=[RuntimeError("no app")])
    env.decisions = [ErrorDecision.SKIP]
    run(env)
    assert [c[0] for c in env.script.calls] == ["open_app", "web_search"]


def test_abort_stops_immediately(env):
    env.plans.append({"steps": [S(1), S(2)]})
    env.script = Script(web_search=[RuntimeError("fatal")])
    env.decisions = [ErrorDecision.ABORT]
    out = run(env)
    assert out.startswith("Task aborted") and "why" in out
    assert len(env.script.calls) == 1


def test_replan_then_success(env):
    env.plans.append({"steps": [S(1), S(2, "open_app")]})
    env.replans.append({"steps": [S(1, "browser_control")]})
    env.script = Script(open_app=[RuntimeError("nope")])
    env.decisions = [ErrorDecision.REPLAN]
    out = run(env)
    assert out == "Summary sentence, sir."
    goal, done, failed, err = env.replan_args
    assert [s["step"] for s in done] == [1]
    assert failed["tool"] == "open_app" and err == "nope"


def test_gives_up_after_max_replans(env):
    env.plans.append({"steps": [S(1)]})
    env.replans.extend([{"steps": [S(1)]}, {"steps": [S(1)]}])
    env.script = Script(web_search=[RuntimeError("x")] * 10)
    env.decisions = [ErrorDecision.REPLAN] * 10
    out = run(env)
    assert out == f"Task failed after {ex.AgentExecutor.MAX_REPLAN_ATTEMPTS} replan attempts, sir."


def test_cancel_before_start(env):
    env.plans.append({"steps": [S(1)]})
    flag = threading.Event()
    flag.set()
    assert run(env, cancel=flag) == "Task cancelled."
    assert env.script.calls == []


def test_cancel_during_retry_does_not_trigger_replan(env):
    """Cancelling while a step is retrying should stop the task, not burn a
    Gemini replan call first."""
    flag = threading.Event()
    env.plans.append({"steps": [S(1)]})
    env.replans.append({"steps": [S(1)]})

    def failing(tool, params, speak):
        flag.set()
        raise RuntimeError("slow")

    env.script = failing
    env.decisions = [ErrorDecision.RETRY]
    out = run(env, cancel=flag)
    assert out == "Task cancelled."
    assert not hasattr(env, "replan_args")


def test_summary_failure_still_returns_text(env):
    """If the summary call to Gemini fails, the fallback text is spoken and returned."""
    env.plans.append({"steps": [S(1)]})
    env.genai.default = RuntimeError("quota")
    env.genai.replies.clear()

    out = run(env, goal="open notes")
    assert env.spoken[-1].startswith("All done, sir.")
    assert out is not None and out.startswith("All done, sir.")


# ── _inject_context ─────────────────────────────────────────────────────────
def test_inject_context_no_results_is_identity():
    p = {"action": "write", "content": ""}
    assert ex._inject_context(p, "file_controller", {}, "g") is p


def test_inject_context_fills_empty_file_content(monkeypatch):
    monkeypatch.setattr(ex, "_translate_to_goal_language", lambda c, g: f"T[{c}]")
    p = {"action": "write", "content": "short"}
    out = ex._inject_context(p, "file_controller", {1: "A" * 150, 2: "Done.", 3: "B" * 150}, "g")
    assert out["content"] == "T[" + "A" * 150 + "\n\n---\n\n" + "B" * 150 + "]"
    assert p["content"] == "short"  # original not mutated


def test_inject_context_keeps_real_content(monkeypatch):
    monkeypatch.setattr(ex, "_translate_to_goal_language", lambda c, g: "X")
    p = {"action": "write", "content": "c" * 60}
    assert ex._inject_context(p, "file_controller", {1: "A" * 200}, "g")["content"] == "c" * 60


def test_inject_context_ignores_other_tools(monkeypatch):
    monkeypatch.setattr(ex, "_translate_to_goal_language", lambda c, g: "X")
    assert "content" not in ex._inject_context({"q": 1}, "web_search", {1: "A" * 200}, "g")


def test_inject_context_tolerates_non_string_results(monkeypatch):
    monkeypatch.setattr(ex, "_translate_to_goal_language", lambda c, g: c)
    out = ex._inject_context({"action": "write"}, "file_controller", {1: None, 2: "Z" * 120}, "g")
    assert out["content"] == "Z" * 120


# ── translation helpers ─────────────────────────────────────────────────────
def test_translate_empty_goal_is_noop(fake_genai):
    assert ex._translate_to_goal_language("abc", "") == "abc"
    assert fake_genai.prompts == []


def test_translate_failure_returns_original(fake_genai, api_key_file):
    fake_genai.reply("Uzbek", RuntimeError("down"))
    assert ex._translate_to_goal_language("hello", "salom dunyo") == "hello"


def test_detect_language_failure_defaults_english(fake_genai, api_key_file):
    fake_genai.reply(RuntimeError("down"))
    assert ex._detect_language("salom") == "English"


# ── _call_tool routing ──────────────────────────────────────────────────────
def test_generated_code_requires_description():
    with pytest.raises(ValueError):
        ex._call_tool("generated_code", {}, None)


def test_unknown_tool_does_not_execute_llm_generated_code(monkeypatch):
    """SECURITY: an unknown tool name coming from the LLM planner is turned into
    'ask Gemini to write Python and run it with subprocess'. A hallucinated or
    prompt-injected tool name therefore leads to arbitrary code execution on the
    simulation-centre PC. Unknown tools should be rejected instead."""
    ran = []
    monkeypatch.setattr(ex, "_run_generated_code", lambda d, speak=None: ran.append(d) or "ran")
    try:
        ex._call_tool("rm_rf_everything", {"path": "C:/"}, None)
    except Exception:
        pass
    assert ran == []


def test_run_generated_code_executes_and_cleans_up(fake_genai, api_key_file, tmp_path, monkeypatch):
    fake_genai.reply("```python\nprint('generated ok')\n```")
    created = []
    real_ntf = ex.tempfile.NamedTemporaryFile

    def spy(*a, **kw):
        f = real_ntf(*a, dir=tmp_path, **kw)
        created.append(f.name)
        return f

    monkeypatch.setattr(ex.tempfile, "NamedTemporaryFile", spy)
    assert ex._run_generated_code("say hi") == "generated ok"
    assert created and not any(__import__("os").path.exists(p) for p in created)


def test_run_generated_code_error_is_raised(fake_genai, api_key_file):
    fake_genai.reply("raise SystemExit('bad things')")
    with pytest.raises(RuntimeError, match="Code error"):
        ex._run_generated_code("fail")
