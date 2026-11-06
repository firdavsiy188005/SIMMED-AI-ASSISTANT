# -*- coding: utf-8 -*-
import json

import pytest

import agent.planner as planner
import agent.error_handler as eh
from agent.error_handler import ErrorDecision


def plan_json(*steps, wrap=False):
    text = json.dumps({"goal": "g", "steps": list(steps)})
    return f"```json\n{text}\n```" if wrap else text


def step(n=1, tool="web_search", **extra):
    s = {"step": n, "tool": tool, "description": f"d{n}", "parameters": {"query": "q"}, "critical": True}
    s.update(extra)
    return s


# ─────────────────────────────────────────────────────────────────────────────
# planner.create_plan
# ─────────────────────────────────────────────────────────────────────────────
def test_plan_happy_path(fake_genai, api_key_file):
    fake_genai.reply(plan_json(step(1), step(2, "file_controller")))
    plan = planner.create_plan("research AI")
    assert [s["tool"] for s in plan["steps"]] == ["web_search", "file_controller"]
    assert fake_genai.configured_keys == ["test-key-1234567890"]
    assert fake_genai.models[-1].system_instruction == planner.PLANNER_PROMPT


def test_plan_strips_markdown_fences(fake_genai, api_key_file):
    fake_genai.reply(plan_json(step(1), wrap=True))
    assert len(planner.create_plan("x")["steps"]) == 1


def test_plan_context_is_passed(fake_genai, api_key_file):
    fake_genai.reply(plan_json(step(1)))
    planner.create_plan("goal", context="ctx-123")
    assert "ctx-123" in fake_genai.prompts[-1]


def test_plan_replaces_generated_code(fake_genai, api_key_file):
    fake_genai.reply(plan_json(step(1, "generated_code", description="y" * 500)))
    s = planner.create_plan("x")["steps"][0]
    assert s["tool"] == "web_search"
    assert len(s["parameters"]["query"]) == 200


@pytest.mark.parametrize("reply", [
    "not json at all",
    json.dumps({"goal": "g"}),
    json.dumps({"steps": "nope"}),
    json.dumps([1, 2, 3]),
    RuntimeError("network down"),
])
def test_plan_falls_back_on_bad_output(fake_genai, api_key_file, reply):
    fake_genai.reply(reply)
    plan = planner.create_plan("my goal")
    assert plan["steps"][0]["tool"] == "web_search"
    assert plan["steps"][0]["parameters"]["query"] == "my goal"


def test_plan_missing_api_key_file_falls_back(fake_genai, tmp_path, monkeypatch):
    """No config/api_keys.json → create_plan raises instead of falling back,
    because _get_api_key() is called outside the try block."""
    monkeypatch.setattr(planner, "API_CONFIG_PATH", tmp_path / "missing.json")
    plan = planner.create_plan("goal")
    assert plan["steps"][0]["tool"] == "web_search"


def test_plan_with_step_missing_description_is_kept(fake_genai, api_key_file):
    """A valid plan whose step lacks 'description' is thrown away: the debug
    print uses s['description'] and the KeyError lands in the generic except,
    which returns the fallback plan."""
    fake_genai.reply(json.dumps({"steps": [{"step": 1, "tool": "open_app",
                                           "parameters": {"app_name": "Word"}}]}))
    plan = planner.create_plan("open word")
    assert plan["steps"][0]["tool"] == "open_app"


def test_replan_happy_path(fake_genai, api_key_file):
    fake_genai.reply(plan_json(step(1, "browser_control"), wrap=True))
    plan = planner.replan("g", [step(1)], step(2, "open_app"), "err")
    assert plan["steps"][0]["tool"] == "browser_control"
    assert "Step 1 (web_search): DONE" in fake_genai.prompts[-1]


def test_replan_no_completed_steps(fake_genai, api_key_file):
    fake_genai.reply(plan_json(step(1)))
    planner.replan("g", [], {"tool": "x"}, "err")
    assert "(none)" in fake_genai.prompts[-1]


def test_replan_failure_falls_back(fake_genai, api_key_file):
    fake_genai.reply("garbage")
    assert planner.replan("g", [], {}, "e")["steps"][0]["tool"] == "web_search"


def test_replan_sanitises_generated_code(fake_genai, api_key_file):
    fake_genai.reply(plan_json(step(1, "generated_code")))
    assert planner.replan("g", [], {}, "e")["steps"][0]["tool"] == "web_search"


def test_planner_prompt_lists_every_tool_the_executor_knows():
    import agent.executor as ex
    import inspect
    src = inspect.getsource(ex._call_tool)
    for tool in ["open_app", "web_search", "browser_control", "file_controller", "code_helper",
                 "dev_agent", "screen_process", "send_message", "reminder", "youtube_video",
                 "weather_report", "computer_settings", "desktop_control", "computer_control",
                 "flight_finder", "game_updater"]:
        assert f'"{tool}"' in src
        assert f"\n{tool}\n" in planner.PLANNER_PROMPT, tool


# ─────────────────────────────────────────────────────────────────────────────
# error_handler.analyze_error
# ─────────────────────────────────────────────────────────────────────────────
def verdict(decision, **kw):
    d = {"decision": decision, "reason": "r", "fix_suggestion": "f", "max_retries": 1, "user_message": "m"}
    d.update(kw)
    return json.dumps(d)


@pytest.mark.parametrize("word,expected", [
    ("retry", ErrorDecision.RETRY), ("SKIP", ErrorDecision.SKIP),
    ("replan", ErrorDecision.REPLAN), ("Abort", ErrorDecision.ABORT),
    ("dance", ErrorDecision.REPLAN),
])
def test_analyze_maps_decisions(fake_genai, api_key_file, word, expected):
    fake_genai.reply(verdict(word))
    assert eh.analyze_error(step(1, critical=False), "boom")["decision"] is expected


def test_analyze_critical_step_cannot_be_skipped(fake_genai, api_key_file):
    fake_genai.reply(verdict("skip"))
    r = eh.analyze_error(step(1, critical=True), "boom")
    assert r["decision"] is ErrorDecision.REPLAN


def test_analyze_max_attempts_short_circuits_without_api(fake_genai, api_key_file):
    r = eh.analyze_error(step(1), "x" * 300, attempt=2, max_attempts=2)
    assert r["decision"] is ErrorDecision.REPLAN
    assert fake_genai.prompts == []
    assert len(r["reason"]) < 130


@pytest.mark.parametrize("reply", ["not json", "[]", json.dumps({"decision": None}), RuntimeError("x")])
def test_analyze_bad_model_output_defaults_to_replan(fake_genai, api_key_file, reply):
    fake_genai.reply(reply)
    assert eh.analyze_error(step(1), "boom")["decision"] is ErrorDecision.REPLAN


def test_analyze_truncates_long_error_in_prompt(fake_genai, api_key_file):
    fake_genai.reply(verdict("retry"))
    eh.analyze_error(step(1), "E" * 5000)
    assert fake_genai.prompts[-1].count("E") <= 520


def test_generate_fix_builds_code_helper_step(fake_genai, api_key_file):
    fake_genai.reply("```python\nprint('hi')\n```")
    s = eh.generate_fix(step(3, depends_on=[1, 2]), "err", "use another way")
    assert s["tool"] == "code_helper"
    assert s["parameters"]["code"] == "print('hi')"
    assert s["step"] == 3 and s["depends_on"] == [1, 2]


def test_generate_fix_falls_back(fake_genai, api_key_file):
    fake_genai.reply(RuntimeError("down"))
    s = eh.generate_fix(step(3), "err", "x")
    assert s["tool"] == "generated_code"
