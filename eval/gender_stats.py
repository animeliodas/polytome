"""Статистика пола персонажей по русскому тексту (без оригинала): голосование по всей книге.

python eval/gender_stats.py <папка с главами перевода> [мин. упоминаний]
"""
import collections
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from polytome.checks import find_chinese_names, gender_votes  # noqa: E402
from polytome.workspace import chapter_number, split_paragraphs  # noqa: E402


def norm(name: str) -> str:
    """«Ли Сяобая» → «Ли Сяобай»: грубо снимаем падежное окончание у имени (для подсчёта)."""
    return name


def scan(path: str):
    text = Path(path).read_text(encoding="utf-8-sig")
    names = collections.Counter()
    for p in split_paragraphs(text):
        for nm in find_chinese_names(p):
            names[nm] += 1
    return chapter_number(Path(path).stem), names, text


def main():
    folder, min_count = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 40
    files = sorted(folder.glob("*.txt"), key=lambda f: chapter_number(f.stem) or 0)
    total = collections.Counter()
    texts = {}
    with ProcessPoolExecutor(8) as ex:
        for n, names, text in ex.map(scan, map(str, files), chunksize=20):
            total.update(names)
            texts[n] = text
    main_names = [nm for nm, c in total.most_common(400) if c >= min_count]
    print(f"глав {len(texts)}, персонажей с ≥{min_count} упоминаний: {len(main_names)}")
    votes = collections.defaultdict(collections.Counter)
    where = collections.defaultdict(list)
    with ProcessPoolExecutor(8) as ex:
        futs = {n: ex.submit(gender_votes, t, main_names) for n, t in texts.items()}
        for n, f in futs.items():
            for name, g, verb in f.result():
                votes[name][g] += 1
                where[(name, g)].append((n, verb))
    rows = []
    for name in main_names:
        v = votes[name]
        tot = v["masc"] + v["femn"]
        if tot < 5:
            continue
        maj = "masc" if v["masc"] >= v["femn"] else "femn"
        minority = tot - v[maj]
        rows.append((minority / tot, name, v["masc"], v["femn"], maj, total[name]))
    rows.sort(reverse=True)
    print(f"{'персонаж':22} {'упом.':>6} {'муж':>5} {'жен':>5}  пол   доля «не того рода»")
    for share, name, m, f, maj, cnt in rows[:45]:
        print(f"{name:22} {cnt:6} {m:5} {f:5}  {'М' if maj == 'masc' else 'Ж'}    {share:5.1%}")
    errs = sum(min(m, f) for _, _, m, f, _, _ in rows)
    print(f"всего голосов: {sum(m + f for _, _, m, f, _, _ in rows)}, против большинства: {errs}")
    # примеры для самых спорных
    for share, name, m, f, maj, cnt in rows[:6]:
        mino = "femn" if maj == "masc" else "masc"
        print(f"  {name}: «не того рода» в главах", sorted({n for n, _ in where[(name, mino)]})[:15])


if __name__ == "__main__":
    main()
