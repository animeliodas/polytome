"""Multilingual tests (no network). Run: .venv/Scripts/python tests/test_lang.py"""
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polytome.checks import check_segment  # noqa: E402
from polytome.formats import create_book, detect_lang, export_book, read_txt  # noqa: E402
from polytome.lang import Pair, normalize  # noqa: E402
from polytome.palladius import transcribe  # noqa: E402


def test_normalize_and_pairs():
    assert normalize("ru-RU") == "ru" and normalize("Chinese") == "zh" and normalize("zh_Hant") == "zh"
    assert normalize("pt-BR") == "pt" and normalize("Deutsch") == "de" and normalize("английский") == "en"
    zr, ze, je, er, ku = (Pair.of("zh", "ru"), Pair.of("zh", "en"), Pair.of("ja", "en"), Pair.of("en", "ru"),
                          Pair.of("zh", "uk"))
    assert zr.name_system == "palladius" and ze.name_system == "pinyin" and je.name_system is None
    assert ku.name_system is None  # Палладий — только для русского
    assert zr.prompt_lang == "ru" and ze.prompt_lang == "en" and er.prompt_lang == "ru"
    assert er.leftover_re is None and je.leftover_re.search("です") and ze.leftover_re.search("中")
    assert not Pair.of("ko", "en").leftover_re.search("中") and Pair.of("ko", "en").leftover_re.search("한")


def test_detect_lang():
    samples = {
        "zh": "黎小白笑了笑，说道：你好吗？我很好，谢谢你的关心。",
        "ja": "彼は笑って言った。「元気ですか？私は元気です、ありがとう。」",
        "ko": "그는 웃으며 말했다. 잘 지냈어? 나는 잘 지내, 고마워.",
        "ru": "Он улыбнулся и сказал: как ты? У меня всё хорошо, спасибо.",
        "uk": "Він усміхнувся і сказав: як ти? У мене все добре, дякую. Їжак і її кіт.",
        "en": "He smiled and said that it was a good day, and she had not seen him for the whole week.",
        "es": "Él sonrió y dijo que el día era bueno, y que ella no lo había visto en toda la semana para nada.",
        "fr": "Il a souri et a dit que la journée était belle, et qu'elle ne l'avait pas vu de la semaine.",
        "de": "Er lächelte und sagte, dass es ein guter Tag ist, und sie hat ihn nicht mit den anderen gesehen.",
        "ar": "ابتسم وقال إن اليوم جميل وإنها لم تره طوال الأسبوع.",
    }
    got = {k: detect_lang(v) for k, v in samples.items()}
    assert got == {k: k for k in samples}, got


def test_pinyin_names():
    cases = {("黎小白", "person"): "Li Xiaobai", ("欧阳锋", "person"): "Ouyang Feng", ("吕布", "person"): "Lü Bu",
             ("西安", "place"): "Xi'an", ("香港", "place"): "Hong Kong", ("夏凝雨", "person"): "Xia Ningyu"}
    for (src, kind), want in cases.items():
        assert transcribe(src, kind, "pinyin")["target"] == want, (src, transcribe(src, kind, "pinyin"))
    assert transcribe("黎小白", "person")["target"] == "Ли Сяобай"  # по умолчанию — Палладий, как раньше


def test_checks_zh_en():
    pair = Pair.of("zh", "en")
    entries = [{"source": "楊心雅", "target": "Yang Xinya", "kind": "person", "gender": "f"}]
    src = "楊心雅看著他，笑了笑說道：「你好，我叫楊心雅，很高興認識你。」"
    ok = check_segment(src, "Yang Xinya looked at him and smiled: \"Hi, I'm Yang Xinya, nice to meet you.\"",
                       entries, pair=pair)
    assert not [i for i in ok if i["level"] == "hard"], ok
    bad = check_segment(src, "Yang Xinyi looked at him and smiled: \"Hi, I'm Yang Xinyi, nice to meet 你.\"",
                        entries, pair=pair)
    types = {i["type"] for i in bad}
    assert {"name_variant", "untranslated"} <= types, bad
    assert "gender" not in types  # морфологическая проверка рода — только для русского


def test_checks_other_pairs():
    ja = check_segment("彼は笑った。", "He smiled. ありがとう", [], pair=Pair.of("ja", "en"))
    assert any(i["type"] == "untranslated" for i in ja), ja
    en_ru = check_segment("He smiled at Harry Potter.", "Он улыбнулся Гарри Поттеру.", [], pair=Pair.of("en", "ru"))
    assert not [i for i in en_ru if i["level"] == "hard"], en_ru  # латиница оригинала в русском не «остаток»


def test_prompts_parity_and_pairs():
    from polytome import prompts as RU
    from polytome import prompts_en as EN
    from polytome.prompting import RU_MESSAGES, Prompts
    ru_keys = {k.removesuffix("_zh").removesuffix("_palladius") for k in RU_MESSAGES}
    en_keys = {k.removesuffix("_examples_zh") for k in EN.MESSAGES}
    assert ru_keys == en_keys, (ru_keys ^ en_keys)
    assert Prompts(Pair.of("zh", "ru")).translator() == RU.TRANSLATOR_BASE.format(src="китайского")
    zh_en = Prompts(Pair.of("zh", "en")).translator()
    assert "Hanyu Pinyin" in zh_en and "Chinese into English" in zh_en and "Палладий" not in zh_en
    en_ru = Prompts(Pair.of("en", "ru")).translator()
    assert "с английского языка" in en_ru and "Палладия" not in en_ru and "иероглиф" not in en_ru
    es = Prompts(Pair.of("ja", "es"))
    assert "Hepburn" in es.translator() and "Capítulo 12" in es.translator()
    assert "Spanish" in es.reviewer("") and "Answer — JSON" in es.gender_editor("- X — female")
    for pair in (Pair.of("ja", "es"), Pair.of("zh", "en"), Pair.of("en", "ru"), Pair.of("zh", "ru")):
        P = Prompts(pair)  # все промты собираются (подстановки и фигурные скобки JSON)
        for text in (P.translator(), P.format_rules, P.reviewer("x"), P.extractor(), P.bible_writer("s", P.bible_template("T")),
                     P.bible_refresher(), P.gender_editor("- A"), P.glossary_resolver(), P.digits_rule()):
            assert text and "{src}" not in text and "{tgt}" not in text, (pair, text[:80])


def test_book_languages_and_export():
    tmp = Path(tempfile.mkdtemp())
    txt = tmp / "novel.txt"
    txt.write_text("Capítulo 1\nÉl sonrió y dijo que el día era bueno, y que ella no lo había visto.\n"
                   "Capítulo 2\nElla se fue de la casa para siempre y no volvió nunca más a la ciudad.\n",
                   encoding="utf-8")
    assert len(read_txt(txt)["chapters"]) == 2
    ws, rep = create_book(txt, tmp / "lib", target_lang="de")
    assert rep["lang"] == "es" and ws.meta["target_lang"] == "de" and ws.pair.tgt.code == "de"
    for n in (1, 2):
        ws.target_dir.mkdir(parents=True, exist_ok=True)
        ws.target_path(n).write_text(f"Kapitel {n}\nEr lächelte.", encoding="utf-8")
    e = export_book(ws, "epub", tmp / "out.epub", title="Test")
    with zipfile.ZipFile(e["path"]) as z:
        opf = z.read("OEBPS/content.opf").decode("utf-8")
        nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
    assert "<dc:language>de</dc:language>" in opf and 'lang="de"' in nav and "Inhalt" in nav, (opf, nav)


def test_fb2_import():
    from polytome.formats import read_fb2
    tmp = Path(tempfile.mkdtemp())
    txt = tmp / "novel.txt"
    txt.write_text("第一章 开始\n黎小白笑了。\n楊心雅看著他。\n第二章 继续\n他走了。\n", encoding="utf-8")
    ws, _rep = create_book(txt, tmp / "lib", target_lang="en")
    for n in (1, 2):
        ws.target_dir.mkdir(parents=True, exist_ok=True)
        ws.target_path(n).write_text(f"Chapter {n}: Start\nLi Xiaobai smiled.", encoding="utf-8")
    fb2 = Path(export_book(ws, "fb2", tmp / "book.fb2", title="Test")["path"])
    back = read_fb2(fb2)
    assert back["title"] == "Test" and len(back["chapters"]) == 2, back
    assert back["chapters"][1]["paragraphs"] == ["Chapter 2: Start", "Li Xiaobai smiled."], back["chapters"][1]
    zp = tmp / "book.fb2.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.write(fb2, "book.fb2")
    ws2, rep2 = create_book(zp, tmp / "lib2", target_lang="ru")
    assert rep2["chapters"] == 2 and rep2["lang"] == "en" and ws2.meta["target_lang"] == "ru", rep2


def test_interrupted_import_leaves_no_book():
    from polytome.api import Polytome
    tmp = Path(tempfile.mkdtemp())
    txt = tmp / "novel.txt"
    txt.write_text("".join(f"第{i}章 标题\n黎小白笑了。\n" for i in range(1, 121)), encoding="utf-8")

    def boom(stage, done, total):
        if stage == "write" and done >= 50:
            raise KeyboardInterrupt  # как будто приложение закрыли посреди импорта

    try:
        create_book(txt, tmp / "lib", target_lang="en", on_progress=boom)
        raise AssertionError("ожидалось прерывание")
    except KeyboardInterrupt:
        pass
    h = Polytome({"target_lang": "en"}, home=tmp)
    assert h.books(tmp / "lib") == []  # недописанная временная папка — не книга
    events = []
    h.listener = events.append
    info = h.import_book(txt, library=tmp / "lib")
    assert info["chapters_total"] == 120 and [b["path"] for b in h.books(tmp / "lib")] == [info["path"]]
    assert not any(p.name.startswith(".importing") for p in (tmp / "lib").iterdir())
    assert any(e["ev"] == "import_progress" and e["stage"] == "write" for e in events), events[:3]


def test_api_languages():
    from polytome.api import Polytome, PolytomeError
    tmp = Path(tempfile.mkdtemp())
    txt = tmp / "novel.txt"
    txt.write_text("第一章 开始\n黎小白笑了。\n第二章 继续\n他走了。\n", encoding="utf-8")
    h = Polytome({"target_lang": "en", "providers": {"openrouter": {"api_key": "x"}}}, home=tmp)
    info = h.import_book(txt)
    assert info["source_lang"] == "zh" and info["target_lang"] == "en", info
    assert any(x["code"] == "en" and x["default"] and x["name_system"] == "pinyin" for x in h.languages())
    assert h.set_languages(info["path"], target_lang="es")["target_lang"] == "es"
    from polytome.workspace import Workspace
    ws = Workspace(info["path"])
    ws.target_dir.mkdir(parents=True, exist_ok=True)
    ws.target_path(1).write_text("Capítulo 1", encoding="utf-8")
    try:
        h.set_languages(info["path"], target_lang="fr")
        raise AssertionError("ожидалась ошибка: язык при готовом переводе")
    except PolytomeError:
        pass
    r = json.loads(h.call("languages", {"source_lang": "ja"}))
    assert r["ok"] and all(x["name_system"] is None for x in r["result"])


def test_catalog_complete():
    import re
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import i18n_wrap
    cat = json.loads((Path(__file__).resolve().parent.parent / "polytome/locales/en.json").read_text(encoding="utf-8"))
    missing = i18n_wrap.used_keys() - set(cat)
    assert not missing, sorted(missing)[:5]
    ph = re.compile(r"\{\d+[^}]*\}")
    bad = [k for k, v in cat.items() if sorted(ph.findall(k)) != sorted(ph.findall(v))]
    assert not bad, bad[:3]


def test_english_ui_book():
    os.environ["POLYTOME_UI_LANG"] = "en"
    try:
        from polytome.i18n import _
        assert _("Нет главы {0}").format(3) == "No chapter 3"
        tmp = Path(tempfile.mkdtemp())
        txt = tmp / "novel.txt"
        txt.write_text("Chapter 1\nHe smiled and said that it was a good day.\nChapter 2\nShe left.\n", encoding="utf-8")
        ws, rep = create_book(txt, tmp / "lib", target_lang="fr")
        assert ws.source_dir.name == "Original" and (ws.source_dir / "Chapter 1.txt").is_file()
        assert ws.target_path(2).name == "Chapter 2.txt" and ws.target_dir.name == "Translation"
    finally:
        del os.environ["POLYTOME_UI_LANG"]


def test_ui_lang_env():
    from polytome import i18n
    os.environ["POLYTOME_UI_LANG"] = "en"
    assert i18n.ui_lang() == "en" and i18n.folder_names() == ("Original", "Translation")
    os.environ["POLYTOME_UI_LANG"] = "ru"
    assert i18n.folder_names() == ("Оригинал", "Перевод")
    del os.environ["POLYTOME_UI_LANG"]


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("ok  ", name)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("FAIL", name, type(e).__name__, e)
    sys.exit(1 if fails else 0)
