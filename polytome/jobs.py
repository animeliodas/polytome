"""Фоновые задачи: запуск перевода отдельным процессом, статус, остановка.

Нужно для Hermes и любых агентов с ограничением времени на вызов (у Hermes ячейка живёт 300 с, у MCP-вызова —
120 с): задача стартует в фоне, а статус читается из .polytome/job.json без LLM и без токенов.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .i18n import _
from .config import APP_DIR
from .workspace import Workspace


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def spawn(book: Path, args: list[str]) -> dict:
    """Запустить `polytome <args>` для книги в фоне; вывод — в .polytome/logs/<время>.log."""
    ws = Workspace(book)
    job = ws.load_job() or {}
    if job.get("status") in ("running", "starting") and pid_alive(job.get("pid")):
        return {"error": _("для этой книги уже идёт задача"), "pid": job.get("pid"), "hint": "polytome status / polytome stop"}
    logs = ws.meta_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / time.strftime("%Y%m%d-%H%M%S.log")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(APP_DIR) + os.pathsep
           + os.environ.get("PYTHONPATH", ""), "POLYTOME_PLAIN": "1", "COLUMNS": "200"}
    cmd = [sys.executable, "-m", "polytome", *args]
    kw: dict = {"stdout": open(log, "w", encoding="utf-8"), "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL,
                "cwd": str(APP_DIR), "env": env}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kw)
    # Сразу видно «идёт запуск» — иначе в первые секунды (и во время подготовки книги) статус пуст и
    # ожидающий агент решает, что всё закончилось (так было в первом тесте Hermes).
    ws.save_job({**{k: v for k, v in job.items() if k not in ("status", "pid", "phase")},
                 "status": "starting", "pid": proc.pid, "phase": "starting", "log": str(log)})
    return {"started": True, "pid": proc.pid, "log": str(log),
            "hint": _("статус: polytome status \"{0}\"  · остановка: polytome stop \"{1}\"").format(ws.root, ws.root)}


def request_stop(book: Path) -> dict:
    ws = Workspace(book)
    job = ws.load_job() or {}
    if job.get("status") != "running" or not pid_alive(job.get("pid")):
        return {"stopped": False, "reason": _("нет работающей задачи")}
    (ws.meta_dir / "STOP").write_text("stop", encoding="utf-8")
    return {"stopped": True, "note": _("задача допишет текущие батчи, сохранит прогресс и остановится (обычно < 1 мин)")}


def status(book: Path) -> dict:
    ws = Workspace(book)
    st = ws.status()
    job = ws.load_job()
    out = {"book": st["title"], "path": st["root"], "chapters_total": st["chapters_total"],
           "chapters_translated": st["chapters_translated"]}
    if job:
        alive = pid_alive(job.get("pid"))
        state = job.get("status")
        if state in ("running", "starting") and not alive:
            state = "interrupted"  # процесс умер: сбой, выключение ПК, kill
        out["job"] = {"state": state, "phase": job.get("phase"), "pid": job.get("pid"), "alive": alive,
                      "profile": job.get("profile"),
                      "chapters": _span(job.get("chapters", [])), "done": job.get("chapters_done"),
                      "left": job.get("chapters_left"), "in_progress": job.get("in_progress"),
                      "failed": job.get("failed"), "tokens_this_run": job.get("tokens_this_run"),
                      "chapters_per_min": job.get("chapters_per_min"), "eta_min": job.get("eta_min"),
                      "started_at": job.get("started_at"), "updated_at": job.get("updated_at")}
        if job.get("halt_reason"):
            out["job"]["halt_reason"] = job["halt_reason"]
        if state in ("interrupted", "cancelled", "failed"):
            out["job"]["hint"] = _("продолжить: polytome continue <книга>")
    logs = sorted((ws.meta_dir / "logs").glob("*.log")) if (ws.meta_dir / "logs").is_dir() else []
    if logs:
        tail = logs[-1].read_text(encoding="utf-8", errors="replace").strip().splitlines()[-6:]
        out["log_tail"] = [l[:200] for l in tail]
    return out


def _span(nums: list[int]) -> str:
    return f"{min(nums)}–{max(nums)} ({len(nums)})" if nums else ""
