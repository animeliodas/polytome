"""Тесты API для приложений и JSON-RPC сервера (без сети). Запуск: .venv/Scripts/python tests/test_api.py"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hanru.api import Hanru, HanruError  # noqa: E402
from hanru.config import Config  # noqa: E402
from hanru.server import Server  # noqa: E402

CONFIG = {"slots": {"strong": "openrouter:test-model", "fast": "openrouter:test-model",
                    "reviewer": "openrouter:test-model", "extractor": "openrouter:test-model"},
          "providers": {"openrouter": {"api_key": "test-key-not-real"}}, "profile": "economy"}


def _novel(tmp: Path) -> Path:
    txt = tmp / "novel.txt"
    txt.write_text("第一章 开始\n黎小白笑了。\n楊心雅看著他。\n第二章 继续\n他走了。\n", encoding="utf-8")
    return txt


def _fake_translate(book: str, n: int, paras: list[str]):
    from hanru.workspace import Workspace
    ws = Workspace(book)
    ws.target_dir.mkdir(parents=True, exist_ok=True)
    ws.target_path(n).write_text("\n\n".join(paras), encoding="utf-8")


def test_config_in_memory():
    home = Path(tempfile.mkdtemp())
    cfg = Config.from_dict(CONFIG, home)
    assert cfg.provider_key("openrouter") == "test-key-not-real"
    assert cfg.profile_name == "economy" and cfg.role("translator")[0].model == "test-model"
    assert "Числа" in cfg.style_text()  # встроенный STYLE.md из пакета
    assert Config.from_dict({**CONFIG, "style_text": "свои правила"}, home).style_text() == "свои правила"
    assert not (home / "hanru.json").exists() and not (home / "secrets.json").exists()  # ничего не записано


def test_library_and_reading():
    tmp = Path(tempfile.mkdtemp())
    h = Hanru(CONFIG, home=tmp / "home")
    info = h.import_book(_novel(tmp))
    book = info["path"]
    assert Path(book).parent == tmp / "home" / "books", book
    assert info["chapters_total"] == 2 and info["chapters_translated"] == 0 and info["source_lang"] == "zh"
    assert h.import_book(book)["path"] == book  # повторный импорт папки = открыть
    assert [b["path"] for b in h.books()] == [book]
    toc = h.chapters(book)
    assert [c["n"] for c in toc] == [1, 2] and toc[0]["title"] == "第一章 开始" and not toc[0]["translated"]
    ch = h.chapter(book, 1)
    assert ch["source"] == ["第一章 开始", "黎小白笑了。", "楊心雅看著他。"] and ch["translation"] is None
    _fake_translate(book, 1, ["Глава 1. Начало", "Ли Сяобай улыбнулся.", "Ян Синья смотрела на него."])
    ch = h.chapter(book, 1)
    assert ch["aligned"] and ch["translation"][2] == "Ян Синья смотрела на него.", ch
    toc = h.chapters(book)
    assert toc[0]["translated"] and toc[0]["title"] == "Глава 1. Начало" and not toc[1]["translated"]
    est = h.estimate(book)
    assert est["chapters"] == 1 and est["profile"] == "economy", est
    st = h.status(book)
    assert st["chapters_total"] == 2 and st["chapters_translated"] == 1 and st["active_here"] is False
    ex = h.export(book, "fb2", tmp / "out.fb2", chapters=[1])
    assert ex["chapters"] == 1 and Path(ex["path"]).is_file()
    try:
        h.chapter(book, 99)
        raise AssertionError("ожидалась ошибка")
    except HanruError:
        pass


def test_check_and_providers():
    h = Hanru(CONFIG, home=Path(tempfile.mkdtemp()))
    c = h.check()
    assert c["ready"] and c["roles"]["translator"]["model"] == "openrouter:test-model", c
    no_key = Hanru({**CONFIG, "providers": {}}, home=Path(tempfile.mkdtemp()))
    assert not no_key.check()["ready"]
    prov = {p["id"]: p for p in h.providers()}
    assert prov["openrouter"]["has_key"] and prov["ollama"]["local"] and not prov["ollama"]["needs_key"]
    assert any(p["current"] for p in h.profiles())


def test_legacy_book_folder():
    tmp = Path(tempfile.mkdtemp())
    h = Hanru(CONFIG, home=tmp)
    book = Path(h.import_book(_novel(tmp))["path"])
    (book / ".hanru").rename(book / ".codek")  # книга, созданная до переименования проекта
    assert h.book_info(book)["chapters_total"] == 2
    assert (book / ".hanru").is_dir() and not (book / ".codek").exists()


def test_profile_precedence():
    from hanru.engine import Engine
    from hanru.llm import LLMClient
    from hanru.workspace import Workspace
    tmp = Path(tempfile.mkdtemp())
    ws = Workspace(tmp / "b")
    ws.meta["profile"] = "quality"
    ws.init()
    cfg = Config.from_dict(CONFIG, tmp)
    assert Engine(ws, cfg, LLMClient(1)).cfg.profile_name == "quality"  # профиль книги важнее общего
    assert Engine(ws, cfg.with_overrides(profile="local"), LLMClient(1)).cfg.profile_name == "local"  # --profile
    assert Engine(ws, cfg, LLMClient(1), profile="balanced").cfg.profile_name == "balanced"


def test_call_json():
    tmp = Path(tempfile.mkdtemp())
    h = Hanru(CONFIG, home=tmp)
    r = json.loads(h.call("import_book", json.dumps({"source": str(_novel(tmp))})))
    assert r["ok"] and r["result"]["chapters_total"] == 2, r
    book = r["result"]["path"]
    r = json.loads(h.call("chapter", {"book": book, "n": 2, "which": "source"}))
    assert r["ok"] and r["result"]["source"] == ["第二章 继续", "他走了。"], r
    r = json.loads(h.call("export", {"book": book}))
    assert not r["ok"] and r["error"]["type"] == "HanruError", r  # переведённых глав нет
    r = json.loads(h.call("rm_rf", {}))
    assert not r["ok"] and "Нет метода" in r["error"]["message"]
    r = json.loads(h.call("chapters", {"book": str(tmp / "нет такой")}))
    assert not r["ok"] and "Не книга" in r["error"]["message"]


def test_events_listener():
    got = []

    class KotlinLike:  # объект с onEvent(json) — как Java-интерфейс из Chaquopy
        def onEvent(self, s):
            got.append(json.loads(s))

    tmp = Path(tempfile.mkdtemp())
    h = Hanru(CONFIG, home=tmp, on_event=KotlinLike())
    book = h.import_book(_novel(tmp))["path"]
    eng = h._engine(book)
    eng.emit("chapter_start", n=1, segments=3)
    assert got and got[0]["ev"] == "chapter_start" and got[0]["book"] == book and got[0]["n"] == 1, got


def test_server_stdio():
    tmp = Path(tempfile.mkdtemp())
    reqs = [{"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "configure", "params": {"config": CONFIG, "home": str(tmp)}},
            {"jsonrpc": "2.0", "id": 3, "method": "import_book", "params": {"source": str(_novel(tmp))}},
            {"jsonrpc": "2.0", "id": 4, "method": "nope"}]
    out = io.StringIO()
    srv = Server(CONFIG, home=tmp, out=out)
    for r in reqs:  # синхронно, без потоков — порядок ответов предсказуем
        srv.handle(r)
    resp = {m["id"]: m for m in map(json.loads, out.getvalue().splitlines())}
    assert resp[1]["result"] == "pong"
    assert resp[2]["result"]["profile"] == "economy"
    assert resp[3]["result"]["chapters_total"] == 2
    assert resp[4]["error"]["code"] == -32601


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
