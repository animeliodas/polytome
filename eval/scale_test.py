"""Нагрузочный тест без LLM: роман на 913 глав + 159 глав перевода + глоссарий ~1000 записей.

Ищет операции, которые деградируют с размером книги (квадратичные проходы, лишние чтения диска).
"""
import os
import json
import random
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from hanru.checks import check_chapter, gender_conflicts, name_variants_report  # noqa: E402
from hanru.config import Config  # noqa: E402
from hanru.engine import Engine  # noqa: E402
from hanru.glossary import Glossary  # noqa: E402
from hanru.llm import LLMClient  # noqa: E402
from hanru.palladius import transcribe  # noqa: E402
from hanru.tools import Toolbox  # noqa: E402
from hanru.workspace import Workspace  # noqa: E402

SRC = Path(os.environ.get("HANRU_EVAL_BOOK") or Path.home() / "Desktop/Первод книг/Книги/Бешеная жизнь богача")
BOOK = ROOT / "books" / "long"


def timed(label, fn):
    t = time.time()
    r = fn()
    print(f"{label:48} {time.time() - t:7.2f} с")
    return r


def main():
    if not (BOOK / "Оригинал").is_dir():
        shutil.copytree(SRC / "Оригинал", BOOK / "Оригинал")
        shutil.copytree(SRC / "Перевод", BOOK / "Перевод")
    ws = Workspace(BOOK)
    ws.meta["title"] = "long"
    ws.init()
    gl = Glossary(ws.glossary_path)
    gl.entries = json.loads((ROOT / "books/rich_agent/.hanru/glossary.json").read_text(encoding="utf-8"))
    # раздуваем глоссарий реальными 2–3-символьными сочетаниями из текста (как растёт глоссарий длинной книги)
    rng = random.Random(1)
    text = "".join(ws.source_paragraphs(n)[3] for n in list(ws.chapters())[:900] if len(ws.source_paragraphs(n)) > 3)
    cands = {text[i:i + rng.choice((2, 3))] for i in rng.sample(range(len(text) - 3), 4000)}
    for c in list(cands)[:900]:
        if all("一" <= ch <= "鿿" for ch in c):
            gl.upsert(c, transcribe(c, "person").get("target") or transcribe(c, "other").get("as_word", c),
                      "person", rng.choice(["m", "f"]), status="confirmed")
    gl.save()
    print(f"книга: {len(ws.chapters())} глав, перевод: {sum(1 for n in ws.chapters() if ws.is_translated(n))}, "
          f"глоссарий: {len(gl.entries)}")
    eng = Engine(ws, Config.load(), LLMClient(1))
    tb = Toolbox(eng)
    timed("chapters() ×1000 (кэш)", lambda: [ws.chapters() for _ in range(1000)])
    timed("source_paragraphs всех 913 глав (холодно)", lambda: [ws.source_paragraphs(n) for n in ws.chapters()])
    timed("source_paragraphs всех 913 глав (кэш)", lambda: [ws.source_paragraphs(n) for n in ws.chapters()])
    timed("workspace_status", lambda: tb.call("workspace_status", {}))
    timed("list_chapters all", lambda: tb.call("list_chapters", {"chapters": "all"}))
    timed("estimate (913 глав)", lambda: eng.estimate(list(ws.chapters())))
    paras = [p for n in ws.chapters() for p in ws.source_paragraphs(n)]
    timed(f"verify_genders ({len(paras)} абз., {sum(1 for e in gl.entries if e.get('kind') == 'person')} перс.)",
          lambda: gender_conflicts(gl, paras))
    big = "\n".join(ws.source_paragraphs(500))
    timed("glossary.relevant(глава) ×50", lambda: [gl.relevant(big) for _ in range(50)])
    st = ws.load_state(10)
    timed("check_chapter (1 глава, импорт Hermes)", lambda: check_chapter(st, gl))
    timed("check 1-160 через инструмент", lambda: tb.call("check", {"chapters": "1-160"}))
    texts = {n: ws.target_path(n).read_text(encoding="utf-8-sig") for n in ws.chapters() if ws.is_translated(n)}
    timed("name_consistency (159 глав)", lambda: name_variants_report(texts, gl))
    timed("search по оригиналу 913 глав", lambda: tb.call("search", {"query": "黎小白", "scope": "source"}))


if __name__ == "__main__":
    main()
