# -*- coding: utf-8 -*-
"""
talaba_natijalar — ovozli tool: "Medikus, natijalarimni ko'rsat".

Talaba rivojlanish panelini [F12] ochadi va MEDIKUS/MEDIKA ovozda o'qib
beradigan qisqa xulosa matnini qaytaradi. Ma'lumot features/talaba_panel.py
dagi tayyor (testlangan) funksiyalardan olinadi.
"""
import logging

log = logging.getLogger(__name__)

# Sessiya talabasi — "Men Firdavsiyman" deganda eslab qolinadi (dastur yopilguncha).
# Modul darajasida: ovoz sessiyasi qayta ulansa ham (persona almashsa) saqlanadi.
_SESSIYA = {"talaba": ""}


def _moslashtir(soralgan: str, fio_lar: list) -> str | None:
    """Aytilgan ismni ro'yxatdagi to'liq F.I.Sh.ga moslashtiradi.
    Ovozda odatda faqat ism/familiya aytiladi — qisman moslik ham qabul qilinadi."""
    if not fio_lar:
        return None
    s = (soralgan or "").strip().lower()
    if not s:
        return fio_lar[0] if len(fio_lar) == 1 else None
    for f in fio_lar:                      # aniq moslik
        if f.lower() == s:
            return f
    for f in fio_lar:                      # barcha aytilgan so'zlar ism ichida
        fl = f.lower()
        if all(q in fl for q in s.split()):
            return f
    return None


def _xulosa(fio: str, st: dict) -> str:
    """Ovozda o'qishga qulay qisqa xulosa."""
    if not st["soni"]:
        return (f"{fio} bo'yicha hali natija yo'q. Test topshirish yoki "
                "Virtual kasalxonada mashq qilishni tavsiya qilaman.")
    qism = [
        f"{fio} bo'yicha jami {st['soni']} ta sinov, o'rtacha ball {st['ortacha']}.",
        f"Tendensiya: {st['tendensiya'].split()[0]}.",
    ]
    if st["zaif"]:
        qism.append("Zaif mavzular: " + "; ".join(st["zaif"][:3]) + ".")
    else:
        qism.append("Zaif mavzu aniqlanmadi — barakalla.")
    if st["tavsiyalar"]:
        qism.append(st["tavsiyalar"][0])
    return " ".join(qism)


def sessiya_talaba(parameters: dict, response=None, player=None) -> str:
    """Tool: foydalanuvchi o'zini tanishtirganda ("Men Firdavsiyman") chaqiriladi —
    sessiya talabasini eslab qoladi, keyin "natijalarim" ismisiz ishlaydi."""
    from features.talaba_panel import malumot_yukla

    soralgan = str((parameters or {}).get("talaba") or "").strip()
    if not soralgan:
        return "Ismingizni eshitolmadim — iltimos, qaytaring."

    _, _, fio_lar = malumot_yukla()
    fio = _moslashtir(soralgan, fio_lar)

    _SESSIYA["talaba"] = fio or soralgan
    if player is not None:
        try:
            player.write_log(f"👤 Sessiya talabasi: {_SESSIYA['talaba']}")
        except Exception:
            log.warning("Sessiya talabasini logga yozib bo'lmadi", exc_info=True)

    ism = (fio or soralgan).split()[0]
    if fio:
        return (f"Tanishganimdan xursandman, {ism}! Endi 'natijalarimni ko'rsat' "
                "desangiz, aynan sizning rivojlanishingizni ko'rsataman.")
    return (f"Xush kelibsiz, {ism}! Siz hali natijalar ro'yxatida yo'q ekansiz — "
            "birinchi test yoki virtual bemor treningidan so'ng natijalaringiz "
            "shu nom bilan yig'ila boshlaydi.")


def talaba_natijalar(parameters: dict, response=None, player=None) -> str:
    """Tool kirish nuqtasi (SIMMED konvensiyasi)."""
    from features.talaba_panel import (malumot_yukla, birlashtir,
                                       talaba_statistika)

    soralgan = str((parameters or {}).get("talaba") or "").strip()
    if not soralgan and _SESSIYA["talaba"]:
        soralgan = _SESSIYA["talaba"]          # "natijalarim" — sessiya talabasi
    results, vk, fio_lar = malumot_yukla()

    if not fio_lar:
        return ("Hozircha birorta ham talaba natijasi yo'q. Avval test topshirish "
                "yoki Virtual kasalxonada mashq qilish kerak.")

    fio = _moslashtir(soralgan, fio_lar)
    if fio is None:
        royxat = ", ".join(fio_lar[:5])
        if soralgan:
            return (f"'{soralgan}' ismli talaba topilmadi. "
                    f"Ro'yxatdagilar: {royxat}. Qaysi birini ko'rsatay?")
        return f"Qaysi talabani ko'rsatay? Ro'yxatda: {royxat}."

    st = talaba_statistika(fio, birlashtir(results, vk))

    # Panelni UI oqimida ochish (signal orqali — thread-xavfsiz)
    if player is not None:
        try:
            player.open_talaba_panel(fio)
            player.write_log(f"📊 Rivojlanish paneli ochildi: {fio}")
        except Exception:
            log.warning("Panelni ochib bo'lmadi", exc_info=True)

    return _xulosa(fio, st)
