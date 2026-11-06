# -*- coding: utf-8 -*-
"""Shared pytest fixtures for SIMMED.

The real app needs Windows, a microphone, PyQt6 and Gemini API keys. These
fixtures replace the external pieces with in-memory fakes so the logic can be
tested on any machine (including CI on Linux):

* ``fake_genai``   – stands in for ``google.generativeai`` (planner, executor,
                     error handler). Queue replies with ``fake_genai.reply(...)``.
* ``fake_new_genai`` – stands in for ``google.genai`` (virtual patient engine).
* Every module that writes to disk is redirected into ``tmp_path``.
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# google.generativeai fake
# ─────────────────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, text):
        self.text = text


class FakeGenAI(types.ModuleType):
    """Minimal ``google.generativeai`` replacement.

    ``reply(x)`` queues a response: a string becomes ``.text``; an Exception
    instance is raised from ``generate_content``. When the queue is empty
    ``default`` is used.
    """

    def __init__(self):
        super().__init__("google.generativeai")
        self.replies = []
        self.default = "OK"
        self.prompts = []
        self.models = []
        self.configured_keys = []
        outer = self

        class GenerativeModel:
            def __init__(self, model_name=None, system_instruction=None, **kw):
                self.model_name = model_name
                self.system_instruction = system_instruction
                outer.models.append(self)

            def generate_content(self, prompt, *a, **kw):
                outer.prompts.append(prompt)
                r = outer.replies.pop(0) if outer.replies else outer.default
                if isinstance(r, BaseException):
                    raise r
                return _Resp(r)

        self.GenerativeModel = GenerativeModel

    def configure(self, api_key=None, **kw):
        self.configured_keys.append(api_key)

    def reply(self, *items):
        self.replies.extend(items)


@pytest.fixture
def fake_genai(monkeypatch):
    fake = FakeGenAI()
    google_pkg = sys.modules.get("google") or types.ModuleType("google")
    if not hasattr(google_pkg, "__path__"):
        google_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake)
    monkeypatch.setattr(google_pkg, "generativeai", fake, raising=False)
    return fake


# ─────────────────────────────────────────────────────────────────────────────
# google.genai fake (new SDK, used by actions/virtual_bemor.py)
# ─────────────────────────────────────────────────────────────────────────────
class FakeNewGenAI:
    def __init__(self):
        self.replies = []
        self.default = "OK"
        self.calls = []  # (api_key, model, contents, config)
        outer = self

        class _Models:
            def __init__(self, key):
                self.key = key

            def generate_content(self, model=None, contents=None, config=None):
                outer.calls.append((self.key, model, contents, config))
                r = outer.replies.pop(0) if outer.replies else outer.default
                if isinstance(r, BaseException):
                    raise r
                return _Resp(r)

        class Client:
            def __init__(self, api_key=None):
                self.models = _Models(api_key)

        class _Cfg:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        self.genai = types.ModuleType("google.genai")
        self.genai.Client = Client
        self.types = types.ModuleType("google.genai.types")
        self.types.GenerateContentConfig = _Cfg
        self.types.ThinkingConfig = _Cfg
        self.genai.types = self.types

    def reply(self, *items):
        self.replies.extend(items)


@pytest.fixture
def fake_new_genai(monkeypatch):
    fake = FakeNewGenAI()
    google_pkg = sys.modules.get("google") or types.ModuleType("google")
    if not hasattr(google_pkg, "__path__"):
        google_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", fake.genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake.types)
    monkeypatch.setattr(google_pkg, "genai", fake.genai, raising=False)
    return fake


# ─────────────────────────────────────────────────────────────────────────────
# API key file used by planner / executor / error handler
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def api_key_file(tmp_path, monkeypatch):
    import json
    import agent.planner as planner
    import agent.error_handler as eh
    import agent.executor as ex

    p = tmp_path / "api_keys.json"
    p.write_text(json.dumps({"gemini_api_key": "test-key-1234567890"}), encoding="utf-8")
    for mod in (planner, eh, ex):
        monkeypatch.setattr(mod, "API_CONFIG_PATH", p)
    return p


# ─────────────────────────────────────────────────────────────────────────────
# KNOWN BUGS LEDGER
# Each test below reproduces a real defect found while testing. They are marked
# xfail(strict=True): the suite stays green today, and as soon as a bug is
# fixed its test XPASSes → the run FAILS, reminding you to delete the entry.
# ─────────────────────────────────────────────────────────────────────────────
KNOWN_BUGS = {
    # project integrity
    "test_skill_video_and_data.py::test_all_local_imports_exist":
        "20 actions.* modules, features.talaba_panel and ui are imported but not in the repo",
    # config / memory
    "test_config_and_logging.py::test_load_config_saved_with_bom":
        "api_keys.json saved by Windows Notepad (UTF-8 BOM) is silently ignored",
    "test_config_and_logging.py::test_non_object_config_does_not_crash":
        "get_gemini_key() crashes if api_keys.json is a list/string/null",
    "test_memory_manager.py::test_explicit_none_value_inside_entry_is_not_stored_as_text":
        '{"value": None} is stored as the string "None"',
    "test_memory_manager.py::test_trim_handles_entries_nested_deeper_than_two_levels":
        "nested memory entries are never trimmed; file grows past MEMORY_MAX_CHARS",
    # agent
    "test_task_queue.py::test_cancelled_tasks_are_removed_from_internal_queue":
        "cancelled pending tasks are never removed from TaskQueue._queue (leak)",
    "test_planner_and_error_handler.py::test_plan_missing_api_key_file_falls_back":
        "create_plan raises FileNotFoundError instead of using the fallback plan",
    "test_planner_and_error_handler.py::test_plan_with_step_missing_description_is_kept":
        "valid plan discarded when a step has no 'description' (KeyError in debug print)",
    "test_executor.py::test_cancel_during_retry_does_not_trigger_replan":
        "cancelling during a retry still triggers a Gemini replan call",
    "test_executor.py::test_unknown_tool_does_not_execute_llm_generated_code":
        "SECURITY: unknown tool name → LLM-written Python is executed via subprocess",
    # actions
    "test_actions.py::test_fakultet_filter_is_respected":
        "fakultet filter (+2) weaker than filename match (+3): paediatrics gets adult CPR",
    "test_actions.py::test_null_fields_in_kb_do_not_crash":
        "amaliy_konikma crashes on null fields in the knowledge base",
    "test_actions.py::test_non_dict_rows_in_kb_do_not_crash":
        "amaliy_konikma crashes on non-dict rows in the knowledge base",
    "test_actions.py::test_moslashtir_prefers_whole_word_match":
        "'Ali' matches 'Aliyev Alisher' first → student sees someone else's results",
    "test_actions.py::test_xulosa_empty_trend_does_not_crash":
        "_xulosa IndexError when tendensiya is empty",
    "test_actions.py::test_natija_saqla_does_not_destroy_corrupt_journal":
        "corrupt virtual_kasalxona.json → all previous student results are overwritten",
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        key = f"{item.path.name}::{item.originalname}"
        reason = KNOWN_BUGS.get(key)
        if reason:
            item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
