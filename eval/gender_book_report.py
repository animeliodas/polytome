"""Итог редактуры рода по всей книге: исходный перевод vs отредактированная копия.

python eval/gender_book_report.py <папка исходного перевода> <книга Polytome>
"""
import collections
import difflib
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from polytome.checks import gender_votes  # noqa: E402
from polytome.glossary import Glossary  # noqa: E402
from polytome.workspace import Workspace, chapter_number  # noqa: E402

orig_dir, book = Path(sys.argv[1]), sys.argv[2]
ws = Workspace(book)
cast = {e["target"]: e["gender"] for e in Glossary(ws.glossary_path).entries
        if e.get("kind") == "person" and e.get("gender") in ("m", "f")}
NAMES = list(cast)


def count(path: str):
    t = Path(path).read_text(encoding="utf-8-sig")
    right, wrong = collections.Counter(), collections.Counter()
    for name, g, _ in gender_votes(t, NAMES):
        if (g == "masc") == (cast[name] == "m"):  # без «a and b or c»: пустой Counter ложен
            right[name] += 1
        else:
            wrong[name] += 1
    return right, wrong


def total(paths):
    R, W = collections.Counter(), collections.Counter()
    with ProcessPoolExecutor(8) as ex:
        for r, w in ex.map(count, paths, chunksize=25):
            R.update(r)
            W.update(w)
    return R, W


if __name__ == "__main__":
    nums = sorted(ws.chapters())
    R0, W0 = total([str(orig_dir / f"Глава {n}.txt") for n in nums])
    R1, W1 = total([str(ws.target_path(n)) for n in nums])
    print(f"{'персонаж':14} пол   ошибок до → после   верных до → после")
    for name in sorted(cast, key=lambda x: -(W0[x] + R0[x])):
        print(f"{name:14} {'Ж' if cast[name] == 'f' else 'М'}     {W0[name]:5} → {W1[name]:<5}       {R0[name]:5} → {R1[name]}")
    print(f"ИТОГО ошибок рода (детектор): {sum(W0.values())} → {sum(W1.values())}; "
          f"верных согласований: {sum(R0.values())} → {sum(R1.values())}")
    edits = []
    for n in nums:
        st = ws.load_state(n)
        for s in (st or {}).get("segments", []):
            for h in s.get("history", []):
                if h.get("by") == "gender-editor":
                    edits.append((n, s["id"], h["old"], s["tr"]))
    print(f"исправлено абзацев: {len(edits)} в {len({e[0] for e in edits})} главах")
    random.Random(11).shuffle(edits)
    for n, i, old, new in edits[:15]:
        d = " ".join(x for x in difflib.ndiff(old.split(), new.split()) if x[:1] in "+-")
        print(f"  гл.{n} [{i}] {d[:150]}")
