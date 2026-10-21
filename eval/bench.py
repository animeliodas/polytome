"""Бенчмарк моделей-переводчиков: одна глава, одинаковые условия (глоссарий имён, без библии).

python eval/bench.py <workspace> <chapter> <model1,model2,...> [--review]
Результат: eval/out/ch<N>__<model>.txt + eval/out/bench_ch<N>.json (время, токены).
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from polytome.config import Config  # noqa: E402
from polytome.engine import Engine  # noqa: E402
from polytome.llm import LLMClient  # noqa: E402
from polytome.workspace import Workspace  # noqa: E402

ws_path, chapter, models = sys.argv[1], int(sys.argv[2]), sys.argv[3].split(",")
review = "--review" in sys.argv
TAG = __import__("os").environ.get("BENCH_TAG", "")
out_dir = ROOT / "eval" / "out"
out_dir.mkdir(parents=True, exist_ok=True)
ws = Workspace(ws_path)


def run(model: str):
    cfg = Config.load()
    label = model
    if "@" in model:  # модель@low — reasoning_effort для эксперимента
        model, effort = model.split("@", 1)
        cfg.data.setdefault("models", {})[model] = {"extra": {"reasoning_effort": effort}}
    cfg.data.setdefault("roles", {})["translator"] = [model]
    llm = LLMClient(8)
    eng = Engine(ws, cfg, llm)
    eng.cfg = cfg
    t0 = time.time()
    segs = [{"id": i + 1, "src": p, "tr": None, "history": []} for i, p in enumerate(ws.source_paragraphs(chapter))]
    entries = eng.gl.relevant("\n".join(s["src"] for s in segs))
    try:
        batches = eng._batches(segs)
        for bi, b in enumerate(batches):
            first = segs.index(b[0])
            seam = [s for s in segs[max(0, first - eng.seam):first] if s.get("tr")]
            look = batches[bi + 1][:2] if bi + 1 < len(batches) else []
            eng._fill(chapter, b, seam, look, entries)
        t_tr = time.time() - t0
        eng._repair(chapter, segs, entries)
        fixes = eng._review(chapter, segs) if review else []
    except Exception as e:  # noqa: BLE001
        return {"model": model, "error": str(e)}
    name = label.split(":", 1)[1].replace(":", "_").replace("@", "-")
    text = "\n\n".join(s.get("tr") or "[ПРОПУСК]" for s in segs)
    (out_dir / f"{TAG}ch{chapter}__{name}{'_rev' if review else ''}.txt").write_text(text, encoding="utf-8")
    return {"model": label, "seconds_translate": round(t_tr, 1), "seconds_total": round(time.time() - t0, 1),
            "missing": sum(1 for s in segs if not s.get("tr")), "repairs": sum(1 for s in segs if s.get("by") == "repair"),
            "review_fixes": len(fixes), "usage": llm.usage.by_role}


log = out_dir / f"{TAG}bench_ch{chapter}{'_rev' if review else ''}.jsonl"


def run_and_log(model):
    r = run(model)
    line = json.dumps(r, ensure_ascii=False)
    print(line, flush=True)
    with open(log, "a", encoding="utf-8") as f:  # пишем сразу: зависшая модель не съест результаты остальных
        f.write(line + "\n")
    return r


with ThreadPoolExecutor(len(models)) as ex:
    list(ex.map(run_and_log, models))
