"""Собрать сайт таблицы моделей для GitHub Pages: python bench/site/build.py <папка>

В папку попадают статика (index.html, app.js, style.css), index.json — сводка всех прогонов текущего набора, тексты
набора и ловушки, — и сами файлы результатов (results/<пара>/*.json): перевод сайт подгружает по клику.
Сайт статический, без сервера и ключей; GitHub Actions пересобирает его при каждом новом результате.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from polytome import __version__  # noqa: E402
from polytome import bench, bench_suite as suite  # noqa: E402
from polytome.lang import get as lang  # noqa: E402

STATIC = ("index.html", "app.js", "style.css")


def build(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    for name in STATIC:
        shutil.copy(HERE / name, out / name)
    sv = suite.suite_version()
    runs = []
    for f in sorted((ROOT / "bench" / "results").glob("*/*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        if r.get("schema") != bench.SCHEMA or r.get("suite") != sv or r.get("error") not in (None, "budget"):
            continue
        rel = f"results/{f.parent.name}/{f.name}"
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, out / rel)
        runs.append({
            "pair": r["pair"], "model": r["model"], "provider": r.get("provider"), "date": r["date"],
            "budget": r.get("error") == "budget", "score": r.get("score"), "first": r.get("first_pass_ok"),
            "final": r.get("final_ok"), "traps": r.get("traps_ok"),
            "failed": [x["id"] for x in r.get("traps_failed") or []], "issues": r.get("issues_final") or {},
            "seconds": r.get("seconds"), "tokens": (r.get("tokens") or {}).get("prompt", 0) + (r.get("tokens") or {}).get("completion", 0),
            "sec1k": r.get("sec_per_1k_chars"), "tok1k": r.get("tokens_per_1k_chars"),
            "cost1m": r.get("cost_per_1m_chars_usd"), "file": rel,
        })
    codes = sorted({c for p in suite.PAIRS for c in p.split("-")}, key=lambda c: (suite.SOURCES + suite.TARGETS).index(c))
    data = {
        "suite": sv, "polytome": __version__, "generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "budget": bench.BUDGET, "pairs": list(suite.PAIRS),
        "langs": {c: {"en": lang(c).name, "native": lang(c).native, "ru": _RU.get(c, lang(c).name)} for c in codes},
        "texts": {src: suite.chapters(src) for src in suite.SOURCES},
        "traps": {p: suite.traps(*p.split("-")) for p in suite.PAIRS},
        "runs": runs,
    }
    (out / "index.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # GitHub Pages: отдавать файлы как есть
    return data


_RU = {"zh": "китайский", "ja": "японский", "ko": "корейский", "en": "английский", "fr": "французский",
       "de": "немецкий", "es": "испанский", "ru": "русский"}

if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "_site")
    d = build(target)
    print(f"{target}: {len(d['runs'])} runs, suite {d['suite']}")
