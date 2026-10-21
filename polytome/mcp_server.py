"""MCP-сервер (stdio, JSON-RPC 2.0): отдаёт инструменты Polytome любому MCP-клиенту — Hermes, Claude Code и т.п.

Запуск:  python -m polytome.mcp_server <папка книги>
Клиенту (пример для Claude Code / Hermes):
  {"command": "<...>/.venv/Scripts/python.exe", "args": ["-m", "polytome.mcp_server", "<папка книги>"],
   "env": {"PYTHONPATH": "<папка polytome>"}}
Без зависимостей: протокол реализован вручную (initialize, tools/list, tools/call, ping).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .i18n import _
from .config import Config
from .engine import Engine
from .llm import LLMClient
from .tools import Toolbox
from .workspace import Workspace, is_book

PROTOCOL_VERSION = "2025-06-18"


def _log(msg: str):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def serve(book: str):
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = Config.load()
    ws = Workspace(Path(book))
    if not ws.meta_dir.is_dir():
        ws.init()
    llm = LLMClient(cfg.parallel)
    eng = Engine(ws, cfg, llm, on_event=lambda e: _log(json.dumps(e, ensure_ascii=False)[:300]))
    tb = Toolbox(eng)

    def reply(id_, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": id_}
        if error:
            msg["error"] = error
        else:
            msg["result"] = result
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, id_, params = req.get("method"), req.get("id"), req.get("params") or {}
        if id_ is None:  # уведомление
            continue
        if method == "initialize":
            reply(id_, {"protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "polytome", "version": "0.1"},
                        "instructions": "Инструменты агентного перевода книги. Начни с workspace_status."})  # i18n: skip
        elif method == "ping":
            reply(id_, {})
        elif method == "tools/list":
            tools = [{"name": t.name, "description": t.description, "inputSchema": t.schema()["function"]["parameters"]}
                     for t in tb.tools.values()]
            tools.append({"name": "open_book", "description": "Создать книгу из EPUB/TXT/папки глав (или открыть "  # i18n: skip
                          "существующую папку книги) и переключиться на неё.",
                          "inputSchema": {"type": "object", "properties": {"source": {"type": "string"},
                                          "library": {"type": "string"}}, "required": ["source"]}})
            reply(id_, {"tools": tools})
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name == "open_book":
                try:
                    from .formats import create_book
                    src = Path(args["source"])
                    if is_book(src):
                        ws2, rep = Workspace(src), {"opened": str(src)}
                    else:
                        ws2, rep = create_book(src, Path(args["library"]) if args.get("library") else None)
                    eng = Engine(ws2, cfg, llm, on_event=lambda e: _log(json.dumps(e, ensure_ascii=False)[:300]))
                    tb = Toolbox(eng)
                    out = json.dumps(rep, ensure_ascii=False)
                except Exception as e:  # noqa: BLE001
                    out = json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
                reply(id_, {"content": [{"type": "text", "text": out}], "isError": out.startswith('{"error"')})
                continue
            out = tb.call(name, args)
            is_err = out.startswith('{"error"')
            reply(id_, {"content": [{"type": "text", "text": out}], "isError": is_err})
        else:
            reply(id_, error={"code": -32601, "message": _("метод {0} не поддерживается").format(method)})


if __name__ == "__main__":
    serve(sys.argv[1] if len(sys.argv) > 1 else ".")
