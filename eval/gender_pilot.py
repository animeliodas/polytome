"""Пилот редактора рода: главы → edit_genders → голоса до/после + примеры правок.

python eval/gender_pilot.py <книга> <главы через запятую> [модель для роли reviewer]
"""
import collections
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from polytome.checks import gender_votes  # noqa: E402
from polytome.config import Config  # noqa: E402
from polytome.engine import Engine  # noqa: E402
from polytome.llm import LLMClient  # noqa: E402
from polytome.workspace import Workspace  # noqa: E402

book, chs = sys.argv[1], [int(x) for x in sys.argv[2].split(",")]
cfg = Config.load()
if len(sys.argv) > 3:
    cfg = cfg.with_overrides(roles={"reviewer": sys.argv[3]})
ws = Workspace(book)
eng = Engine(ws, cfg, LLMClient(10))
persons = {e["target"]: e["gender"] for e in eng.gl.entries if e.get("kind") == "person" and e.get("gender")}


def votes():
    wrong = collections.Counter()
    right = collections.Counter()
    for n in chs:
        t = ws.target_path(n).read_text(encoding="utf-8-sig")
        for name, g, _ in gender_votes(t, list(persons)):
            ok = (g == "masc") == (persons[name] == "m")
            (right if ok else wrong)[name] += 1
    return wrong, right


w0, r0 = votes()
t0 = time.time()
with ThreadPoolExecutor(5) as ex:
    reps = list(ex.map(eng.edit_genders, chs))
w1, r1 = votes()
print(f"время {time.time() - t0:.0f} с · {eng.llm.usage.summary()}")
for r in reps:
    print(f"гл.{r['chapter']}: исправлено абзацев {r['fixed']}, отклонено {r['rejected']}")
print("ошибок рода по детектору (до → после):", sum(w0.values()), "→", sum(w1.values()))
print("   по персонажам до:", dict(w0))
print("   по персонажам после:", dict(w1))
print("верных согласований (до → после):", sum(r0.values()), "→", sum(r1.values()))
for r in reps[:3]:
    st = ws.load_state(r["chapter"])
    shown = 0
    for s in st["segments"]:
        h = [x for x in s.get("history", []) if x.get("by") == "gender-editor"]
        if h and shown < 3:
            import difflib
            old, new = h[-1]["old"], s["tr"]
            diff = [d for d in difflib.ndiff(old.split(), new.split()) if d[:1] in "+-"]
            print(f"  гл.{r['chapter']} [{s['id']}] {' '.join(diff)[:160]}")
            shown += 1
