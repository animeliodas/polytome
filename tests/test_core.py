"""Быстрые тесты детерминированной части (без сети). Запуск: .venv/Scripts/python tests/test_core.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hanru.agent import parse_text_calls  # noqa: E402
from hanru.align import align  # noqa: E402
from hanru.checks import check_segment, find_chinese_names  # noqa: E402
from hanru.engine import parse_json_lenient, parse_numbered  # noqa: E402
from hanru.glossary import Glossary, word_stem  # noqa: E402
from hanru.palladius import transcribe  # noqa: E402
from hanru.workspace import chapter_number, cn_to_int, parse_range  # noqa: E402


def test_palladius():
    cases = {"黎小白": "Ли Сяобай", "楊心雅": "Ян Синья", "毛泽东": "Мао Цзэдун", "诸葛亮": "Чжугэ Лян",
             "曾国藩": "Цзэн Гофань", "单雄信": "Шань Сюнсинь", "夏凝雨": "Ся Нинъюй", "吕布": "Люй Бу"}
    for src, want in cases.items():
        assert transcribe(src, "person")["target"] == want, (src, transcribe(src, "person"))
    places = {"青州": "Цинчжоу", "平安": "Пинъань", "嫦娥": "Чанъэ", "延安": "Яньань", "北京": "Пекин"}
    for src, want in places.items():
        assert transcribe(src, "place")["target"] == want, (src, transcribe(src, "place"))


def test_numbers():
    assert cn_to_int("一百二十三") == 123 and cn_to_int("十五") == 15 and cn_to_int("两千") == 2000
    assert chapter_number("第一百零五章 标题") == 105 and chapter_number("Глава 12") == 12
    assert parse_range("1-3,5", [1, 2, 3, 4, 5]) == [1, 2, 3, 5]


def test_name_checks():
    gl = Glossary(Path(tempfile.mkdtemp()) / "g.json")
    gl.upsert("楊心雅", "Ян Синья", "person", "f")
    gl.upsert("黎小白", "Ли Сяобай", "person", "m")
    gl.upsert("張揚", "Чжан Ян", "person", "m")
    ents = gl.relevant("楊心雅看著黎小白")
    assert word_stem("Синья") == "Синь"
    ok = check_segment("楊心雅看著黎小白", "Ян Синья посмотрела на Ли Сяобая.", ents, gl=gl)
    assert not [i for i in ok if i["level"] == "hard"], ok
    bad = check_segment("楊心雅看著黎小白", "Ян Синя посмотрела на Ли Сяобая.", ents, gl=gl)
    assert any(i["type"] == "name_variant" for i in bad), bad
    alien = check_segment("黎小白笑了", "Су Чэнь усмехнулся.", gl.relevant("黎小白笑了"), gl=gl)
    assert any(i["type"] == "unknown_name" for i in alien), alien
    assert find_chinese_names("Привет Мир. Ну Да.") == set()


def test_untranslated_and_short():
    iss = check_segment("黎小白其實也處於懵逼狀態，他從小縣城裡出來的是沒錯，但他也不傻", "Ли 小白 был в шоке.", [])
    types = {i["type"] for i in iss}
    assert "untranslated" in types and "short" in types, iss


def test_parsers():
    out = parse_numbered("<think>хм</think>\n[1] Раз.\n[2] Два\nпродолжение\n[9] лишнее", [1, 2])
    assert out == {1: "Раз.", 2: "Два продолжение"}, out
    assert parse_json_lenient('```json\n{"fixes": [{"id": 1,}],}\n```') == {"fixes": [{"id": 1}]}
    calls = parse_text_calls('ок <tool_call>{"name": "check", "arguments": {"chapters": "1"}}</tool_call>')
    assert calls[0].name == "check" and calls[0].arguments == {"chapters": "1"}
    xml = parse_text_calls('<atem:invoke name="glossary_add"><atem:parameter name="source">楊</atem:parameter></atem:invoke>')
    assert xml[0].name == "glossary_add" and xml[0].arguments["source"] == "楊"


def test_gender_and_numbers():
    from hanru.checks import gender_issues, normalize_numbers, number_word_issues
    ents = [{"source": "苏言", "target": "Су Янь", "kind": "person", "gender": "m"},
            {"source": "沈星", "target": "Шэнь Син", "kind": "person", "gender": "f"}]
    assert gender_issues("Шэнь Син улыбнулся.", ents) and gender_issues("— Да, — сказала Су Янь.", ents)
    assert not gender_issues("Шэнь Син улыбнулась. У Шэнь Син был телефон. Су Янь кивнул.", ents)
    assert normalize_numbers("стоит 500 миллионов юаней") == "стоит 500000000 юаней"
    assert number_word_issues("дал пять юаней") and not number_word_issues("десятки тысяч юаней")


def test_formats_roundtrip():
    from xml.etree import ElementTree as ET
    from hanru.formats import create_book, export_book, read_epub, read_txt
    from hanru.workspace import Workspace
    tmp = Path(tempfile.mkdtemp())
    txt = tmp / "novel.txt"
    txt.write_text("第一章 开始\n黎小白笑了。\n楊心雅看著他。\n第二章 继续\n他走了。\n", encoding="utf-8")
    book = read_txt(txt)
    assert len(book["chapters"]) == 2 and book["lang"] == "zh", book
    ws, rep = create_book(txt, tmp / "lib")
    assert rep["chapters"] == 2 and ws.source_paragraphs(1)[0] == "第一章 开始"
    for n in (1, 2):  # «переводим» — кладём текст в Перевод
        ws.target_dir.mkdir(parents=True, exist_ok=True)
        ws.target_path(n).write_text(f"Глава {n}. Тест\nАбзац «{n}» & <тег>.", encoding="utf-8")
    e = export_book(ws, "epub", tmp / "out.epub", title="Тест")
    back = read_epub(Path(e["path"]))
    assert len(back["chapters"]) == 2 and back["chapters"][1]["paragraphs"][1] == "Абзац «2» & <тег>.", back
    f = export_book(ws, "fb2", tmp / "out.fb2", title="Тест")
    ET.parse(f["path"])


def test_align():
    src = ["第一章", "黎小白笑了笑說道：「你好嗎？我很好，謝謝你的關心。」", "楊心雅點點頭。"]
    tgt = ["Глава 1", "Ли Сяобай улыбнулся и сказал:", "— Как ты? У меня всё хорошо, спасибо за заботу.", "Ян Синья кивнула."]
    a = align(src, tgt)
    assert len(a) == 3 and "спасибо" in a[1] and a[2] == "Ян Синья кивнула.", a


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("ok  ", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    sys.exit(1 if fails else 0)
