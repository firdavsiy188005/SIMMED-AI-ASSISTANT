# -*- coding: utf-8 -*-
"""Skill-video matching, shipped data files, and whole-project integrity."""
import ast
import json
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# actions/skill_video.py  (needs actions.youtube_video, which is not in the repo)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def sv(monkeypatch):
    yt = types.ModuleType("actions.youtube_video")
    yt.api_key = "YTKEY"
    yt.channel_videos = []
    yt._load_yt_api_key = lambda: yt.api_key
    yt._fetch_channel_videos = lambda: list(yt.channel_videos)
    monkeypatch.setitem(sys.modules, "actions.youtube_video", yt)
    monkeypatch.delitem(sys.modules, "actions.skill_video", raising=False)
    import actions.skill_video as mod
    mod._DB_CACHE.update({"mtime": None, "data": None})
    mod._CHANNEL_ID_CACHE.clear()
    mod.yt = yt
    yield mod
    sys.modules.pop("actions.skill_video", None)


@pytest.mark.parametrize("query,title_part", [
    ("cpr ko'rsat", "CPR"),
    ("yurak massaji videosi", "CPR"),
    ("pediatric cpr", "Pediatric"),
    ("детская реанимация", "Pediatric"),
])
def test_match_skill_real_db(sv, query, title_part):
    sk = sv._match_skill(query)
    assert sk is not None and title_part in sk["title"]


@pytest.mark.parametrize("query", ["", "   ", "kosmik kema", "notanish narsa"])
def test_match_skill_no_match(sv, query):
    assert sv._match_skill(query) is None


def test_more_specific_key_wins(sv):
    """'bolalar cpr' should pick Pediatric CPR, not adult CPR."""
    assert "Pediatric" in sv._match_skill("bolalar cpr")["title"]


def test_direct_url_has_priority(sv):
    for url in ["https://www.youtube.com/watch?v=abcdefghijk",
                "https://youtu.be/abcdefghijk", "https://www.youtube.com/embed/abcdefghijk?x=1"]:
        assert sv._find_verified_video({"direct_url": url, "title": "T"}) == ("abcdefghijk", "T")


def test_adti_channel_cache_used_before_api(sv, monkeypatch):
    sv.yt.channel_videos = [{"id": "VID00000001", "title": "Kattalarda yurak reanimatsiyasi texnikasi"}]
    monkeypatch.setattr(sv, "_resolve_channel_id", lambda *a: pytest.fail("API should not be hit"))
    vid, _ = sv._find_verified_video({"title": "Kattalarda yurak reanimatsiyasi"})
    assert vid == "VID00000001"


def test_no_api_key_returns_none(sv):
    sv.yt.api_key = ""
    assert sv._find_verified_video({"title": "x"}) == (None, None)


def test_only_verified_channels_searched_in_priority_order(sv, monkeypatch):
    searched = []
    monkeypatch.setattr(sv, "_resolve_channel_id", lambda h, k: "UC_" + h)
    def fake_search(cid, terms, key):
        searched.append(cid)
        return (None, None) if len(searched) < 2 else ("VID", "Found")
    monkeypatch.setattr(sv, "_search_in_channel", fake_search)
    vid, title = sv._find_verified_video({"title": "x", "search_terms": "adult cpr"})
    assert vid == "VID"
    db = json.loads((ROOT / "data" / "amaliy_video_manbalar.json").read_text(encoding="utf-8"))
    handles = {"UC_" + c["handle"] for c in db["verified_channels"]}
    assert set(searched) <= handles
    assert searched[0] == "UC_@ADTISIMULYATSIYAMARKAZI"  # priority 1 first


def test_network_errors_are_swallowed(sv, monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(sv.urllib.request, "urlopen", boom)
    assert sv._resolve_channel_id("@x", "k") is None
    assert sv._search_in_channel("UC", "t", "k") == (None, None)


def test_konikma_video_entrypoint(sv, monkeypatch):
    assert "ayting" in sv.konikma_video({})
    assert "topilmadi" in sv.konikma_video({"skill": "kosmik kema"})
    started = []
    monkeypatch.setattr(sv.threading, "Thread", lambda target, daemon: types.SimpleNamespace(
        start=lambda: started.append(target)))
    msg = sv.konikma_video({"query": "cpr"})
    assert "qidirilmoqda" in msg and len(started) == 1


def test_corrupt_db_falls_back(sv, tmp_path, monkeypatch):
    bad = tmp_path / "db.json"
    bad.write_text("{oops", encoding="utf-8")
    monkeypatch.setattr(sv, "_DB_PATH", bad)
    assert sv._load_db() == {"verified_channels": [], "skills": []}


# ─────────────────────────────────────────────────────────────────────────────
# data/ files
# ─────────────────────────────────────────────────────────────────────────────
def test_video_db_schema():
    db = json.loads((ROOT / "data" / "amaliy_video_manbalar.json").read_text(encoding="utf-8"))
    assert db["verified_channels"] and db["skills"]
    for ch in db["verified_channels"]:
        assert ch["handle"].startswith("@") and ch["name"] and isinstance(ch.get("priority", 9), int)
    titles = set()
    for sk in db["skills"]:
        assert sk["title"] and sk["title"] not in titles, sk["title"]
        titles.add(sk["title"])
        assert sk["keys"] and all(k.strip() == k and k for k in sk["keys"]), sk["title"]
        url = sk.get("direct_url", "")
        assert not url or re.search(r"(?:v=|youtu\.be/|embed/)[A-Za-z0-9_-]{11}", url), url


def test_video_db_keys_do_not_collide():
    """The same key on two skills makes the match depend on file order."""
    db = json.loads((ROOT / "data" / "amaliy_video_manbalar.json").read_text(encoding="utf-8"))
    seen = {}
    dupes = []
    for sk in db["skills"]:
        for k in sk["keys"]:
            k = k.lower()
            if k in seen and seen[k] != sk["title"]:
                dupes.append((k, seen[k], sk["title"]))
            seen[k] = sk["title"]
    assert dupes == []


def test_skills_sample_schema():
    rows = json.loads((ROOT / "data" / "amaliy_konikmalar.sample.json").read_text(encoding="utf-8"))
    skills = [r for r in rows if "nomi" in r]
    assert skills
    for r in skills:
        for f in ("fakultet", "kafedra", "fayl", "nomi", "matn"):
            assert isinstance(r.get(f), str) and r[f].strip(), (r.get("nomi"), f)


def test_prompt_file_present_and_utf8():
    text = (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")
    assert len(text) > 100


# ─────────────────────────────────────────────────────────────────────────────
# Project integrity: every local import must resolve to a file in the repo
# ─────────────────────────────────────────────────────────────────────────────
LOCAL_PACKAGES = {"actions", "agent", "memory", "core", "config", "features", "ui"}


def _local_imports():
    found = {}
    for py in ROOT.rglob("*.py"):
        if "tests" in py.parts or ".git" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mod = node.module
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in LOCAL_PACKAGES:
                        found.setdefault(a.name, set()).add(py.relative_to(ROOT).as_posix())
            if mod and mod.split(".")[0] in LOCAL_PACKAGES:
                found.setdefault(mod, set()).add(py.relative_to(ROOT).as_posix())
    return found


def test_all_local_imports_exist():
    missing = {}
    for mod, users in sorted(_local_imports().items()):
        rel = Path(*mod.split("."))
        if not ((ROOT / rel).with_suffix(".py").exists() or (ROOT / rel / "__init__.py").exists()):
            missing[mod] = sorted(users)
    assert missing == {}, "Modules imported but not in the repo:\n" + "\n".join(
        f"  {m}  <- {', '.join(u)}" for m, u in missing.items())


def test_requirements_parse():
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8-sig").splitlines()
    pkgs = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
    assert len(pkgs) == len(set(p.lower() for p in pkgs)), "duplicate requirement"
    assert "google-genai" in pkgs and "google-generativeai" in pkgs


def test_no_secrets_committed():
    pattern = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
    hits = []
    for p in ROOT.rglob("*"):
        if p.is_file() and ".git" not in p.parts and p.suffix in {".py", ".json", ".txt", ".md"}:
            if pattern.search(p.read_text(encoding="utf-8", errors="ignore")):
                hits.append(p.name)
    assert hits == []
