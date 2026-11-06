# -*- coding: utf-8 -*-
import json
import logging
import sys
import threading

import pytest

import memory.config_manager as cm
import simmed_logging


# ─────────────────────────────────────────────────────────────────────────────
# config_manager
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def cfg(tmp_path, monkeypatch):
    d = tmp_path / "config"
    monkeypatch.setattr(cm, "CONFIG_DIR", d)
    monkeypatch.setattr(cm, "CONFIG_FILE", d / "api_keys.json")
    return d / "api_keys.json"


def test_no_config(cfg):
    assert not cm.config_exists()
    assert cm.load_api_keys() == {}
    assert cm.get_gemini_key() is None
    assert cm.is_configured() is False


def test_save_creates_dir_and_strips(cfg):
    cm.save_api_keys("   AIzaSyTESTKEY1234567890  \n")
    assert cm.config_exists()
    assert cm.get_gemini_key() == "AIzaSyTESTKEY1234567890"
    assert cm.is_configured()


def test_save_preserves_other_keys(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"gemini_api_key_2": "backup", "youtube_api_key": "yt"}), encoding="utf-8")
    cm.save_api_keys("new-key-123456789012")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data == {"gemini_api_key_2": "backup", "youtube_api_key": "yt",
                    "gemini_api_key": "new-key-123456789012"}


def test_save_over_corrupt_file(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{{{", encoding="utf-8")
    cm.save_api_keys("k" * 20)
    assert cm.get_gemini_key() == "k" * 20


@pytest.mark.parametrize("key,ok", [("", False), ("short", False), ("x" * 15, False), ("x" * 16, True)])
def test_is_configured_length_rule(cfg, key, ok):
    cm.save_api_keys(key)
    assert cm.is_configured() is ok


def test_load_corrupt_returns_empty(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text("not json", encoding="utf-8")
    assert cm.load_api_keys() == {}


def test_load_config_saved_with_bom(cfg):
    """Windows Notepad saves 'UTF-8' with a BOM. The app runs on Windows and
    users are told to edit config/api_keys.json by hand, so a BOM must not make
    the key silently disappear."""
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"gemini_api_key": "AIzaSyTESTKEY1234567890"}), encoding="utf-8-sig")
    assert cm.get_gemini_key() == "AIzaSyTESTKEY1234567890"


@pytest.mark.parametrize("payload", ["[]", '"str"', "null"])
def test_non_object_config_does_not_crash(cfg, payload):
    cfg.parent.mkdir(parents=True)
    cfg.write_text(payload, encoding="utf-8")
    assert cm.get_gemini_key() is None
    assert cm.is_configured() is False


def test_example_config_is_valid_template():
    from pathlib import Path
    ex = Path(cm.__file__).resolve().parent.parent / "config" / "api_keys.example.json"
    data = json.loads(ex.read_text(encoding="utf-8-sig"))
    assert "gemini_api_key" in data


# ─────────────────────────────────────────────────────────────────────────────
# simmed_logging
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def clean_root(tmp_path, monkeypatch):
    root = logging.getLogger()
    saved = (root.handlers[:], root.level, getattr(root, "_simmed_configured", None),
             sys.excepthook, threading.excepthook)
    root.handlers = []
    if hasattr(root, "_simmed_configured"):
        del root._simmed_configured
    monkeypatch.setattr(simmed_logging, "_log_dir", lambda: tmp_path)
    yield root, tmp_path
    for h in root.handlers:
        h.close()
    root.handlers, root.level = saved[0], saved[1]
    if saved[2] is None and hasattr(root, "_simmed_configured"):
        del root._simmed_configured
    sys.excepthook, threading.excepthook = saved[3], saved[4]


def test_setup_logging_writes_file(clean_root):
    root, d = clean_root
    simmed_logging.setup_logging()
    logging.getLogger("x").info("hello SIMMED")
    for h in root.handlers:
        h.flush()
    text = (d / simmed_logging.LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "hello SIMMED" in text


def _own(root):
    """Handlers added by SIMMED (ignore pytest's LogCaptureHandler)."""
    return [h for h in root.handlers if type(h).__name__ != "LogCaptureHandler"]


def test_setup_logging_idempotent(clean_root):
    root, _ = clean_root
    simmed_logging.setup_logging()
    n = len(_own(root))
    simmed_logging.setup_logging()
    assert len(_own(root)) == n == 2


def test_uncaught_thread_exception_is_logged(clean_root):
    root, d = clean_root
    simmed_logging.setup_logging()

    def boom():
        raise ValueError("thread kaboom")

    t = threading.Thread(target=boom, name="T1")
    t.start()
    t.join()
    for h in root.handlers:
        h.flush()
    text = (d / simmed_logging.LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "thread kaboom" in text and "T1" in text


def test_excepthook_logs_but_lets_keyboardinterrupt_through(clean_root, monkeypatch):
    root, d = clean_root
    simmed_logging.setup_logging()
    called = []
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: called.append(a[0]))
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert called == [KeyboardInterrupt]
    try:
        raise RuntimeError("main kaboom")
    except RuntimeError as e:
        sys.excepthook(type(e), e, e.__traceback__)
    for h in root.handlers:
        h.flush()
    assert "main kaboom" in (d / simmed_logging.LOG_FILE_NAME).read_text(encoding="utf-8")


def test_unwritable_log_dir_does_not_crash(clean_root, monkeypatch):
    root, _ = clean_root

    def bad():
        raise OSError("read-only")

    monkeypatch.setattr(simmed_logging, "_log_dir", bad)
    simmed_logging.setup_logging()  # must not raise
    assert len(_own(root)) == 1  # console only
