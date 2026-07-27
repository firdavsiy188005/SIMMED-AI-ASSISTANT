# -*- coding: utf-8 -*-
"""
VIRTUAL KASALXONA — AI BEMOR dvigateli.

Talaba uchun interaktiv tashxis treningi:
  1. yangi_bemor()   — AI yashirin tashxisli virtual bemor yaratadi
  2. bemor_javob()   — bemor rolida talaba savoliga javob beradi
  3. tahlil_natija() — buyurilgan tahlilning real ko'rsatkichlarini beradi
  4. baholash()      — talaba tashxisini xolis baholaydi (0-100 ball)

Barcha chaqiruvlar kalit-zaxira bilan himoyalangan (limit tugasa keyingi
kalitga o'tadi) — trening hech qachon uzilmaydi.
"""

import json
import re
from pathlib import Path

_KEY_IDX = [0]

_QUOTA_SIGNS = ("resource_exhausted", "quota", "rate limit", "429", "exceeded",
                "permission_denied", "api key not valid", "api_key_invalid",
                "401", "403", "unauthenticated")

_MODEL = "gemini-2.5-flash"


def _load_api_keys() -> list:
    cfg_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    keys = []
    for k in ["gemini_api_key", "gemini_api_key_2", "gemini_api_key_3",
              "gemini_api_key_4", "gemini_api_key_5"]:
        v = (cfg.get(k) or "").strip()
        if v and v not in keys:
            keys.append(v)
    if not keys:
        raise RuntimeError("Gemini kalit topilmadi (config/api_keys.json)")
    return keys


def _generate(contents, system: str = None, temperature: float = 0.3,
              json_mode: bool = False) -> str:
    """Gemini chaqiruvi — kalit-zaxira bilan (self-healing)."""
    from google import genai
    from google.genai import types
    keys = _load_api_keys()
    last_err = None
    for _ in range(len(keys)):
        try:
            client = genai.Client(api_key=keys[_KEY_IDX[0] % len(keys)])
            cfg = types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=4000,
                response_mime_type="application/json" if json_mode else None,
                # Tez javob: ichki "o'ylash"ni o'chiramiz — token tejaladi,
                # javob kesilmaydi
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            )
            resp = client.models.generate_content(
                model=_MODEL, contents=contents, config=cfg)
            return (resp.text or "").strip()
        except Exception as e:
            last_err = e
            low = str(e).lower()
            if len(keys) > 1 and any(s in low for s in _QUOTA_SIGNS):
                _KEY_IDX[0] = (_KEY_IDX[0] + 1) % len(keys)
                print(f"[VIRTUAL-BEMOR] 🔑 zaxira kalit #{_KEY_IDX[0] + 1}")
                continue
            raise
    raise last_err


def _parse_json(text: str) -> dict:
    """AI javobidan JSON ajratib olish (```json ... ``` bo'lsa ham).
    strict=False — matn ichidagi qator-boshi belgilariga chidamli."""
    t = text.strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if m:
        t = m.group(0)
    return json.loads(t, strict=False)


# ─────────────────────────────────────────────────────────────────────────────
# MARKAZ BILIM BAZASI (270+ amaliy ko'nikma) — bemorlar SHU manbadan yaratiladi.
# Admin panel orqali yangi ko'nikma qo'shilsa, fayl har safar yangidan o'qiladi
# va avtomatik ishlatiladi.
# ─────────────────────────────────────────────────────────────────────────────

_KB_FILE = Path(__file__).resolve().parent.parent / "data" / "amaliy_konikmalar.json"

# Yo'nalish -> bilim bazasida qidiriladigan kalit so'zlar (uz/ru)
_YONALISH_KALITLAR = {
    "Terapiya":               ["terapiya", "терапия", "umumiy amaliyot", "вроп", "воп"],
    "Kardiologiya":           ["kardio", "кардио", "yurak", "сердеч", "ekg", "экг"],
    "Pulmonologiya":          ["pulmon", "пульмон", "o'pka", "легк", "nafas", "дыхат"],
    "Gastroenterologiya":     ["gastro", "гастро", "oshqozon", "желуд", "ich", "кишеч"],
    "Nevrologiya":            ["nevro", "невро", "nerv", "insul", "инсульт"],
    "Endokrinologiya":        ["endokrin", "эндокрин", "diabet", "диабет", "qandli"],
    "Pediatriya":             ["pediatr", "педиатр", "bola", "дет", "neonat", "неонат"],
    "Xirurgiya":              ["xirurg", "хирург", "jarroh", "yara", "ран", "travma", "травм"],
    "Akusherlik-ginekologiya": ["akusher", "акушер", "ginek", "гинек", "tug'ruq", "род", "homila"],
    "Yuqumli kasalliklar":    ["yuqumli", "инфекц", "infek"],
    "Reanimatologiya":        ["reanimat", "реанимат", "anestez", "анестез", "shoshilinch",
                               "скорая", "slr", "слр", "cpr"],
}


def _load_kb() -> list:
    """Markaz bilim bazasini o'qish (har safar yangidan — admin qo'shgani darhol ishlaydi)."""
    try:
        d = json.loads(_KB_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def kb_manba(yonalish: str) -> dict | None:
    """Yo'nalishga mos TASODIFIY ko'nikma-manbani tanlaydi: {'nomi','matn'} yoki None."""
    import random
    kb = _load_kb()
    if not kb:
        return None
    kalitlar = _YONALISH_KALITLAR.get(yonalish, [yonalish.lower()])
    mos = []
    for el in kb:
        if not isinstance(el, dict):
            continue
        blob = " ".join(str(el.get(k, "")) for k in ("nomi", "matn", "fayl",
                                                     "kafedra", "fakultet")).lower()
        if any(k in blob for k in kalitlar):
            mos.append(el)
    tanlov = mos or kb   # mos topilmasa — butun bazadan
    el = random.choice(tanlov)
    matn = str(el.get("matn", ""))[:2500]
    nomi = str(el.get("nomi", "Amaliy ko'nikma"))
    return {"nomi": nomi, "matn": matn} if matn.strip() else None


# ─────────────────────────────────────────────────────────────────────────────
# 1. BEMOR YARATISH
# ─────────────────────────────────────────────────────────────────────────────

def yangi_bemor(yonalish: str, qiyinlik: str = "O'rta") -> dict:
    """Yangi virtual bemor — ADTI markazining RASMIY o'quv materiali asosida."""
    manba = kb_manba(yonalish)
    manba_blok = ""
    if manba:
        manba_blok = (
            "\nMUHIM — HOLAT MANBASI: quyida ADTI O'quv Simulyatsiya va Attestatsiya "
            "Markazining RASMIY O'QUV MATERIALI berilgan. Bemor holati (kasalligi, "
            "klinikasi) AYNAN SHU MATERIALDA yoritilgan mavzu/kasallik/ko'nikmaga mos "
            "bo'lishi SHART — talaba shu material bo'yicha sinaladi:\n"
            f"--- MATERIAL: {manba['nomi']} ---\n{manba['matn']}\n--- MATERIAL TUGADI ---\n"
        )
    prompt = (
        f"Tibbiyot talabasi uchun {qiyinlik.upper()} darajadagi o'quv klinik holat yarat. "
        f"Yo'nalish: {yonalish}. O'zbekiston sharoitiga mos, real hayotiy bemor bo'lsin."
        f"{manba_blok}\n"
        "FAQAT quyidagi JSON formatda javob ber:\n"
        "{\n"
        '  "ism": "Ism Familiya (o\'zbekcha)",\n'
        '  "yosh": 45,\n'
        '  "jins": "erkak yoki ayol",\n'
        '  "kasb": "kasbi",\n'
        '  "shikoyat": "Bemorning O\'Z TILIDA (oddiy, tibbiy atamasiz) boshlang\'ich '
        'shikoyati — 2-3 jumla, birinchi shaxsda",\n'
        '  "yashirin_tashxis": "Aniq klinik tashxis (talabaga KO\'RSATILMAYDI)",\n'
        '  "yashirin_klinika": "Kasallikning to\'liq klinik manzarasi: anamnez '
        'tafsilotlari, ob\'ektiv belgilar, tahlil o\'zgarishlari — bemor javoblari '
        'va tahlil natijalari SHU manzaraga qat\'iy mos bo\'lishi kerak"\n'
        "}"
    )
    raw = _generate(prompt, temperature=0.9, json_mode=True)
    case = _parse_json(raw)
    case["yonalish"] = yonalish
    case["qiyinlik"] = qiyinlik
    if manba:
        case["manba_nomi"] = manba["nomi"]
        case["manba_matn"] = manba["matn"][:1500]
    return case


# ─────────────────────────────────────────────────────────────────────────────
# 2. BEMOR JAVOBI (rol o'ynash)
# ─────────────────────────────────────────────────────────────────────────────

def bemor_javob(case: dict, suhbat: list, savol: str) -> str:
    """Bemor rolida talaba savoliga javob (suhbat — [(kim, matn), ...])."""
    system = (
        f"Sen VIRTUAL BEMORsan — rol o'ynayapsan. Sening ma'lumotlaring:\n"
        f"Ism: {case.get('ism')}, yosh: {case.get('yosh')}, jins: {case.get('jins')}, "
        f"kasb: {case.get('kasb')}.\n"
        f"KASALLIGING (sir!): {case.get('yashirin_tashxis')}\n"
        f"KLINIK MANZARA: {case.get('yashirin_klinika')}\n\n"
        "QOIDALAR:\n"
        "1. ODDIY ODAM tilida gapir — tibbiy atama ishlatma, kasalligingning nomini BILMAYSAN.\n"
        "2. Javoblaring klinik manzaraga QAT'IY mos bo'lsin — shifokor (talaba) so'ragan "
        "belgini manzarada bo'lsa tasvirlab ber, bo'lmasa 'yo'q' de.\n"
        "3. Qisqa javob ber (1-3 jumla), birinchi shaxsda, tabiiy ohangda.\n"
        "4. Tashxisni O'ZING AYTMA, talabaga yordam berma — sen shunchaki bemorsan.\n"
        "5. Talaba qo'pol bo'lsa ham xotirjam javob ber.\n"
        "6. Talaba tilida javob ber (o'zbek/rus/ingliz)."
    )
    tarix = "\n".join(f"{'Shifokor' if k == 'talaba' else 'Bemor'}: {m}"
                      for k, m in suhbat[-12:])
    prompt = f"SUHBAT TARIXI:\n{tarix}\n\nShifokor: {savol}\n\nBemor javobi:"
    javob = _generate(prompt, system=system, temperature=0.6)
    # Model ba'zan "Bemor:" prefiksini qo'shadi — tozalaymiz
    return re.sub(r"^(Bemor:\s*)+", "", javob).strip()


# ─────────────────────────────────────────────────────────────────────────────
# 3. TAHLIL NATIJASI
# ─────────────────────────────────────────────────────────────────────────────

TAHLILLAR = [
    "Umumiy ko'rik (AB, puls, harorat, nafas)",
    "Qon umumiy tahlili (KAK)",
    "Siydik umumiy tahlili",
    "Biokimyoviy tahlil",
    "EKG",
    "Rentgen (ko'krak qafasi)",
    "UTT (qorin bo'shlig'i)",
    "Qon guruhida glyukoza",
]


def tahlil_natija(case: dict, tahlil: str) -> str:
    """Buyurilgan tahlilning real, kasallikka mos natijasi."""
    system = (
        f"Sen klinik laboratoriya/diagnostika tizimisan. Bemor: {case.get('ism')}, "
        f"{case.get('yosh')} yosh, {case.get('jins')}.\n"
        f"HAQIQIY TASHXIS (sir): {case.get('yashirin_tashxis')}\n"
        f"KLINIK MANZARA: {case.get('yashirin_klinika')}\n\n"
        "Berilgan tekshiruv natijasini REAL raqamli ko'rsatkichlar bilan yoz — "
        "kasallik manzarasiga QAT'IY mos: patologik ko'rsatkichlar kasallikka xos "
        "o'zgargan, qolganlari normada. Norma oralig'ini qavsda ko'rsat. "
        "TASHXIS NOMINI YOZMA — faqat xulosasiz natijalar. Qisqa, jadvalsimon matn."
    )
    return _generate(f"Tekshiruv: {tahlil}", system=system, temperature=0.2)


# ─────────────────────────────────────────────────────────────────────────────
# 4. BAHOLASH
# ─────────────────────────────────────────────────────────────────────────────

def baholash(case: dict, suhbat: list, tahlillar: list,
             tashxis: str, davolash: str) -> dict:
    """Talaba ishini xolis baholash: {ball, togri_tashxis, izoh, ...}."""
    tarix = "\n".join(f"{'Talaba' if k == 'talaba' else 'Bemor'}: {m}"
                      for k, m in suhbat)
    system = (
        "Sen ADTI attestatsiya komissiyasining tajribali imtihonchi-shifokorisan. "
        "Talabaning virtual bemor bilan ishlashini XOLIS baholaysan. "
        "FAQAT JSON formatda javob ber."
    )
    manba_blok = ""
    if case.get("manba_matn"):
        manba_blok = (
            f"MARKAZ RASMIY O'QUV MATERIALI ({case.get('manba_nomi', '')}) — "
            f"baholashda SHU standartga tayan:\n{case['manba_matn']}\n\n"
        )
    prompt = (
        f"{manba_blok}"
        f"HAQIQIY TASHXIS: {case.get('yashirin_tashxis')}\n"
        f"KLINIK MANZARA: {case.get('yashirin_klinika')}\n\n"
        f"TALABA SUHBATI (anamnez yig'ishi):\n{tarix or '(savol bermagan)'}\n\n"
        f"BUYURILGAN TAHLILLAR: {', '.join(tahlillar) or '(tahlil buyurmagan)'}\n\n"
        f"TALABA TASHXISI: {tashxis}\n"
        f"TALABA DAVOLASH REJASI: {davolash or '(yozilmagan)'}\n\n"
        "Quyidagi JSON formatda baholab ber:\n"
        "{\n"
        '  "ball": 0-100 orasida butun son (tashxis to\'g\'riligi 50%, anamnez '
        "sifati 20%, tahlil tanlovi 15%, davolash 15%),\n"
        '  "togri_tashxis": "haqiqiy tashxis nomi",\n'
        '  "tashxis_togri": true/false (talaba tashxisi mos keldimi, sinonim/yaqin '
        "bo'lsa ham true),\n"
        '  "yaxshi_tomonlar": "nimalarni to\'g\'ri qildi (2-3 band)",\n'
        '  "xatolar": "nimani o\'tkazib yubordi yoki xato qildi (2-3 band)",\n'
        '  "tavsiya": "keyingi safar uchun 1-2 aniq tavsiya"\n'
        "}"
    )
    raw = _generate(prompt, system=system, temperature=0.1, json_mode=True)
    return _parse_json(raw)


# ─────────────────────────────────────────────────────────────────────────────
# 5. NATIJALARNI SAQLASH
# ─────────────────────────────────────────────────────────────────────────────

_NATIJA_FILE = Path(__file__).resolve().parent.parent / "data" / "virtual_kasalxona.json"


def natija_saqla(talaba: str, case: dict, natija: dict):
    """Trening natijasini jurnalga yozish (trener ko'rishi uchun)."""
    from datetime import datetime
    try:
        rows = json.loads(_NATIJA_FILE.read_text(encoding="utf-8"))
    except Exception:
        rows = []
    rows.append({
        "sana": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "talaba": talaba or "Noma'lum",
        "yonalish": case.get("yonalish", ""),
        "qiyinlik": case.get("qiyinlik", ""),
        "kasallik": natija.get("togri_tashxis", case.get("yashirin_tashxis", "")),
        "manba": case.get("manba_nomi", ""),
        "ball": natija.get("ball", 0),
        "tashxis_togri": bool(natija.get("tashxis_togri")),
    })
    _NATIJA_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                            encoding="utf-8")


def natijalar() -> list:
    try:
        return json.loads(_NATIJA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
