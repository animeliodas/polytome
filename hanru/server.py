"""JSON-RPC 2.0 по stdin/stdout — API Hanru для приложений на любом языке (Electron, C#, Kotlin desktop…).

Запуск:  hanru serve [--config hanru.json] [--home <папка>]   (или python -m hanru.server)
Запрос:      {"jsonrpc": "2.0", "id": 1, "method": "import_book", "params": {"source": "D:/книга.epub"}}
Ответ:       {"jsonrpc": "2.0", "id": 1, "result": {...}}
Ошибка:      {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "...", "data": {"type": "HanruError"}}}
Событие:     {"jsonrpc": "2.0", "method": "event", "params": {"ev": "chapter_done", "book": "...", ...}}
Методы — как у hanru.api.Hanru (docs/INTEGRATION.md) + "configure" {"config": {...}, "home": "..."} — заменить
конфиг (ключи в памяти). translate/resume всегда в фоне: ответ сразу, прогресс — событиями.
Каждый запрос выполняется в своём потоке; ответы могут приходить не по порядку — сверяйте id.
"""
from __future__ import annotations

import json
import sys
import threading

from .api import Hanru


class Server:
    def __init__(self, config=None, home=None, out=None):
        self.out = out or sys.stdout
        self.lock = threading.Lock()
        self.api = Hanru(config, home=home, on_event=self.event)

    def send(self, msg: dict):
        line = json.dumps(msg, ensure_ascii=False, default=str)
        with self.lock:
            self.out.write(line + "\n")
            self.out.flush()

    def event(self, ev: dict):
        self.send({"jsonrpc": "2.0", "method": "event", "params": ev})

    def handle(self, req: dict):
        id_, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        if method == "configure":
            try:
                self.api = Hanru(params.get("config") or {}, home=params.get("home"), on_event=self.event)
                res = {"ok": True, "result": self.api.config_summary()}
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "error": {"type": type(e).__name__, "message": str(e)}}
        elif method == "ping":
            res = {"ok": True, "result": "pong"}
        else:
            res = json.loads(self.api.call(method, params))
        if id_ is None:  # уведомление — ответ не нужен
            return
        if res["ok"]:
            self.send({"jsonrpc": "2.0", "id": id_, "result": res["result"]})
        else:
            err = res["error"]
            code = -32601 if err["message"].startswith("Нет метода") else -32000
            self.send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": err["message"],
                                                              "data": {"type": err["type"]}}})

    def serve(self, inp=None):
        inp = inp or sys.stdin
        pending: list[threading.Thread] = []
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as e:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"parse error: {e}"}})
                continue
            th = threading.Thread(target=self.handle, args=(req,), daemon=True)
            th.start()
            pending = [t for t in pending if t.is_alive()] + [th]
        # Клиент закрыл stdin: доотвечать на начатые запросы, фоновые переводы мягко остановить — прогресс
        # сохранится, а следующий запуск продолжит их через resume.
        for th in pending:
            th.join()
        self.shutdown()

    def shutdown(self, timeout: float = 120):
        books = list(self.api._active)
        for b in books:
            self.api.cancel(b)
        for b in books:
            self.api.wait(b, timeout)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="hanru serve", description="JSON-RPC API Hanru по stdin/stdout")
    ap.add_argument("--config", help="путь к hanru.json (по умолчанию — как у CLI)")
    ap.add_argument("--home", help="рабочая папка: библиотека и черновики")
    a = ap.parse_args(argv)
    for s in (sys.stdin, sys.stdout):
        s.reconfigure(encoding="utf-8")
    Server(a.config, a.home).serve()


if __name__ == "__main__":
    main()
