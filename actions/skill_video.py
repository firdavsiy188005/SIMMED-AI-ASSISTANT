# -*- coding: utf-8 -*-
"""
konikma_video — Amaliy ko'nikma videosini FAQAT tekshirilgan rasmiy manbalardan ko'rsatish.

XAVFSIZLIK TAMOYILI (VAZIFA 5):
  - Video FAQAT data/amaliy_video_manbalar.json dagi tekshirilgan manbalardan olinadi:
      1) direct_url — qo'lda tekshirilgan aniq video (eng ustuvor),
      2) verified_channels ichida CHEKLANGAN qidiruv (channelId bilan) — ADTI kanali,
         WHO, Osmosis, Geeky Medics kabi tan olingan tibbiy ta'lim kanallari.
  - Tasodifiy / tekshirilmagan YouTube natijasi HECH QACHON ishlatilmaydi.
  - Topilmasa: "topilmadi" xabari qaytadi — model og'zaki tushuntirishga o'tadi.

UI: mavjud QWebEngineView player (youtube_video bilan bir xil) orqali ochiladi.
"""
import logging
import json
import re
import threading
import urllib.parse
import urllib.request
from pathlib import Path

# Mavjud youtube_video helperlarini qayta ishlatamiz (kalit + kanal handle keshi)
from actions.youtube_video import _load_yt_api_key, _fetch_channel_videos


def _base_dir() -> Path:
    return Path(__file__).resolve().parent.parent


_DB_PATH = _base_dir() / "data" / "amaliy_video_manbalar.json"
_DB_CACHE = {"mtime": None, "data": None}
_CHANNEL_ID_CACHE = {}  # handle -> channelId (bir marta resolve qilinadi)


def _load_db() -> dict:
    try:
        mt = _DB_PATH.stat().st_mtime
    except Exception:
        mt = None
    if _DB_CACHE["data"] is None or mt != _DB_CACHE["mtime"]:
        try:
            _DB_CACHE["data"] = json.loads(_DB_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[SKILL-VIDEO] baza o'qishda xato: {e}")
            _DB_CACHE["data"] = {"verified_channels": [], "skills": []}
        _DB_CACHE["mtime"] = mt
    return _DB_CACHE["data"]


def _match_skill(query: str) -> dict | None:
    """So'rovni baza ko'nikmalariga moslashtirish (kalit so'z kesishmasi)."""
    db = _load_db()
    q = (query or "").lower()
    if not q.strip():
        return None
    best, best_score = None, 0.0
    for sk in db.get("skills", []):
        score = 0.0
        for k in sk.get("keys", []):
            k = k.lower()
            if k and k in q:
                score += 2.0 + len(k) / 20.0  # uzunroq moslik = kuchliroq
        # sarlavha so'zlari ham qisman hisoblanadi
        title_words = set(re.findall(r"\w{4,}", sk.get("title", "").lower()))
        q_words = set(re.findall(r"\w{4,}", q))
        score += 0.5 * len(title_words & q_words)
        if score > best_score:
            best, best_score = sk, score
    return best if best_score >= 1.5 else None


def _resolve_channel_id(handle: str, api_key: str) -> str | None:
    """@handle -> channelId (UC...). Keshlanadi. API'siz None."""
    if not handle:
        return None
    if handle in _CHANNEL_ID_CACHE:
        return _CHANNEL_ID_CACHE[handle]
    h = handle.lstrip("@")
    try:
        url = ("https://www.googleapis.com/youtube/v3/channels"
               f"?part=id&forHandle={urllib.parse.quote(h)}&key={api_key}")
        with urllib.request.urlopen(url, timeout=6) as r:
            items = json.loads(r.read()).get("items", [])
        cid = items[0]["id"] if items else None
        _CHANNEL_ID_CACHE[handle] = cid
        return cid
    except Exception as e:
        print(f"[SKILL-VIDEO] channel resolve xato ({handle}): {e}")
        _CHANNEL_ID_CACHE[handle] = None
        return None


def _search_in_channel(channel_id: str, terms: str, api_key: str) -> tuple[str | None, str | None]:
    """FAQAT berilgan channelId ichida embed'lanadigan video qidirish."""
    try:
        params = urllib.parse.urlencode({
            "part": "snippet", "channelId": channel_id, "q": terms,
            "type": "video", "videoEmbeddable": "true",
            "maxResults": "1", "key": api_key, "safeSearch": "moderate",
        })
        req = urllib.request.Request(
            f"https://www.googleapis.com/youtube/v3/search?{params}",
            headers={"User-Agent": "SIMMED/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            items = json.loads(r.read()).get("items", [])
        if items:
            return items[0]["id"]["videoId"], items[0]["snippet"]["title"]
    except Exception as e:
        print(f"[SKILL-VIDEO] channel qidiruv xato: {e}")
    return None, None


def _find_verified_video(skill: dict) -> tuple[str | None, str | None]:
    """Tekshirilgan videoni topish: direct_url → ADTI kanal keshi → verified channel qidiruv."""
    # 1) Qo'lda tekshirilgan aniq URL — eng ustuvor
    durl = (skill.get("direct_url") or "").strip()
    if durl:
        m = re.search(r"(?:v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})", durl)
        if m:
            return m.group(1), skill.get("title", "")

    api_key = _load_yt_api_key()

    # 2) ADTI o'z kanal keshidan (youtube_video moduli keshlagan videolar) — so'z kesishmasi
    try:
        vids = _fetch_channel_videos()
        title_words = set(re.findall(r"\w{4,}", skill.get("title", "").lower()))
        best, best_sc = None, 0.0
        for v in vids:
            tw = set(re.findall(r"\w{4,}", v["title"].lower()))
            sc = len(title_words & tw)
            if sc > best_sc:
                best, best_sc = v, sc
        if best and best_sc >= 2:
            return best["id"], best["title"]
    except Exception:
        logging.getLogger(__name__).warning("Jim o'tilgan xato", exc_info=True)

    if not api_key:
        return None, None

    # 3) Verified channellar ichida cheklangan qidiruv (priority tartibida)
    db = _load_db()
    channels = sorted(db.get("verified_channels", []),
                      key=lambda c: c.get("priority", 9))
    terms = skill.get("search_terms") or skill.get("title", "")
    for ch in channels:
        cid = _resolve_channel_id(ch.get("handle", ""), api_key)
        if not cid:
            continue
        vid, title = _search_in_channel(cid, terms, api_key)
        if vid:
            print(f"[SKILL-VIDEO] topildi ({ch.get('name')}): {title[:50]}")
            return vid, f"{title}  ·  {ch.get('name')}"
    return None, None


def konikma_video(parameters: dict, response=None, player=None):
    """
    Amaliy ko'nikma videosini tekshirilgan manbadan ko'rsatadi.
    parameters: {"skill": "<ko'nikma nomi>"}
    """
    parameters = parameters or {}
    query = (parameters.get("skill") or parameters.get("query")
             or parameters.get("konikma") or "").strip()
    if not query:
        return "Qaysi amaliy ko'nikma videosini ko'rsatishimni ayting."

    skill = _match_skill(query)
    if not skill:
        return (f"'{query}' bo'yicha tekshirilgan rasmiy video bazada topilmadi. "
                "Tasodifiy video ko'rsatmayman — buning o'rniga bu ko'nikmani og'zaki, "
                "bosqichma-bosqich tushuntirib beraman.")

    def _load():
        vid_id, title = _find_verified_video(skill)
        if vid_id and player and hasattr(player, "show_video"):
            player.show_video(f"https://www.youtube.com/watch?v={vid_id}")
            if hasattr(player, "_win") and hasattr(player._win, "_vid_title_lbl"):
                try:
                    from PyQt6.QtCore import QTimer
                    safe = (title or skill.get("title", ""))[:70]
                    QTimer.singleShot(300, lambda t=safe:
                                      player._win._vid_title_lbl.setText(f"▶  {t}"))
                except Exception:
                    logging.getLogger(__name__).warning("Jim o'tilgan xato", exc_info=True)
        else:
            print(f"[SKILL-VIDEO] tekshirilgan video topilmadi: '{query}'")

    threading.Thread(target=_load, daemon=True).start()
    return (f"'{skill.get('title', query)}' ko'nikmasi bo'yicha tekshirilgan video "
            "rasmiy manbadan qidirilmoqda. Topilsa ko'rsataman; topilmasa og'zaki tushuntiraman.")


if __name__ == "__main__":
    for q in ["cpr ko'rsat", "intubatsiya videosi", "qon bosim o'lchash", "notanish narsa"]:
        print("===", q, "===")
        sk = _match_skill(q)
        print("  match:", sk.get("title") if sk else None)
