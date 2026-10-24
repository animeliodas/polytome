"""Замеры моделей на переводе (polytome bench): одинаковый набор текстов, качество — проверками кода.

Модель переводит набор (bench_suite) тем же конвейером, что и книги: батчи → починка. Качество меряется не другой
моделью «на глаз», а детерминированными проверками Polytome: остатки оригинала, имена по глоссарию, род персонажей,
числа, пропуски, повторы. Считаются:
- first_pass_ok — доля абзацев без серьёзных замечаний сразу после перевода (насколько модель понимает задачу);
- final_ok — после починки (что получит читатель);
- score — среднее этих двух (0–100);
- скорость, токены и цена (если провайдер сообщает цены) — на 1000 знаков оригинала.

Результат — JSON без ключей и адресов серверов: его можно прислать в общий репозиторий (bench/results), из всех
результатов собирается таблица bench/LEADERBOARD.md.
"""
from __future__ import annotations

import json
import re
import shutil
import statistics
import threading
import time
import uuid
from pathlib import Path

from . import __version__
from . import bench_suite as suite
from .checks import check_segment, chapter_level_issues, glossary_stems
from .config import ROLES, Config
from .engine import Engine
from .glossary import Glossary
from .i18n import _
from .llm import LLMClient, LLMError
from .workspace import Workspace, atomic_write

SCHEMA = 1

# Жёсткий лимит токенов на один прогон пары. Обычный прогон — 3–50 тыс.; модель, которой нужно больше, для перевода
# книг слишком расточительна (маленькие «рассуждающие» модели теряют абзацы, и переспросы с длинными рассуждениями
# съедали сотни тысяч токенов). Лимит проверяется перед каждым запросом: перерасход — не больше одного запроса.
BUDGET = 100_000


class BudgetExceeded(LLMError):
    pass


def _pct(ok: int, total: int) -> float:
    return round(100.0 * ok / total, 1) if total else 0.0


def _source_chars(src: str) -> int:
    return sum(len(re.sub(r"\s", "", p)) for ch in suite.chapters(src) for p in ch)


def info() -> dict:
    """Что измеряется: пары, размер набора и грубая оценка токенов на одну пару."""
    out = []
    for pair in suite.PAIRS:
        src = pair.split("-")[0]
        chars = _source_chars(src)
        segs = sum(len(ch) for ch in suite.chapters(src))
        # системный промпт ~3–4 тыс. токенов на запрос; запросов ~ по одному на главу + починка
        out.append({"pair": pair, "chapters": len(suite.chapters(src)), "segments": segs, "source_chars": chars,
                    "tokens_estimate": len(suite.chapters(src)) * 3 * 4000 + chars * (3 if src == "zh" else 1)})
    return {"suite": suite.suite_version(), "budget": BUDGET, "pairs": out}


def run(cfg: Config, model: str, pair: str, home: Path, on_event=None, cancel: threading.Event | None = None,
        keep: bool = False, llm: LLMClient | None = None, budget: int | None = BUDGET) -> dict:
    """Перевести набор пары моделью model («провайдер:модель») и оценить. Ничего не пишет в библиотеку:
    рабочая папка временная (keep=True — оставить для разбора). budget — лимит токенов на прогон (None — без
    лимита): превысила — прогон останавливается, результат с error="budget"."""
    if pair not in suite.PAIRS:
        raise ValueError(_("Нет пары {0} в наборе. Есть: {1}").format(pair, ", ".join(suite.PAIRS)))
    src, tgt = pair.split("-")
    emit = on_event or (lambda e: None)
    cfg = cfg.with_overrides(roles={r: model for r in ROLES})
    ref = cfg.role("translator")[0]
    llm = llm or LLMClient(cfg.parallel)
    cancel = cancel or threading.Event()
    over_budget = threading.Event()
    if budget:
        plain_chat = llm.chat

        def chat_within_budget(*a, **kw):
            if llm.usage.total() >= budget:
                over_budget.set()
                cancel.set()  # остановить и циклы движка, а не только этот запрос
                raise BudgetExceeded(_("лимит {0} токенов на прогон исчерпан").format(budget))
            return plain_chat(*a, **kw)

        llm.chat = chat_within_budget

    # своя папка на каждый прогон: параллельные прогоны в одну секунду не мешают друг другу
    root = home / ".bench" / f"{pair}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    from .i18n import chapter_file, folder_names
    src_dir, tgt_dir = folder_names()
    ws = Workspace(root)
    ws.meta.update({"title": f"bench {pair}", "original_title": f"bench {pair}", "source_lang": src,
                    "target_lang": tgt, "source_dir": src_dir, "target_dir": tgt_dir, "chapter_file": chapter_file()})
    ws.init()
    chs = suite.chapters(src)
    for i, paras in enumerate(chs, 1):
        atomic_write(ws.source_dir / ws.meta["chapter_file"].format(n=i), "\n".join(paras), durable=False)
    gl = Glossary(ws.glossary_path)
    for e in suite.glossary(src, tgt):
        gl.upsert(e["source"], e["target"], e["kind"], gender=e.get("gender"), status="confirmed")
    gl.save()

    eng = Engine(ws, cfg, llm, on_event=lambda e: emit({**e, "bench": True}))
    eng.cancel = cancel
    t0 = time.time()
    total = first_ok = final_ok = missing = 0
    issues_first: dict[str, int] = {}
    issues_final: dict[str, int] = {}
    translation: dict[str, list[str]] = {}
    error = None
    try:
        stems = glossary_stems(eng.gl, eng.pair)
        for n in range(1, len(chs) + 1):
            if eng.cancel.is_set():
                error = "cancelled"
                break
            emit({"ev": "bench_progress", "stage": "translate", "chapter": n, "chapters": len(chs)})
            segs = [{"id": i + 1, "src": p, "tr": None, "history": []} for i, p in enumerate(ws.source_paragraphs(n))]
            entries = eng.gl.relevant("\n".join(s["src"] for s in segs))
            batches = eng._batches(segs)
            for bi, batch in enumerate(batches):
                first = segs.index(batch[0])
                seam = [s for s in segs[max(0, first - eng.seam):first] if s.get("tr")]
                look = batches[bi + 1][:eng.lookahead] if bi + 1 < len(batches) else []
                eng._fill(n, batch, seam, look, entries)
            eng._normalize(segs)
            missing += sum(1 for s in segs if not s.get("tr"))
            hard_first = _hard_by_segment(segs, eng, stems)
            emit({"ev": "bench_progress", "stage": "repair", "chapter": n, "chapters": len(chs)})
            eng._repair(n, segs, entries)
            eng._normalize(segs)
            hard_final = _hard_by_segment(segs, eng, stems)
            total += len(segs)
            first_ok += sum(1 for s in segs if not hard_first.get(s["id"]))
            final_ok += sum(1 for s in segs if not hard_final.get(s["id"]))
            for per, acc in ((hard_first, issues_first), (hard_final, issues_final)):
                for types in per.values():
                    for t in types:
                        acc[t] = acc.get(t, 0) + 1
            translation[str(n)] = [s.get("tr") or "" for s in segs]
        if eng.cancel.is_set():  # отменили посреди главы: починка не прошла — неполный результат не выдавать
            error = "cancelled"
    except Exception as e:  # noqa: BLE001 — провайдер недоступен, модель не существует: это тоже результат
        error = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        if over_budget.is_set() or (budget and llm.usage.total() > budget):  # и если перевалила последним запросом
            error = "budget"
        if budget:
            llm.chat = plain_chat
        if not keep:
            shutil.rmtree(root, ignore_errors=True)

    seconds = round(time.time() - t0, 1)
    with llm.usage.lock:
        prompt = sum(r["prompt"] for r in llm.usage.by_role.values())
        completion = sum(r["completion"] for r in llm.usage.by_role.values())
        calls = sum(r["calls"] for r in llm.usage.by_role.values())
    chars = _source_chars(src)
    price = _price(llm, cfg, ref)
    cost = round(prompt * price["in"] / 1e6 + completion * price["out"] / 1e6, 5) if price else None
    k = chars / 1000 if chars else 1
    rec = {
        "schema": SCHEMA, "suite": suite.suite_version(), "polytome": __version__,
        "date": time.strftime("%Y-%m-%d"), "model": f"{ref.provider}:{ref.model}", "provider": ref.provider,
        "pair": pair, "segments": total, "source_chars": chars,
        "first_pass_ok": _pct(first_ok, total), "final_ok": _pct(final_ok, total),
        "score": round((_pct(first_ok, total) + _pct(final_ok, total)) / 2, 1) if total else 0.0,
        "missing_after_draft": missing, "issues_first": issues_first, "issues_final": issues_final,
        "calls": calls, "seconds": seconds, "sec_per_1k_chars": round(seconds / k, 1),
        "tokens": {"prompt": prompt, "completion": completion},
        "tokens_per_1k_chars": round((prompt + completion) / k),
        "price_per_1m_tokens": price, "cost_usd": cost,
        "cost_per_1m_chars_usd": round(cost / chars * 1e6, 2) if cost is not None and chars else None,
        "translation": translation,
    }
    if error:
        rec["error"] = error
        if error == "budget":
            rec["budget"] = budget
    emit({"ev": "bench_done", "result": {k2: v for k2, v in rec.items() if k2 != "translation"}})
    return rec


# При обычном переводе это мягкие замечания (имя законно заменить местоимением), но в замерах тексты известны:
# пропавшее число или имя, которое в абзаце оригинала есть, — ошибка модели.
SERIOUS_SOFT = {"number", "name_missing"}


def _hard_by_segment(segs: list[dict], eng: Engine, stems: set[str]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for s in segs:
        types = [i["type"] for i in check_segment(s["src"], s.get("tr"), eng.gl.relevant(s["src"]), stems, eng.gl,
                                                  eng.pair)
                 if i["level"] == "hard" or i["type"] in SERIOUS_SOFT]
        if s.get("tr") and not eng.pair.cjk_to_alphabetic:
            # для пар без иероглифов движок длину абзаца не проверяет — здесь грубо: вдвое короче или длиннее
            a, b = len(re.sub(r"\s", "", s["src"])), len(re.sub(r"\s", "", s["tr"]))
            if a >= 40 and (b < a * 0.5 or b > a * 2.5):
                types.append("short" if b < a * 0.5 else "long")
        if types:
            out[s["id"]] = types
    for sid, iss in chapter_level_issues(segs).items():
        hard = [i["type"] for i in iss if i["level"] == "hard"]
        if hard:
            out.setdefault(sid, []).extend(hard)
    return out


def _price(llm: LLMClient, cfg: Config, ref) -> dict | None:
    """Цена за 1 млн токенов из списка моделей провайдера (OpenRouter и др. её сообщают); нет — None."""
    try:
        for m in llm.list_models_info(cfg.resolve(f"{ref.provider}:_")):
            if m["id"] == ref.model and m.get("price_in") is not None and m.get("price_out") is not None:
                if m["price_in"] >= 0 and m["price_out"] >= 0:
                    return {"in": m["price_in"], "out": m["price_out"]}
    except Exception:  # noqa: BLE001 — цена необязательна
        pass
    return None


# ------------------------------------------------------------------ результаты и таблица
def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")[:80] or "model"


def save(rec: dict, results_dir: Path) -> Path:
    """bench/results/<пара>/<дата>_<модель>.json — отдельный файл на прогон: присланные результаты не конфликтуют."""
    d = Path(results_dir) / rec["pair"]
    d.mkdir(parents=True, exist_ok=True)
    base = f"{rec['date']}_{slug(rec['model'])}"
    p = d / f"{base}.json"
    i = 2
    while p.exists():
        p = d / f"{base}_{i}.json"
        i += 1
    atomic_write(p, json.dumps(rec, ensure_ascii=False, indent=1))
    return p


def load(results_dir: Path) -> list[dict]:
    out = []
    for p in sorted(Path(results_dir).glob("*/*.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("schema") == SCHEMA and r.get("error") in (None, "budget"):  # «не уложилась в лимит» — тоже итог
            out.append(r)
    return out


def leaderboard(results: list[dict], suite_version: str | None = None) -> str:
    """Таблица Markdown по парам: медиана по прогонам модели; только текущая версия набора."""
    sv = suite_version or suite.suite_version()
    over = [r for r in results if r.get("suite") == sv and r.get("error") == "budget"]
    rows = [r for r in results if r.get("suite") == sv and not r.get("error")]
    lines = ["# Polytome model leaderboard / Таблица моделей", "",
             f"Suite / набор: `{sv}`. Score = average of the paragraphs clean right after translation and after "
             "the code-guided repair (0–100); checks are deterministic (names, gender, numbers, leftovers, omissions). "
             "Median over runs. Add your run: see [README.md](README.md).", "",
             "Score — среднее долей абзацев без серьёзных замечаний сразу после перевода и после починки (0–100); "
             "проверки — кодом (имена, род, числа, остатки оригинала, пропуски). Медиана по прогонам.", "",
             "**Limits / Ограничения.** The checks catch errors code can see — not style or a wrong meaning; strong models "
             "reach 100, and at the top they differ in speed and tokens. Every result file contains the translation: "
             "read it. / Проверки ловят ошибки, видимые коду, — не стиль и не искажённый смысл; сильные модели "
             "получают 100, и наверху они различаются скоростью и токенами. В каждом файле результата есть сам "
             "перевод — его стоит прочитать.", ""]
    for pair in suite.PAIRS:
        by_model: dict[str, list[dict]] = {}
        for r in rows:
            if r["pair"] == pair:
                by_model.setdefault(r["model"], []).append(r)
        wasteful = sorted({r["model"] for r in over if r["pair"] == pair} - set(by_model))
        if not by_model and not wasteful:
            continue
        lines += [f"## {pair}", "",
                  "| Model | Runs | Score | First pass | After repair | Sec / 1k chars | Tokens / 1k chars | $ / 1M chars | Last run |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---|"]

        def med(rs, key):
            vals = [r[key] for r in rs if r.get(key) is not None]
            return statistics.median(vals) if vals else None

        stats = []
        for model, rs in by_model.items():
            stats.append((model, len(rs), med(rs, "score"), med(rs, "first_pass_ok"), med(rs, "final_ok"),
                          med(rs, "sec_per_1k_chars"), med(rs, "tokens_per_1k_chars"), med(rs, "cost_per_1m_chars_usd"),
                          max(r["date"] for r in rs)))
        stats.sort(key=lambda x: (-(x[2] or 0), x[7] if x[7] is not None else 1e9))

        def f(v, fmt):
            return "—" if v is None else fmt.format(v)

        for m, runs, sc, fp, fo, sp, tk, cost, last in stats:
            lines.append(f"| `{m}` | {runs} | {f(sc, '{:.1f}')} | {f(fp, '{:.0f}%')} | {f(fo, '{:.0f}%')} | "
                         f"{f(sp, '{:.1f}')} | {f(tk, '{:.0f}')} | {f(cost, '${:.2f}')} | {last} |")
        lines.append("")
        if wasteful:
            lines += [f"Did not fit the token budget ({BUDGET:,} per run) / Не уложились в лимит токенов: "
                      + ", ".join(f"`{m}`" for m in wasteful), ""]
    if not any(ln.startswith("## ") for ln in lines):
        lines.append("_No results yet / Результатов пока нет._")
    return "\n".join(lines) + "\n"
