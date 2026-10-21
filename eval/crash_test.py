"""Тест устойчивости: жёсткое убийство процесса посреди перевода (как выключение ПК) и продолжение.

python eval/crash_test.py            — полный сценарий (родитель)
python eval/crash_test.py child      — дочерний процесс: переводит главы, печатает события
python eval/crash_test.py cancel     — сценарий Ctrl+C: мягкая остановка внутри процесса и продолжение
"""
import os
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BOOK = ROOT / "books" / "rich_crash"
SRC = Path(os.environ.get("POLYTOME_EVAL_BOOK") or Path.home() / "Desktop/Первод книг/Книги/Бешеная жизнь богача") / "Оригинал"
CHAPTERS = "21-26"


def setup():
    if BOOK.exists():
        shutil.rmtree(BOOK)
    (BOOK / "Оригинал").mkdir(parents=True)
    for n in range(21, 27):
        shutil.copy(SRC / f"Глава {n}.txt", BOOK / "Оригинал")
    (BOOK / ".polytome").mkdir()
    for f in ("glossary.json", "bible.md"):
        shutil.copy(ROOT / "books/rich_agent/.polytome" / f, BOOK / ".polytome" / f)
    (BOOK / ".polytome/book.json").write_text(json.dumps({"title": "crash-test", "batch_chars": 450}), encoding="utf-8")


def engine(on_event):
    from polytome.config import Config
    from polytome.engine import Engine
    from polytome.llm import LLMClient
    from polytome.tools import Toolbox
    from polytome.workspace import Workspace
    eng = Engine(Workspace(BOOK), Config.load(), LLMClient(10), on_event=on_event)
    return eng, Toolbox(eng)


def log_event(e):
    if e["ev"] in ("batch_done", "chapter_done", "resume", "chapter_cancelled", "job_start"):
        e = {k: v for k, v in e.items() if k != "report"} | ({"complete": e["report"].get("complete")}
                                                              if "report" in e else {})
        print(time.strftime("%H:%M:%S"), json.dumps(e, ensure_ascii=False), flush=True)


def child():
    eng, tb = engine(log_event)
    out = json.loads(tb.call("translate", {"chapters": CHAPTERS}))
    print("RESULT", json.dumps({k: out.get(k) for k in ("translated", "failed", "skipped", "resumed_job",
                                                         "cancelled", "seconds")}, ensure_ascii=False), flush=True)


def inspect(tag):
    from polytome.workspace import Workspace, read_json
    ws = Workspace(BOOK)
    rows = []
    for n in range(21, 27):
        st = read_json(ws.state_path(n))
        done = sum(1 for s in (st or {}).get("segments", []) if s.get("tr"))
        total = len(ws.source_paragraphs(n))
        rows.append(f"гл.{n}: файл={'да' if ws.is_translated(n) else 'нет'} стадия={(st or {}).get('stage')} "
                    f"абз={done}/{total}")
    job = ws.load_job() or {}
    corrupt = list((BOOK / ".polytome").rglob("*.corrupt-*")) + list((BOOK).rglob("*.tmp"))
    print(f"--- {tag}: job.status={job.get('status')} done={job.get('done')} | битых/tmp файлов: {len(corrupt)}")
    for r in rows:
        print("   ", r)


def scenario_kill():
    setup()
    print("=== Сценарий 1: жёсткое убийство процесса через 50 с ===")
    p = subprocess.Popen([sys.executable, __file__, "child"], cwd=ROOT,
                         env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    time.sleep(50)
    p.kill()  # TerminateProcess: никакой finally/atexit — как выключение питания
    p.wait()
    print("процесс убит")
    inspect("после убийства")
    print("=== Перезапуск ===")
    t0 = time.time()
    subprocess.run([sys.executable, __file__, "child"], cwd=ROOT,
                   env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    print(f"перезапуск занял {time.time() - t0:.0f} с")
    inspect("после перезапуска")


def scenario_cancel():
    setup()
    print("=== Сценарий 2: Ctrl+C (мягкая остановка) через 40 с ===")
    eng, tb = engine(log_event)
    threading.Timer(40, eng.cancel.set).start()
    t0 = time.time()
    out = json.loads(tb.call("translate", {"chapters": CHAPTERS}))
    print(f"остановка заняла {time.time() - t0 - 40:.0f} с после сигнала;", "cancelled:", out.get("cancelled"),
          "translated:", out.get("translated"), out.get("note"))
    inspect("после остановки")
    eng.cancel.clear()
    out = json.loads(tb.call("translate", {"chapters": CHAPTERS}))
    print("продолжение:", {k: out.get(k) for k in ("translated", "skipped", "resumed_job", "cancelled")})
    inspect("после продолжения")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "kill"
    {"child": child, "kill": scenario_kill, "cancel": scenario_cancel}[mode]()
