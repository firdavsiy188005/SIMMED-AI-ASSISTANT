# -*- coding: utf-8 -*-
import json
import threading

import pytest

import memory.memory_manager as mm


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    path = tmp_path / "memory" / "long_term.json"
    monkeypatch.setattr(mm, "MEMORY_PATH", path)
    return path


CATEGORIES = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}


# ── load ────────────────────────────────────────────────────────────────────
def test_load_missing_file_returns_empty_skeleton():
    assert mm.load_memory() == {c: {} for c in CATEGORIES}


def test_load_corrupt_json_returns_empty(isolated_memory):
    isolated_memory.parent.mkdir(parents=True)
    isolated_memory.write_text("{not json", encoding="utf-8")
    assert mm.load_memory() == {c: {} for c in CATEGORIES}


@pytest.mark.parametrize("payload", ["[]", '"text"', "42", "null"])
def test_load_non_dict_json_returns_empty(isolated_memory, payload):
    isolated_memory.parent.mkdir(parents=True)
    isolated_memory.write_text(payload, encoding="utf-8")
    assert mm.load_memory() == {c: {} for c in CATEGORIES}


def test_load_fills_missing_categories(isolated_memory):
    isolated_memory.parent.mkdir(parents=True)
    isolated_memory.write_text(json.dumps({"identity": {"name": {"value": "Ali"}}}), encoding="utf-8")
    data = mm.load_memory()
    assert set(data) == CATEGORIES
    assert data["identity"]["name"]["value"] == "Ali"


# ── remember / forget roundtrip ─────────────────────────────────────────────
def test_remember_then_load():
    msg = mm.remember("name", "Firdavs", category="identity")
    assert "identity/name" in msg
    assert mm.load_memory()["identity"]["name"]["value"] == "Firdavs"


def test_remember_invalid_category_falls_back_to_notes():
    mm.remember("x", "y", category="hacker")
    data = mm.load_memory()
    assert "hacker" not in data
    assert data["notes"]["x"]["value"] == "y"


def test_unicode_is_preserved_on_disk(isolated_memory):
    mm.remember("city", "Андижон — Oʻzbekiston", category="identity")
    raw = isolated_memory.read_text(encoding="utf-8")
    assert "Андижон" in raw  # ensure_ascii=False
    assert mm.load_memory()["identity"]["city"]["value"] == "Андижон — Oʻzbekiston"


def test_forget_existing_and_missing():
    mm.remember("k", "v")
    assert mm.forget("k") == "Forgotten: notes/k"
    assert "k" not in mm.load_memory()["notes"]
    assert mm.forget("k") == "Not found: notes/k"
    assert mm.forget("k", category="no_such_cat").startswith("Not found")


def test_forget_memory_alias():
    assert mm.forget_memory is mm.forget


# ── update_memory semantics ─────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [None, {}, [], "str", 5])
def test_update_memory_ignores_invalid_input(bad, isolated_memory):
    result = mm.update_memory(bad)
    assert set(result) == CATEGORIES
    assert not isolated_memory.exists()


def test_update_skips_none_and_blank_values(isolated_memory):
    mm.update_memory({"notes": {"a": None, "b": "   ", "c": ""}})
    assert not isolated_memory.exists()  # nothing changed → nothing written


def test_update_unchanged_value_does_not_rewrite(monkeypatch):
    mm.remember("a", "1")
    saves = []
    monkeypatch.setattr(mm, "save_memory", lambda m: saves.append(m))
    mm.remember("a", "1")
    assert saves == []


def test_long_value_is_truncated():
    mm.remember("bio", "x" * 1000)
    val = mm.load_memory()["notes"]["bio"]["value"]
    assert len(val) == mm.MAX_VALUE_LENGTH + 1
    assert val.endswith("…")


def test_non_string_values_are_stringified():
    mm.update_memory({"identity": {"age": 21, "student": True}})
    ident = mm.load_memory()["identity"]
    assert ident["age"]["value"] == "21"
    assert ident["student"]["value"] == "True"


def test_explicit_none_value_inside_entry_is_not_stored_as_text():
    """{"value": None} must not be saved as the literal string "None"."""
    mm.update_memory({"notes": {"k": {"value": None}}})
    entry = mm.load_memory()["notes"].get("k")
    assert entry is None or entry["value"] != "None"


# ── trimming ────────────────────────────────────────────────────────────────
def test_memory_file_never_exceeds_limit(isolated_memory):
    for i in range(60):
        mm.remember(f"key_{i:02d}", "v" * 100)
    size = len(json.dumps(json.loads(isolated_memory.read_text(encoding="utf-8")), ensure_ascii=False))
    assert size <= mm.MEMORY_MAX_CHARS


def test_trim_removes_oldest_first():
    mem = mm._empty_memory()
    mem["notes"]["old"] = {"value": "o" * 900, "updated": "2020-01-01"}
    mem["notes"]["new"] = {"value": "n" * 900, "updated": "2030-01-01"}
    mem["notes"]["mid"] = {"value": "m" * 900, "updated": "2025-01-01"}
    out = mm._trim_to_limit(mem)
    assert "old" not in out["notes"]
    assert "new" in out["notes"]


def test_trim_handles_entries_nested_deeper_than_two_levels():
    """_recursive_update accepts nested dicts (identity → family → mother), but
    _trim_to_limit only sees depth-2 entries, so deep data can never be trimmed
    and the file grows past MEMORY_MAX_CHARS."""
    for i in range(40):
        mm.update_memory({"relationships": {"family": {f"member_{i}": "z" * 100}}})
    size = len(json.dumps(mm.load_memory(), ensure_ascii=False))
    assert size <= mm.MEMORY_MAX_CHARS


# ── thread safety ───────────────────────────────────────────────────────────
def test_concurrent_writes_do_not_corrupt_file(isolated_memory):
    errors = []

    def worker(n):
        try:
            for j in range(10):
                mm.remember(f"t{n}_{j}", "v")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    json.loads(isolated_memory.read_text(encoding="utf-8"))  # still valid JSON


# ── prompt formatting ───────────────────────────────────────────────────────
def test_format_empty():
    assert mm.format_memory_for_prompt(None) == ""
    assert mm.format_memory_for_prompt({}) == ""
    assert mm.format_memory_for_prompt(mm._empty_memory()) == ""


def test_format_orders_identity_fields_and_sections():
    mem = mm._empty_memory()
    mem["identity"] = {"city": {"value": "Andijon"}, "name": {"value": "Ali"}, "fav_color": {"value": "blue"}}
    mem["preferences"] = {"voice": {"value": "Medika"}}
    mem["notes"] = {"exam_date": {"value": "June"}}
    out = mm.format_memory_for_prompt(mem)
    assert out.index("Name: Ali") < out.index("City: Andijon") < out.index("Fav Color: blue")
    assert "Preferences:" in out and "Voice: Medika" in out
    assert "exam_date: June" in out
    assert out.endswith("\n")


def test_format_is_capped_at_2000_chars():
    mem = mm._empty_memory()
    for sec in ("preferences", "projects", "relationships", "wishes", "notes"):
        mem[sec] = {f"k{i}": {"value": "x" * 300} for i in range(15)}
    out = mm.format_memory_for_prompt(mem)
    assert len(out) <= 2001


def test_format_tolerates_plain_string_entries():
    mem = mm._empty_memory()
    mem["identity"] = {"name": "Ali"}
    assert "Name: Ali" in mm.format_memory_for_prompt(mem)
