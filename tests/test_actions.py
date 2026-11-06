# -*- coding: utf-8 -*-
"""Tests for the clinical-skill actions that ship in this repository."""
import json
import sys
import types

import pytest

import actions.amaliy_konikma as ak
import actions.talaba_natijalar as tn
import actions.virtual_bemor as vb


# ─────────────────────────────────────────────────────────────────────────────
# amaliy_konikma — practical-skills search
# ─────────────────────────────────────────────────────────────────────────────
KB = [
    {"_izoh": "metadata row without skill fields"},
    {"fakultet": "Davolash", "kafedra": "Anesteziologiya-reanimatologiya", "fayl": "reanimatsiya.docx",
     "nomi": "Kattalarda yurak-o'pka reanimatsiyasi (CPR)", "matn": "CPR: 30 kompressiya : 2 nafas. " * 10},
    {"fakultet": "Pediatriya", "kafedra": "Pediatriya", "fayl": "pediatriya.docx",
     "nomi": "Chaqaloqlarda reanimatsiya", "matn": "Chaqaloq CPR 15:2"},
    {"fakultet": "Davolash", "kafedra": "Терапия", "fayl": "ekg.docx",
     "nomi": "ЭКГ ёзиш техникаси", "matn": "Электродларни жойлаштириш: V1 тўш суягининг ўнг қирғоғи..."},
    {"fakultet": "Stomatologiya", "kafedra": "Xirurgik stomatologiya", "fayl": "tish.docx",
     "nomi": "Tish olish", "matn": "Anesteziya, keyin elevator va qisqich."},
]


@pytest.fixture
def kb(tmp_path, monkeypatch):
    path = tmp_path / "amaliy_konikmalar.json"

    def write(data):
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(ak, "_KB", None)
        return path

    monkeypatch.setattr(ak, "_KB_PATH", path)
    monkeypatch.setattr(ak, "_KB", None)
    monkeypatch.setattr(ak, "_KB_MTIME", None)
    write(KB)
    return write


@pytest.mark.parametrize("lat,cyr", [
    ("ekg", "екг"), ("o'pka", "ўпка"), ("shoshilinch", "шошилинч"),
    ("g'alla", "ғалла"), ("yurak", "юрак"), ("choy", "чой"), ("Toshkent", "тошкент"),
])
def test_to_cyr(lat, cyr):
    assert ak._to_cyr(lat) == cyr


def test_tokens_drop_short_words_and_include_cyrillic():
    t = ak._tokens("ekg va yurak")
    assert {"ekg", "екг", "yurak", "юрак"} <= t
    assert "va" not in t
    assert ak._tokens(None) == set()


def test_best_match_by_name(kb):
    out = ak.amaliy_konikma({"query": "yurak-o'pka reanimatsiya"})
    assert out.startswith("[Fakultet: Davolash | Kafedra: Anesteziologiya-reanimatologiya]")
    assert "30 kompressiya" in out
    assert "Chaqaloqlarda reanimatsiya" in out  # listed under related skills


def test_latin_query_finds_cyrillic_document(kb):
    out = ak.amaliy_konikma({"query": "ekg yozish"})
    assert "Электродларни" in out


def test_fakultet_filter_is_respected(kb):
    """Asking for CPR in the paediatrics faculty returns the ADULT CPR guide:
    the faculty bonus (+2) is weaker than a filename hit (+3)."""
    out = ak.amaliy_konikma({"query": "reanimatsiya", "fakultet": "pediatriya"})
    assert out.startswith("[Fakultet: Pediatriya")


def test_nomi_parameter_alias(kb):
    assert "elevator" in ak.amaliy_konikma({"nomi": "tish olish"})


@pytest.mark.parametrize("params", [None, {}, {"query": ""}, {"query": "ab"}])
def test_empty_or_too_short_query(kb, params):
    assert "aniqroq ayting" in ak.amaliy_konikma(params)


def test_no_match(kb):
    assert "topilmadi" in ak.amaliy_konikma({"query": "kosmonavtika raketasi"})


def test_missing_kb_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ak, "_KB_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(ak, "_KB", None)
    assert "bazasi topilmadi" in ak.amaliy_konikma({"query": "ekg"})


def test_kb_reloads_when_file_changes(kb):
    import os
    import time
    assert "topilmadi" in ak.amaliy_konikma({"query": "kateterizatsiya"})
    p = ak._KB_PATH
    p.write_text(json.dumps(KB + [{"nomi": "Siydik kateterizatsiyasi", "matn": "Foley kateter"}]),
                 encoding="utf-8")
    t = time.time() + 5
    os.utime(p, (t, t))
    assert "Foley" in ak.amaliy_konikma({"query": "kateterizatsiya"})


def test_output_is_capped(kb):
    kb([{"nomi": "Uzun ko'nikma", "matn": "x" * 10000}])
    assert len(ak.amaliy_konikma({"query": "uzun"})) < 3700


def test_null_fields_in_kb_do_not_crash(kb):
    """A record exported with null fields (e.g. "kafedra": null) crashes the
    search with AttributeError: 'NoneType' object has no attribute 'lower'."""
    kb([{"fakultet": None, "kafedra": None, "fayl": None, "nomi": "Qon bosimini o'lchash", "matn": "Tonometr"}])
    assert "Tonometr" in ak.amaliy_konikma({"query": "qon bosimini"})


def test_non_dict_rows_in_kb_do_not_crash(kb):
    kb(["stray string", {"nomi": "Qon bosimini o'lchash", "matn": "Tonometr"}])
    assert "Tonometr" in ak.amaliy_konikma({"query": "qon bosimini"})


def test_sample_kb_file_works_with_search(monkeypatch):
    from pathlib import Path
    sample = Path(ak.__file__).resolve().parent.parent / "data" / "amaliy_konikmalar.sample.json"
    monkeypatch.setattr(ak, "_KB_PATH", sample)
    monkeypatch.setattr(ak, "_KB", None)
    assert "kompressiya" in ak.amaliy_konikma({"query": "yurak-o'pka reanimatsiyasi CPR"})


# ─────────────────────────────────────────────────────────────────────────────
# talaba_natijalar — student progress voice tool
# ─────────────────────────────────────────────────────────────────────────────
STUDENTS = ["Aliyev Alisher Anvarovich", "Karimova Dilnoza", "Ali Valiyev"]


@pytest.mark.parametrize("asked,expected", [
    ("karimova dilnoza", "Karimova Dilnoza"),
    ("Dilnoza", "Karimova Dilnoza"),
    ("  KARIMOVA  ", "Karimova Dilnoza"),
    ("Ali Valiyev", "Ali Valiyev"),
    ("Bobur", None),
])
def test_moslashtir(asked, expected):
    assert tn._moslashtir(asked, STUDENTS) == expected


def test_moslashtir_edge_cases():
    assert tn._moslashtir("x", []) is None
    assert tn._moslashtir("", ["Only One"]) == "Only One"
    assert tn._moslashtir("", STUDENTS) is None


def test_moslashtir_prefers_whole_word_match():
    """'Ali' is a substring of 'Aliyev Alisher', which comes first in the list,
    so a student who says 'Men Ali' is shown another student's grades."""
    assert tn._moslashtir("Ali", STUDENTS) == "Ali Valiyev"


def _stats(**kw):
    st = {"soni": 3, "ortacha": 78.5, "tendensiya": "o'smoqda ↑", "zaif": [], "tavsiyalar": []}
    st.update(kw)
    return st


def test_xulosa_variants():
    assert "hali natija yo'q" in tn._xulosa("A", _stats(soni=0))
    out = tn._xulosa("A", _stats(zaif=["EKG", "CPR", "Intubatsiya", "X"], tavsiyalar=["Ko'proq mashq"]))
    assert "3 ta sinov" in out and "78.5" in out and "Tendensiya: o'smoqda." in out
    assert "EKG; CPR; Intubatsiya." in out and "X" not in out.split("Zaif mavzular:")[1].split(".")[0]
    assert out.endswith("Ko'proq mashq")
    assert "barakalla" in tn._xulosa("A", _stats())


def test_xulosa_empty_trend_does_not_crash():
    """If the trend string is empty, .split()[0] raises IndexError."""
    tn._xulosa("A", _stats(tendensiya=""))


@pytest.fixture
def panel(monkeypatch):
    """Stub for features.talaba_panel (that package is not in the repo)."""
    mod = types.ModuleType("features.talaba_panel")
    mod.data = {"results": [], "vk": [], "fio": list(STUDENTS)}
    mod.malumot_yukla = lambda: (mod.data["results"], mod.data["vk"], mod.data["fio"])
    mod.birlashtir = lambda r, v: list(r) + list(v)
    mod.talaba_statistika = lambda fio, rows: _stats()
    pkg = types.ModuleType("features")
    pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "features", pkg)
    monkeypatch.setitem(sys.modules, "features.talaba_panel", mod)
    monkeypatch.setitem(tn._SESSIYA, "talaba", "")
    return mod


class FakePlayer:
    def __init__(self, fail=False):
        self.logs, self.opened, self.fail = [], [], fail

    def write_log(self, m):
        if self.fail:
            raise RuntimeError("ui gone")
        self.logs.append(m)

    def open_talaba_panel(self, fio):
        if self.fail:
            raise RuntimeError("ui gone")
        self.opened.append(fio)


def test_session_student_then_my_results(panel):
    p = FakePlayer()
    msg = tn.sessiya_talaba({"talaba": "Dilnoza"}, player=p)
    assert "Dilnoza" in msg or "Karimova" in msg
    out = tn.talaba_natijalar({}, player=p)
    assert p.opened == ["Karimova Dilnoza"]
    assert "Karimova Dilnoza bo'yicha" in out


def test_session_unknown_student(panel):
    msg = tn.sessiya_talaba({"talaba": "Bobur Toshmatov"})
    assert msg.startswith("Xush kelibsiz, Bobur")
    assert tn._SESSIYA["talaba"] == "Bobur Toshmatov"


def test_session_requires_name(panel):
    assert "eshitolmadim" in tn.sessiya_talaba({})
    assert "eshitolmadim" in tn.sessiya_talaba(None)


def test_results_no_students(panel):
    panel.data["fio"] = []
    assert "birorta ham" in tn.talaba_natijalar({"talaba": "x"})


def test_results_unknown_and_unspecified(panel):
    assert "topilmadi" in tn.talaba_natijalar({"talaba": "Bobur"})
    assert tn.talaba_natijalar({}).startswith("Qaysi talabani")


def test_results_survive_ui_errors(panel):
    out = tn.talaba_natijalar({"talaba": "Dilnoza"}, player=FakePlayer(fail=True))
    assert "Karimova Dilnoza" in out


# ─────────────────────────────────────────────────────────────────────────────
# virtual_bemor — AI virtual patient
# ─────────────────────────────────────────────────────────────────────────────
def _write_keys(tmp_path, monkeypatch, data):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "api_keys.json").write_text(json.dumps(data), encoding="utf-8")
    # _load_api_keys resolves config/ relative to the module's __file__
    monkeypatch.setattr(vb, "__file__", str(tmp_path / "actions" / "virtual_bemor.py"))


def test_load_api_keys_dedup_and_blank(tmp_path, monkeypatch):
    _write_keys(tmp_path, monkeypatch, {"gemini_api_key": "K1", "gemini_api_key_2": " K2 ",
                                        "gemini_api_key_3": "K1", "gemini_api_key_4": " ",
                                        "gemini_api_key_5": None})
    assert vb._load_api_keys() == ["K1", "K2"]


def test_load_api_keys_none_configured(tmp_path, monkeypatch):
    _write_keys(tmp_path, monkeypatch, {"gemini_api_key": ""})
    with pytest.raises(RuntimeError, match="kalit topilmadi"):
        vb._load_api_keys()


@pytest.fixture
def keys(monkeypatch):
    def set_keys(*k):
        monkeypatch.setattr(vb, "_load_api_keys", lambda: list(k))
    set_keys("K1", "K2")
    monkeypatch.setattr(vb, "_KEY_IDX", [0])
    return set_keys


def test_generate_basic(fake_new_genai, keys):
    fake_new_genai.reply("  javob  ")
    assert vb._generate("savol") == "javob"
    key, model, contents, cfg = fake_new_genai.calls[0]
    assert key == "K1" and model == vb._MODEL and contents == "savol"
    assert cfg.response_mime_type is None


def test_generate_json_mode(fake_new_genai, keys):
    fake_new_genai.reply("{}")
    vb._generate("x", json_mode=True)
    assert fake_new_genai.calls[0][3].response_mime_type == "application/json"


def test_generate_rotates_key_on_quota(fake_new_genai, keys):
    fake_new_genai.reply(RuntimeError("429 RESOURCE_EXHAUSTED"), "ok")
    assert vb._generate("x") == "ok"
    assert [c[0] for c in fake_new_genai.calls] == ["K1", "K2"]
    assert vb._KEY_IDX[0] == 1  # stays on the working key


def test_generate_all_keys_exhausted_raises(fake_new_genai, keys):
    fake_new_genai.reply(RuntimeError("quota exceeded"), RuntimeError("quota exceeded"))
    with pytest.raises(RuntimeError, match="quota"):
        vb._generate("x")


def test_generate_non_quota_error_is_not_retried(fake_new_genai, keys):
    fake_new_genai.reply(ValueError("bad request"))
    with pytest.raises(ValueError):
        vb._generate("x")
    assert len(fake_new_genai.calls) == 1


def test_generate_none_text_becomes_empty(fake_new_genai, keys):
    fake_new_genai.reply(None)
    assert vb._generate("x") == ""


@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Mana: {"a": "qator\nboshi"} tamom', {"a": "qator\nboshi"}),
])
def test_parse_json(raw, expected):
    assert vb._parse_json(raw) == expected


def test_parse_json_garbage_raises():
    with pytest.raises(ValueError):
        vb._parse_json("no json here")


def test_kb_manba_matches_direction(tmp_path, monkeypatch):
    kbf = tmp_path / "kb.json"
    kbf.write_text(json.dumps([
        "junk",
        {"nomi": "EKG", "matn": "Kardiologiya EKG matni", "kafedra": "Kardiologiya"},
        {"nomi": "Tish", "matn": "Stomatologiya"},
    ]), encoding="utf-8")
    monkeypatch.setattr(vb, "_KB_FILE", kbf)
    for _ in range(10):
        assert vb.kb_manba("Kardiologiya")["nomi"] == "EKG"


def test_kb_manba_missing_or_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(vb, "_KB_FILE", tmp_path / "none.json")
    assert vb.kb_manba("Terapiya") is None
    (tmp_path / "kb.json").write_text(json.dumps([{"nomi": "x", "matn": "   "}]), encoding="utf-8")
    monkeypatch.setattr(vb, "_KB_FILE", tmp_path / "kb.json")
    assert vb.kb_manba("Terapiya") is None


def test_kb_manba_unknown_direction_uses_lowercased_name(tmp_path, monkeypatch):
    kbf = tmp_path / "kb.json"
    kbf.write_text(json.dumps([{"nomi": "Oftalmoskopiya", "matn": "oftalmologiya ko'z tubi"},
                               {"nomi": "Boshqa", "matn": "boshqa"}]), encoding="utf-8")
    monkeypatch.setattr(vb, "_KB_FILE", kbf)
    for _ in range(10):
        assert vb.kb_manba("Oftalmologiya")["nomi"] == "Oftalmoskopiya"


def test_yangi_bemor_builds_case(fake_new_genai, keys, tmp_path, monkeypatch):
    monkeypatch.setattr(vb, "kb_manba", lambda y: {"nomi": "CPR", "matn": "M" * 2000})
    fake_new_genai.reply('{"ism": "Ali", "yosh": 45, "yashirin_tashxis": "MI"}')
    case = vb.yangi_bemor("Kardiologiya", "Qiyin")
    assert case["ism"] == "Ali" and case["yonalish"] == "Kardiologiya" and case["qiyinlik"] == "Qiyin"
    assert case["manba_nomi"] == "CPR" and len(case["manba_matn"]) == 1500
    prompt = fake_new_genai.calls[0][2]
    assert "QIYIN" in prompt and "MATERIAL: CPR" in prompt


def test_bemor_javob_strips_prefix_and_limits_history(fake_new_genai, keys):
    fake_new_genai.reply("Bemor: Bemor: Ko'kragim og'riyapti.")
    suhbat = [("talaba", f"savol {i}") if i % 2 == 0 else ("bemor", f"javob {i}") for i in range(30)]
    out = vb.bemor_javob({"ism": "Ali", "yashirin_tashxis": "MI"}, suhbat, "Qayeringiz og'riyapti?")
    assert out == "Ko'kragim og'riyapti."
    _, _, prompt, cfg = fake_new_genai.calls[0]
    assert "savol 0" not in prompt and "javob 29" in prompt
    assert "MI" in cfg.system_instruction


def test_tahlil_and_baholash(fake_new_genai, keys):
    fake_new_genai.reply("Hb 110 g/l (120-160)", '{"ball": 85, "tashxis_togri": true}')
    case = {"ism": "Ali", "yashirin_tashxis": "Anemiya", "manba_matn": "standart", "manba_nomi": "KAK"}
    assert "Hb" in vb.tahlil_natija(case, "KAK")
    res = vb.baholash(case, [], [], "Anemiya", "")
    assert res == {"ball": 85, "tashxis_togri": True}
    prompt = fake_new_genai.calls[1][2]
    assert "(savol bermagan)" in prompt and "(tahlil buyurmagan)" in prompt and "(yozilmagan)" in prompt
    assert "standart" in prompt


def test_natija_saqla_and_read(tmp_path, monkeypatch):
    f = tmp_path / "vk.json"
    monkeypatch.setattr(vb, "_NATIJA_FILE", f)
    assert vb.natijalar() == []
    vb.natija_saqla("", {"yonalish": "Terapiya", "yashirin_tashxis": "Pnevmoniya"}, {"ball": 70})
    vb.natija_saqla("Dilnoza", {"manba_nomi": "CPR"}, {"ball": 90, "togri_tashxis": "MI", "tashxis_togri": 1})
    rows = vb.natijalar()
    assert len(rows) == 2
    assert rows[0]["talaba"] == "Noma'lum" and rows[0]["kasallik"] == "Pnevmoniya"
    assert rows[1]["tashxis_togri"] is True and rows[1]["manba"] == "CPR"
    assert "Dilnoza" in f.read_text(encoding="utf-8")  # unicode kept readable


def test_natija_saqla_does_not_destroy_corrupt_journal(tmp_path, monkeypatch):
    """If virtual_kasalxona.json is unreadable (half-written, edited by hand),
    natija_saqla silently starts a new list and OVERWRITES all earlier student
    results with just the newest one."""
    f = tmp_path / "vk.json"
    f.write_text('[{"talaba": "old student", "ball": 50}', encoding="utf-8")  # truncated JSON
    monkeypatch.setattr(vb, "_NATIJA_FILE", f)
    vb.natija_saqla("new", {}, {"ball": 1})
    assert "old student" in f.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Fuzzing: random voice-transcript garbage must never crash the tools
# ─────────────────────────────────────────────────────────────────────────────
def _random_texts(n=400, seed=1234):
    import random
    rnd = random.Random(seed)
    alphabet = ("abcdefghijklmnopqrstuvwxyz'ʻ‘’ -"
                "абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ"
                "0123456789.,!?()[]{}\"\\/\n\t🙂\u200b")
    for _ in range(n):
        yield "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 60)))


def test_fuzz_amaliy_konikma(kb):
    for q in _random_texts():
        out = ak.amaliy_konikma({"query": q, "fakultet": q[:5]})
        assert isinstance(out, str) and out


def test_fuzz_moslashtir():
    for q in _random_texts():
        r = tn._moslashtir(q, STUDENTS)
        assert r is None or r in STUDENTS


def test_fuzz_parse_json_only_raises_value_error():
    for t in _random_texts():
        try:
            vb._parse_json(t)
        except ValueError:
            pass
