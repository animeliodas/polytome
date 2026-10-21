"""Ждать фоновый перевод Polytome, укладываясь в лимит ячейки Hermes (300 с).

python polytome_wait.py "<книга>" [--cmd polytome] [--max 240] [--every 20]
Выход: 0 — задача закончилась (done/cancelled/failed/interrupted), 3 — ещё идёт (вызови снова), 2 — ошибка.
Печатает компактный прогресс. Только стандартная библиотека.
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time


def resolve_cmd(cmd: str) -> list[str]:
    c = cmd.strip().strip('"')
    if os.path.exists(c):
        return [os.path.abspath(c)]
    found = shutil.which(c)
    if found:
        return [found]
    return shlex.split(cmd, posix=os.name != "nt")  # например: "python -m polytome"


def status(cmd: str, book: str) -> dict:
    out = subprocess.run(resolve_cmd(cmd) + ["status", book, "--json"], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    return json.loads(out.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("book")
    ap.add_argument("--cmd", default="polytome")
    ap.add_argument("--max", type=int, default=240)
    ap.add_argument("--every", type=int, default=20)
    a = ap.parse_args()
    t0 = time.time()
    last = None
    while True:
        try:
            s = status(a.cmd, a.book)
        except Exception as e:  # noqa: BLE001
            print(f"ошибка статуса: {e}")
            sys.exit(2)
        j = s.get("job") or {}
        line = (f"{time.strftime('%H:%M:%S')} {j.get('state')}: переведено {s.get('chapters_translated')}/"
                f"{s.get('chapters_total')}, в работе {j.get('in_progress')}, ETA {j.get('eta_min')} мин, "
                f"токенов {j.get('tokens_this_run')}")
        if line != last:
            print(line, flush=True)
            last = line
        if not j or j.get("state") in ("running", "starting"):
            if not j and time.time() - t0 > 90:
                print("задача так и не появилась — проверь вывод `run --background` и `status --json`")
                sys.exit(2)
        else:
            if j.get("halt_reason"):
                print("остановлено из-за ошибок провайдера:", j["halt_reason"])
            for line_ in s.get("log_tail", [])[-4:]:
                print("  ", line_)
            sys.exit(0)
        if time.time() - t0 + a.every > a.max:
            print("ещё идёт — вызови скрипт снова")
            sys.exit(3)
        time.sleep(a.every)


if __name__ == "__main__":
    main()
