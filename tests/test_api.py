"""Тесты API для приложений и JSON-RPC сервера (без сети). Запуск: .venv/Scripts/python tests/test_api.py"""
import io
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polytome.api import Polytome, PolytomeError  # noqa: E402
from polytome.config import Config  # noqa: E402
from polytome.server import Server  # noqa: E402

CONFIG = {"slots": {"strong": "openrouter:test-model", "fast": "openrouter:test-model",
                    "reviewer": "openrouter:test-model", "extractor": "openrouter:test-model"},
          "providers": {"openrouter": {"api_key": "test-key-not-real"}}, "profile": "economy"}


def _novel(tmp: Path) -> Path:
    txt = tmp / "novel.txt"
    txt.write_text("第一章 开始\n黎小白笑了。\n楊心雅看著他。\n第二章 继续\n他走了。\n", encoding="utf-8")
    return txt


def _fake_translate(book: str, n: int, paras: list[str]):
    from polytome.workspace import Workspace
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
    assert not (home / "polytome.json").exists() and not (home / "secrets.json").exists()  # ничего не записано


def test_library_and_reading():
    tmp = Path(tempfile.mkdtemp())
    h = Polytome(CONFIG, home=tmp / "home")
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
    except PolytomeError:
        pass


def test_check_and_providers():
    h = Polytome(CONFIG, home=Path(tempfile.mkdtemp()))
    c = h.check()
    assert c["ready"] and c["roles"]["translator"]["model"] == "openrouter:test-model", c
    no_key = Polytome({**CONFIG, "providers": {}}, home=Path(tempfile.mkdtemp()))
    assert not no_key.check()["ready"]
    prov = {p["id"]: p for p in h.providers()}
    assert prov["openrouter"]["has_key"] and prov["ollama"]["local"] and not prov["ollama"]["needs_key"]
    assert any(p["current"] for p in h.profiles())


def test_models_metadata():
    import httpx
    payload = {"data": [
        {"id": "b/model", "name": "B", "context_length": 128000, "pricing": {"prompt": "0.0000003", "completion": "0.0000012"}},
        {"id": "a/free:free", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "c/plain"},  # большинство провайдеров отдают только id
    ]}
    h = Polytome(CONFIG, home=Path(tempfile.mkdtemp()))
    h.llm.http = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
    ms = h.models("openrouter")
    assert [m["id"] for m in ms] == ["a/free:free", "b/model", "c/plain"], ms
    assert ms[1]["price_in"] == 0.3 and ms[1]["price_out"] == 1.2 and ms[1]["context"] == 128000
    assert ms[0].get("free") and "price_in" not in ms[2] and ms[2]["name"] == "c/plain"
    assert h.llm.list_models(h.cfg.resolve("openrouter:_")) == ["a/free:free", "b/model", "c/plain"]


def test_legacy_book_folder():
    tmp = Path(tempfile.mkdtemp())
    h = Polytome(CONFIG, home=tmp)
    book = Path(h.import_book(_novel(tmp))["path"])
    for old in (".codek", ".hanru"):  # книги, созданные под прежними именами проекта
        (book / ".polytome").rename(book / old)
        assert h.book_info(book)["chapters_total"] == 2
        assert (book / ".polytome").is_dir() and not (book / old).exists()


def test_profile_precedence():
    from polytome.engine import Engine
    from polytome.llm import LLMClient
    from polytome.workspace import Workspace
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
    h = Polytome(CONFIG, home=tmp)
    r = json.loads(h.call("import_book", json.dumps({"source": str(_novel(tmp))})))
    assert r["ok"] and r["result"]["chapters_total"] == 2, r
    book = r["result"]["path"]
    r = json.loads(h.call("chapter", {"book": book, "n": 2, "which": "source"}))
    assert r["ok"] and r["result"]["source"] == ["第二章 继续", "他走了。"], r
    r = json.loads(h.call("export", {"book": book}))
    assert not r["ok"] and r["error"]["type"] == "PolytomeError", r  # переведённых глав нет
    r = json.loads(h.call("rm_rf", {}))
    assert not r["ok"] and "rm_rf" in r["error"]["message"] and r["error"]["type"] == "PolytomeError"
    r = json.loads(h.call("chapters", {"book": str(tmp / "нет такой")}))
    assert not r["ok"] and r["error"]["type"] == "PolytomeError" and "нет такой" in r["error"]["message"]


def test_events_listener():
    got = []

    class KotlinLike:  # объект с onEvent(json) — как Java-интерфейс из Chaquopy
        def onEvent(self, s):
            got.append(json.loads(s))

    tmp = Path(tempfile.mkdtemp())
    h = Polytome(CONFIG, home=tmp, on_event=KotlinLike())
    book = h.import_book(_novel(tmp))["path"]
    eng = h._engine(book)
    eng.emit("chapter_start", n=1, segments=3)
    starts = [e for e in got if e["ev"] == "chapter_start"]
    assert starts and starts[0]["book"] == book and starts[0]["n"] == 1, got
    assert any(e["ev"] == "import_progress" for e in got), got  # импорт тоже сообщает о прогрессе


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
    msgs = list(map(json.loads, out.getvalue().splitlines()))
    resp = {m["id"]: m for m in msgs if "id" in m}
    assert any(m.get("method") == "event" and m["params"]["ev"] == "import_progress" for m in msgs)
    assert resp[1]["result"] == "pong"
    assert resp[2]["result"]["profile"] == "economy"
    assert resp[3]["result"]["chapters_total"] == 2
    assert resp[4]["error"]["code"] == -32601


def test_reader_help():
    """Помощь читателю: вариант правки абзаца (без сохранения), сохранение, объяснение фрагмента."""
    import httpx
    from polytome.workspace import Workspace
    tmp = Path(tempfile.mkdtemp())
    h = Polytome(CONFIG, home=tmp / "home")
    book = h.import_book(_novel(tmp))["path"]
    src = h.chapter(book, 1)["source"]
    tr = ["Глава 1. Начало", "Ли Сяобай улыбнулся.", "Ян Синья смотрела на него."]
    Workspace(book).save_state(1, {"segments": [{"id": i + 1, "src": s, "tr": t} for i, (s, t) in enumerate(zip(src, tr))]})
    seen = []

    def reply(req):
        user = json.loads(req.content)["messages"][-1]["content"]
        seen.append(user)
        asked = "Вопрос читателя" in user or "Reader's question" in user
        text = "Это имя героя." if asked else "Ли Сяобай широко улыбнулся."
        return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                                         "usage": {}})

    h.llm.http = httpx.Client(transport=httpx.MockTransport(reply))
    r = h.suggest_fix(book, 1, 1, "добавь «широко»")
    assert r["source"] == "黎小白笑了。" and r["old"] == tr[1] and r["new"] == "Ли Сяобай широко улыбнулся.", r
    assert h.chapter(book, 1)["translation"][1] == tr[1]  # вариант ещё не сохранён
    h.set_paragraph(book, 1, 1, r["new"])
    assert h.chapter(book, 1)["translation"][1] == "Ли Сяобай широко улыбнулся."
    a = h.explain(book, 1, 1, "Ли Сяобай", "Кто это?")
    assert a["answer"] == "Это имя героя." and "黎小白笑了。" in seen[-1] and "Кто это?" in seen[-1], seen[-1]
    out = json.loads(h.call("explain", {"book": book, "n": 1, "paragraph": 2}))
    assert out["ok"] and out["result"]["answer"], out
    bad = json.loads(h.call("set_paragraph", {"book": book, "n": 1, "paragraph": 9, "text": "x"}))
    assert not bad["ok"], bad


def test_translate_meta():
    """Название, автор и оглавление на языке перевода до перевода глав; «佚名» — «автор неизвестен», не имя."""
    import re
    import httpx
    from polytome.workspace import Workspace
    tmp = Path(tempfile.mkdtemp())
    h = Polytome(CONFIG, home=tmp / "home")
    book = h.import_book(_novel(tmp))["path"]
    ws = Workspace(book)
    ws.meta["author"] = "佚名"
    ws.save_meta()
    assert not h.book_info(book)["meta_translated"]
    names = {"novel": "Роман", "第一章 开始": "Глава 1. Начало", "第二章 继续": "Глава 2. Продолжение", "佚名": "Anonymous"}

    def reply(req):
        user = json.loads(req.content)["messages"][-1]["content"]
        out = [f"[{m.group(1)}] {names.get(m.group(2).strip(), 'X')}"
               for m in (re.match(r"^\[(\d+)\] (.*)$", ln) for ln in user.split("\n")) if m]
        return httpx.Response(200, json={"choices": [{"message": {"content": "\n".join(out)}, "finish_reason": "stop"}],
                                         "usage": {}})

    h.llm.http = httpx.Client(transport=httpx.MockTransport(reply))
    r = h.translate_meta(book)
    info = h.book_info(book)
    assert r["title"] == info["title"] == "Роман" and info["meta_translated"] and r["toc_translated"] == 2, (r, info)
    assert info["author"] == ("Автор неизвестен" if info["target_lang"] == "ru" else "Anonymous"), info
    assert info["original_title"] == "novel"
    toc = h.chapters(book)
    assert [c["title"] for c in toc] == ["Глава 1. Начало", "Глава 2. Продолжение"], toc
    assert toc[0]["source_title"] == "第一章 开始" and not toc[0]["translated"]
    assert h.translate_meta(book)["toc_translated"] == 0  # уже переведённое не переводится заново


def test_bench():
    """Замер модели: одинаковый набор, качество — проверками кода; результат без ключей; таблица по результатам."""
    import re
    import httpx
    from polytome import bench, bench_suite

    def good(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "test-model", "pricing": {"prompt": "0.000001",
                                                                                        "completion": "0.000002"}}]})
        body = json.loads(req.content)
        user = body["messages"][-1]["content"]
        out = []
        for m in (re.match(r"^\[(\d+)\] (.*)$", ln) for ln in user.split("\n")):
            if m:
                han = len(re.findall(r"[\u4e00-\u9fff]", m.group(2)))
                nums = " ".join(re.findall(r"\d+", m.group(2)))
                vocab = ["дождь", "дорога", "лавка", "гора", "письмо", "свет", "дверь", "ночь", "чай", "храм", "утро"]
                words = " ".join(vocab[(int(m.group(1)) * 7 + i * 3) % len(vocab)] + str(i % 5)
                                 for i in range(max(2, han // 2)))
                names = ", ".join(e["target"] for e in bench_suite.glossary("zh", "ru") if e["source"] in m.group(2))
                out.append(f"[{m.group(1)}] {names}. {words} {nums}".rstrip())
        return httpx.Response(200, json={"choices": [{"message": {"content": "\n".join(out)}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 200}})

    def echo(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        user = json.loads(req.content)["messages"][-1]["content"]
        out = [ln for ln in user.split("\n") if re.match(r"^\[\d+\] ", ln)]
        return httpx.Response(200, json={"choices": [{"message": {"content": "\n".join(out) or "x"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 10}})

    tmp = Path(tempfile.mkdtemp())
    h = Polytome(CONFIG, home=tmp / "home")
    info = h.bench_info()
    assert info["suite"].startswith("1-") and [p["pair"] for p in info["pairs"]] == ["zh-ru", "zh-en", "en-ru"], info
    from polytome.llm import LLMClient
    llm = LLMClient(2)
    llm.http = httpx.Client(transport=httpx.MockTransport(good))
    rec = bench.run(h.cfg, "openrouter:test-model", "zh-ru", tmp / "home", llm=llm)
    assert "error" not in rec, rec
    assert rec["segments"] == 18 and rec["model"] == "openrouter:test-model" and rec["pair"] == "zh-ru", rec
    assert rec["first_pass_ok"] > 50 and 0 <= rec["score"] <= 100, rec
    assert rec["tokens"]["prompt"] > 0 and rec["price_per_1m_tokens"] == {"in": 1.0, "out": 2.0} and rec["cost_usd"] > 0
    assert len(rec["translation"]) == 2 and {"Линь Сяоюй", "Цинши"} <= set(rec["translation"]["1"][1].split(". ")[0].split(", "))
    assert "test-key" not in json.dumps(rec) and "base_url" not in json.dumps(rec)  # результат можно публиковать
    assert not (tmp / "home" / ".bench").exists() or not any((tmp / "home" / ".bench").iterdir())  # временное убрано

    llm2 = LLMClient(2)
    llm2.http = httpx.Client(transport=httpx.MockTransport(echo))
    bad = bench.run(h.cfg, "openrouter:echo", "zh-ru", tmp / "home", llm=llm2)
    assert bad["first_pass_ok"] < 20 and bad["issues_first"].get("untranslated"), bad
    assert bad["score"] < rec["score"] and bad["cost_usd"] is None

    res = tmp / "results"
    p1 = bench.save(rec, res)
    p2 = bench.save(rec, res)  # тот же день и модель — второй файл, не перезапись
    assert p1 != p2 and p1.parent.name == "zh-ru"
    bench.save(bad, res)
    md = bench.leaderboard(bench.load(res))
    assert "## zh-ru" in md and "`openrouter:test-model` | 2 |" in md, md
    assert md.index("openrouter:test-model") < md.index("openrouter:echo")  # лучшая модель выше

    cancel = threading.Event()
    cancel.set()
    stopped = bench.run(h.cfg, "openrouter:test-model", "en-ru", tmp / "home", cancel=cancel, llm=llm)
    assert stopped.get("error") == "cancelled", stopped
    # отмена посреди прогона (запрос уже ушёл) — тоже «отменено», а не результат без починки
    late = threading.Event()

    def cancel_on_request(req):
        late.set()
        return good(req)

    llm3 = LLMClient(2)
    llm3.http = httpx.Client(transport=httpx.MockTransport(cancel_on_request))
    mid = bench.run(h.cfg, "openrouter:test-model", "zh-ru", tmp / "home", cancel=late, llm=llm3)
    assert mid.get("error") == "cancelled", mid
    out = json.loads(h.call("bench", {"model": "no-colon"}))
    assert not out["ok"], out


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
