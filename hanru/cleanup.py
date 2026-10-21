"""Очистка книги от того, что не является текстом романа.

- служебные строки сайтов, повторяющиеся почти в каждой главе («Нет главы и т.п. - пиши в Комменты…»);
- заметки автора в конце главы: хвост после разделителя «————», «ps: …», благодарности донатерам,
  просьбы о месячных билетах, «Продолжайте писать» (и китайские 求月票 / 感谢…打赏 / PS：);
- плюс правила пользователя (.hanru/cleanup.json): точные строки и регулярные выражения.

Работает и с оригиналом (до перевода — тогда мусор не переводится и не стоит токенов), и с готовым переводом.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from collections import Counter
from pathlib import Path

from .workspace import Workspace, atomic_write, read_json, split_paragraphs

SEPARATOR = re.compile(r"^[\s—\-–_=*~·•。.]{4,}$")
AUTHOR_NOTE = [
    r"^(ps|пс|p\.\s?s\.?|п\.\s?с\.?|ps\d?)\s*[:：.,]",          # ps: / пс: / P.S.
    r"^(ps|пс|p\.s\.?)$",
    r"спасибо[^.!?]{0,120}(наград|абонемент|подписк|донат|голос|чаевы|подар|поддержк|билет|лидер|пожертв)",
    r"благодар[^.!?]{0,120}(наград|абонемент|подписк|донат|голос|подар|поддержк|билет|лидер|пожертв)",
    r"месячн\w*\s+(абонемент|билет|голос)", r"голос\w*\s+(за книгу|месячн)", r"(закажите|подпишитесь|оформите подписку)",
    r"продолж(айте|аю|у|ай|им|ать)\s+писать", r"(обновлени[ея]|глав[аы]) (будет|будут) (позже|завтра|вечером)",
    r"^\(?(третье|второе|первое|четвёртое|двойное) обновление\)?",
    r"求(月票|订阅|推荐票?|收藏|打赏)", r"感谢.{0,40}(打赏|月票|盟主|推荐|订阅)", r"^(PS|ps|P\.S)[:：]", r"作者的话",
    r"(本章完|未完待续)",
]
_NOTE = [re.compile(p, re.I) for p in AUTHOR_NOTE]


def is_author_note(line: str) -> bool:
    return any(rx.search(line) for rx in _NOTE)


def rules_path(ws: Workspace) -> Path:
    return ws.meta_dir / "cleanup.json"


def load_rules(ws: Workspace) -> dict:
    r = read_json(rules_path(ws), None) or {}
    r.setdefault("drop_lines", [])        # точные строки
    r.setdefault("drop_regex", [])        # свои регулярные выражения (строка целиком или часть)
    r.setdefault("author_tail", True)     # хвост после разделителя / ps / благодарности
    r.setdefault("repeated_share", 0.3)   # строка в ≥30% глав и длиной ≥20 — служебная
    return r


def save_rules(ws: Workspace, rules: dict):
    atomic_write(rules_path(ws), json.dumps(rules, ensure_ascii=False, indent=1))


def _chapter_files(ws: Workspace, target: str) -> dict[int, Path]:
    if target == "source":
        return ws.chapters()
    return {n: ws.target_path(n) for n in ws.chapters() if ws.is_translated(n)}


def detect(ws: Workspace, target: str = "translation", rules: dict | None = None) -> dict:
    """Найти мусор (без изменений): повторяющиеся служебные строки, хвосты автора, отдельные заметки."""
    rules = rules or load_rules(ws)
    files = _chapter_files(ws, target)
    chapters = {n: split_paragraphs(p.read_text(encoding="utf-8-sig", errors="replace")) for n, p in files.items()}
    freq = Counter()
    for paras in chapters.values():
        freq.update(set(paras))
    total = max(1, len(chapters))
    repeated = [(line, c) for line, c in freq.most_common(200)
                if c / total >= rules["repeated_share"] and len(line) >= 20 and line not in rules["drop_lines"]]
    plan = {n: _plan_chapter(paras, rules, {l for l, _ in repeated}) for n, paras in chapters.items()}
    removed = {n: p for n, p in plan.items() if p["drop"]}
    examples = []
    for n, p in list(removed.items())[:400]:
        for i in p["drop"]:
            if chapters[n][i] not in {l for l, _ in repeated} and len(examples) < 12:
                examples.append(f"гл.{n}: {chapters[n][i][:110]}")
    return {"target": target, "chapters": total, "repeated_lines": [{"line": l, "chapters": c} for l, c in repeated],
            "chapters_affected": len(removed), "paragraphs_to_remove": sum(len(p["drop"]) for p in removed.values()),
            "author_tails": sum(1 for p in removed.values() if p["tail"]), "examples": examples, "_plan": plan,
            "_chapters": chapters}


def _plan_chapter(paras: list[str], rules: dict, repeated: set[str]) -> dict:
    drop: set[int] = set()
    custom = [re.compile(x, re.I) for x in rules.get("drop_regex", [])]
    exact = set(rules.get("drop_lines", [])) | repeated
    for i, p in enumerate(paras):
        if i == 0:
            continue  # заголовок главы не трогаем
        if p in exact or any(rx.search(p) for rx in custom):
            drop.add(i)
    tail = False
    if rules.get("author_tail", True) and len(paras) > 3:
        n = len(paras)
        # 1) хвост после последнего разделителя, если в нём есть признаки заметки автора
        seps = [i for i in range(max(1, n - 15), n) if SEPARATOR.match(paras[i])]
        if seps:
            s = seps[-1]
            rest = paras[s + 1:]
            if not rest or any(is_author_note(x) for x in rest[:6]):
                drop.update(range(s, n))
                tail = True
        # 2) заметки автора в самом конце без разделителя («Продолжайте писать», «пс: …», благодарности)
        k = n - 1
        while k > 0 and k not in drop and (is_author_note(paras[k]) or (paras[k] in ("!", "！", "?") and k - 1 in drop)):
            drop.add(k)
            tail = True
            k -= 1
        # 3) одиночный разделитель, оставшийся последней строкой
        while n - 1 > 0 and (n - 1 not in drop) and SEPARATOR.match(paras[n - 1]) and all(
                j in drop for j in range(n, len(paras))):
            drop.add(n - 1)
            n -= 1
    return {"drop": sorted(drop), "tail": tail}


def apply(ws: Workspace, target: str = "translation", rules: dict | None = None, backup: bool = True) -> dict:
    """Применить очистку; повторяется, пока что-то находится (длинный хвост виден целиком только после первого
    прохода), максимум 3 раза. Резервная копия — один раз, до первого изменения."""
    total = None
    for i in range(3):
        rep = _apply_once(ws, target, rules, backup and i == 0)
        if total is None:
            total = rep
        else:
            total["chapters_changed"] += rep["chapters_changed"]
            total["paragraphs_to_remove"] += rep["paragraphs_to_remove"]
            total["author_tails"] += rep["author_tails"]
        if not rep["chapters_changed"]:
            break
    return total


def _apply_once(ws: Workspace, target: str, rules: dict | None, backup: bool) -> dict:
    rep = detect(ws, target, rules)
    files = _chapter_files(ws, target)
    folder = ws.source_dir if target == "source" else ws.target_dir
    bk = None
    if backup and rep["chapters_affected"]:
        bk = folder.parent / f"{folder.name}_до_очистки_{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copytree(folder, bk)
    changed = 0
    for n, p in rep["_plan"].items():
        if not p["drop"]:
            continue
        paras = [x for i, x in enumerate(rep["_chapters"][n]) if i not in set(p["drop"])]
        atomic_write(files[n], "\n\n".join(paras) if target == "translation" else "\n".join(paras),
                     encoding="utf-8-sig")
        if target == "translation":
            st = ws.load_state(n)
            if st and not st.get("imported"):
                # посегментное состояние: убранные абзацы помечаем, чтобы проверки не считали их пропуском
                keep = set(paras)
                for s in st["segments"]:
                    if s.get("tr") and s["tr"] not in keep:
                        s["dropped"] = True
                ws.save_state(n, st, write_text=False)
        changed += 1
    out = {k: v for k, v in rep.items() if not k.startswith("_")}
    out.update({"chapters_changed": changed, "backup": str(bk) if bk else None})
    return out
