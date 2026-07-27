# -*- coding: utf-8 -*-
"""
amaliy_konikma — Amaliy ko'nikmalar bilim bazasidan qidirish.

MEDIKUS/MEDIKA talabaga amaliy ko'nikmani (qadama-qadam) o'rgatishi uchun
data/amaliy_konikmalar.json dan mos ko'nikmani topib qaytaradi.
Hujjatlar asosan kirill — lotin so'rov kirillga ham o'giriladi (qidiruv aniqligi uchun).
"""

import os
import sys
import json
import re
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_KB_PATH = _base_dir() / "data" / "amaliy_konikmalar.json"
_KB = None
_KB_MTIME = None

# Lotin (o'zbek) -> kirill (taxminiy, qidiruv uchun)
_TR = [
    ("o'", "ў"), ("oʻ", "ў"), ("g'", "ғ"), ("gʻ", "ғ"),
    ("sh", "ш"), ("ch", "ч"), ("yo", "ё"), ("yu", "ю"), ("ya", "я"), ("ts", "ц"),
    ("a", "а"), ("b", "б"), ("d", "д"), ("e", "е"), ("f", "ф"), ("g", "г"),
    ("h", "ҳ"), ("i", "и"), ("j", "ж"), ("k", "к"), ("l", "л"), ("m", "м"),
    ("n", "н"), ("o", "о"), ("p", "п"), ("q", "қ"), ("r", "р"), ("s", "с"),
    ("t", "т"), ("u", "у"), ("v", "в"), ("x", "х"), ("y", "й"), ("z", "з"),
]


def _to_cyr(s: str) -> str:
    s = s.lower()
    for lat, cyr in _TR:
        s = s.replace(lat, cyr)
    return s


def _load_kb():
    global _KB, _KB_MTIME
    try:
        mt = _KB_PATH.stat().st_mtime
    except Exception:
        mt = None
    # Fayl o'zgargan bo'lsa (yangi fayl yuklangan) — qayta o'qish
    if _KB is None or mt != _KB_MTIME:
        try:
            with open(_KB_PATH, "r", encoding="utf-8") as f:
                _KB = json.load(f)
        except Exception:
            _KB = []
        _KB_MTIME = mt
    return _KB


def _tokens(q: str):
    """So'rovdan kalit so'zlar (lotin + kirill variant)."""
    q = (q or "").lower()
    raw = re.findall(r"[\w'ʻ]{3,}", q)
    cyr = re.findall(r"[\w'ʻ]{3,}", _to_cyr(q))
    return set(raw) | set(cyr)


def amaliy_konikma(parameters=None, response=None, player=None):
    parameters = parameters or {}
    query = parameters.get("query", "") or parameters.get("nomi", "")
    fak = (parameters.get("fakultet", "") or "").lower()
    kb = _load_kb()
    if not kb:
        return "Amaliy ko'nikmalar bazasi topilmadi (data/amaliy_konikmalar.json)."
    toks = _tokens(query)
    if not toks:
        return "Qaysi amaliy ko'nikma kerakligini aniqroq ayting."

    scored = []
    for e in kb:
        nomi = (e.get("nomi", "")).lower()
        kaf = (e.get("kafedra", "")).lower()
        fayl = (e.get("fayl", "")).lower()
        matn = (e.get("matn", "")).lower()
        score = 0
        for t in toks:
            if t in nomi:
                score += 6
            if t in kaf or t in fayl:
                score += 3
            if t in matn:
                score += 1
        if fak and fak in (e.get("fakultet", "").lower() + " " + kaf):
            score += 2
        if score:
            scored.append((score, e))
    if not scored:
        return ("Bu mavzu bo'yicha amaliy ko'nikma topilmadi. Boshqa nom bilan urinib ko'ring "
                "yoki kafedra/fan nomini ayting.")
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[0][1]
    out = [
        f"[Fakultet: {top.get('fakultet','-')} | Kafedra: {top.get('kafedra','-') or '-'}]",
        top.get("matn", "")[:3500],
    ]
    # Qo'shimcha topilgan ko'nikmalar (tanlash uchun)
    others = []
    seen = {top.get("nomi", "")}
    for _, e in scored[1:6]:
        nm = e.get("nomi", "").strip()
        if nm and nm not in seen:
            seen.add(nm)
            others.append(f"• {nm[:90]}")
    if others:
        out.append("\nTegishli boshqa ko'nikmalar:\n" + "\n".join(others))
    return "\n".join(out)


if __name__ == "__main__":
    for q in ["yurak", "ekg", "kateterizatsiya", "intubatsiya", "oftalmoskopiya"]:
        print("=== ", q, " ===")
        print(amaliy_konikma({"query": q})[:300])
        print()
