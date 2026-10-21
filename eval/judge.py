"""Слепое сравнение переводов одной главы несколькими моделями-судьями.

python eval/judge.py <workspace> <chapter> <judge1,judge2> file1.txt file2.txt ... [--window 15]
Имя кандидата = имя файла. Буквы кандидатов перемешиваются в каждом окне (борьба с позиционным смещением).
Вывод: таблица средних баллов + eval/out/judge_ch<N>.json (с ошибками, которые нашли судьи).
"""
import json
import random
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from polytome.align import align  # noqa: E402
from polytome.config import Config  # noqa: E402
from polytome.engine import parse_json_lenient  # noqa: E402
from polytome.llm import LLMClient  # noqa: E402
from polytome.workspace import Workspace, split_paragraphs  # noqa: E402

JUDGE_SYS = """Ты — строгий эксперт-редактор художественного перевода с китайского на русский.
Тебе дан фрагмент оригинала (абзацы с номерами) и несколько анонимных вариантов перевода (A, B, C…).
Оцени КАЖДЫЙ вариант по шкале 1–10:
- accuracy — точность: смысл, полнота (ничего не пропущено/не добавлено), числа, пол персонажей, имена единообразны;
- fluency — естественность русского языка: нет кальки, канцелярита, корявых конструкций, ошибок согласования/падежей;
- style — передача тона, юмора, сленга, ритма; оформление диалогов по русским правилам.
Используй всю шкалу; различай варианты. Для каждого варианта укажи до 3 самых серьёзных ошибок (с номером абзаца).
Ответ — только JSON:
{"scores": {"A": {"accuracy": n, "fluency": n, "style": n, "errors": ["[id] ..."]}, "B": {...}}}"""


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    window = 15
    if "--window" in sys.argv:
        window = int(sys.argv[sys.argv.index("--window") + 1])
    ws_path, chapter, judges, files = args[0], int(args[1]), args[2].split(","), args[3:]
    ws = Workspace(ws_path)
    src = ws.source_paragraphs(chapter)
    cands = {}
    for f in files:
        paras = split_paragraphs(Path(f).read_text(encoding="utf-8-sig"))
        name = Path(f).stem.split("__")[-1]
        if len(paras) != len(src):
            print(f"! {name}: {len(paras)} абзацев вместо {len(src)} — выравниваю (Gale–Church)")
            paras = align(src, paras)
        cands[name] = paras
    names = list(cands)
    cfg = Config.load()
    llm = LLMClient(12)
    rng = random.Random(chapter)
    jobs = []
    for start in range(0, len(src), window):
        ids = list(range(start, min(len(src), start + window)))
        for j in judges:
            order = names[:]
            rng.shuffle(order)
            jobs.append((j, ids, order))

    def run(job):
        j, ids, order = job
        letters = [chr(ord("A") + i) for i in range(len(order))]
        body = ["ОРИГИНАЛ:"] + [f"[{i + 1}] {src[i]}" for i in ids]
        for L, name in zip(letters, order):
            body.append(f"\nВАРИАНТ {L}:")
            body += [f"[{i + 1}] {cands[name][i] if i < len(cands[name]) else '[НЕТ]'}" for i in ids]
        try:
            res = llm.chat(cfg.resolve(j), [{"role": "system", "content": JUDGE_SYS},
                                            {"role": "user", "content": "\n".join(body)}],
                           temperature=0.1, json_mode=True, max_tokens=16000, role="judge")
            data = (parse_json_lenient(res.content) or {}).get("scores", {})
        except Exception as e:  # noqa: BLE001
            print("judge error", j, e)
            return []
        out = []
        for L, name in zip(letters, order):
            s = data.get(L)
            if isinstance(s, dict):
                out.append({"judge": j, "cand": name, "window": ids[0] + 1,
                            **{k: float(s.get(k, 0) or 0) for k in ("accuracy", "fluency", "style")},
                            "errors": s.get("errors", [])})
        return out

    with ThreadPoolExecutor(12) as ex:
        rows = [r for rs in ex.map(run, jobs) for r in rs]
    table = {}
    for n in names:
        mine = [r for r in rows if r["cand"] == n]
        noself = [r for r in mine if r["judge"].split(":", 1)[1].split(":")[0] not in n]
        def avg(rs, k):
            return round(statistics.mean(r[k] for r in rs), 2) if rs else None
        table[n] = {k: avg(mine, k) for k in ("accuracy", "fluency", "style")}
        table[n]["total"] = round(sum(v for v in table[n].values() if v) / 3, 2)
        table[n]["total_no_self"] = round(sum(avg(noself, k) or 0 for k in ("accuracy", "fluency", "style")) / 3, 2) \
            if noself else None
        table[n]["n"] = len(mine)
    ranked = sorted(table.items(), key=lambda kv: -(kv[1]["total"] or 0))
    print(f"{'кандидат':34} {'точн':>5} {'язык':>5} {'стиль':>5} {'итог':>5} {'без_себя':>8}  n")
    for n, t in ranked:
        print(f"{n:34} {t['accuracy']:>5} {t['fluency']:>5} {t['style']:>5} {t['total']:>5} {str(t['total_no_self']):>8}  {t['n']}")
    out = ROOT / "eval" / "out" / f"judge_ch{chapter}_{'_'.join(Path(f).stem.split('__')[-1][:6] for f in files)[:60]}.json"
    out.write_text(json.dumps({"table": table, "rows": rows, "judges": judges}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("→", out)


if __name__ == "__main__":
    main()
