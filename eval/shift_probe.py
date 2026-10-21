"""Прототип детектора сдвига абзацев: длина перевода против длины оригинала относительно медианы главы."""
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from polytome.checks import han_count  # noqa: E402
from polytome.workspace import Workspace  # noqa: E402


def flags(segs, thr=2.0):
    lens = [(max(han_count(s["src"]), len(s["src"]) // 2), len(s.get("tr") or "")) for s in segs]
    g = statistics.median([t / h for h, t in lens if h >= 10] or [3.5])
    dev = [math.log((t + 12) / ((h + 4) * g)) for h, t in lens]
    bad = [abs(d) > math.log(thr) for d in dev]
    out = set()
    for k in range(len(segs)):
        win = bad[max(0, k - 1):k + 2]
        if bad[k] and sum(win) >= 2:
            out.add(segs[k]["id"])
    return out, g


for book in sys.argv[1:]:
    ws = Workspace(book)
    total = flagged_ch = flagged_seg = 0
    examples = []
    for n in ws.chapters():
        st = ws.load_state(n)
        if not st or not all(s.get("tr") for s in st["segments"]):
            continue
        total += 1
        f, g = flags(st["segments"])
        if f:
            flagged_ch += 1
            flagged_seg += len(f)
            examples.append(f"гл.{n}:{sorted(f)[:8]}")
    print(f"{book}: глав {total}, с подозрением на сдвиг {flagged_ch}, абзацев {flagged_seg}")
    print("   ", "; ".join(examples[:12]))
