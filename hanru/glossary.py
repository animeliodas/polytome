"""Глоссарий книги: имена, топонимы, термины. Не подсказка, а контракт — проверяется кодом."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

KINDS = ("person", "place", "org", "term", "title", "item", "skill", "other")
NAME_KINDS = ("person", "place", "org")
_END = "аяоеёуюыиьйъэ"


def word_stem(w: str) -> str:
    """Основа для сравнения склоняемых форм: Синья→Синь, Сяобай→Сяоба, Цинчжоу→Цинчжо."""
    w = w.strip()
    if len(w) >= 5 and w[-2:].lower() in ("ок", "ёк", "ек", "ец", "ел"):
        return w[:-2]  # беглая гласная в прозвищах: Котёнок → Котёнка, Хитрец → Хитреца
    if len(w) >= 3 and w[-1].lower() in "ьй":
        return w[:-1]  # Янь → Ян (Яня, Яню, Янем); было: короткие имена не склонялись → ложные «варианты»
    if len(w) > 3 and w[-1].lower() in _END:
        return w[:-1]
    return w


class Glossary:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.entries: list[dict] = []
        from .workspace import read_json
        self.entries = read_json(path, []) or []

    def save(self):
        with self.lock:
            from .workspace import atomic_write
            self.entries.sort(key=lambda e: (-len(e["source"]), e["source"]))
            atomic_write(self.path, json.dumps(self.entries, ensure_ascii=False, indent=1))

    def get(self, source: str) -> dict | None:
        return next((e for e in self.entries if e["source"] == source), None)

    def upsert(self, source: str, target: str, kind: str = "other", gender: str | None = None,
               note: str | None = None, status: str = "confirmed", aliases: list[str] | None = None) -> dict:
        with self.lock:
            e = self.get(source)
            if e is None:
                e = {"source": source}
                self.entries.append(e)
            e["target"] = target.strip()
            e["kind"] = kind if kind in KINDS else "other"
            if gender:
                e["gender"] = gender
            if note is not None:
                e["note"] = note
            if aliases is not None:
                e["aliases"] = aliases
            if gender:
                e.pop("gender_conflict", None)  # пол задан заново — пусть verify_genders проверит ещё раз
            e["status"] = status
            return e

    def remove(self, source: str) -> bool:
        with self.lock:
            n = len(self.entries)
            self.entries = [e for e in self.entries if e["source"] != source]
            return len(self.entries) != n

    def relevant(self, text: str) -> list[dict]:
        """Термины, встречающиеся в тексте. Длинные первыми; вложенные короткие (楊 внутри 楊心雅) не дублируем."""
        found = []
        covered: list[tuple[int, int]] = []
        for e in sorted(self.entries, key=lambda e: -len(e["source"])):
            if e.get("status") == "candidate":
                continue
            src = e["source"]
            idx = text.find(src)
            if idx < 0:
                continue
            spans = [(m.start(), m.end()) for m in re.finditer(re.escape(src), text)]
            if all(any(a <= s and t <= b for a, b in covered) for s, t in spans):
                continue  # все вхождения внутри более длинных терминов
            covered.extend(spans)
            found.append(e)
        return found

    @staticmethod
    def format_for_prompt(entries: list[dict]) -> str:
        lines = []
        g = {"m": "муж.", "f": "жен.", "n": "—"}
        for e in entries:
            extra = []
            if e.get("gender_conflict"):
                extra.append("пол определяй по оригиналу (他/她)")
            elif e.get("gender") in g and e["gender"] != "n":
                extra.append(g[e["gender"]])
            if e.get("note") and not e.get("gender_conflict"):  # заметка тоже может нести неверный пол
                extra.append(e["note"])
            lines.append(f"{e['source']} → {e['target']}" + (f" ({'; '.join(extra)})" if extra else ""))
        return "\n".join(lines)

    def stats(self) -> dict:
        kinds: dict[str, int] = {}
        for e in self.entries:
            kinds[e.get("kind", "other")] = kinds.get(e.get("kind", "other"), 0) + 1
        return {"total": len(self.entries), "by_kind": kinds,
                "auto_unreviewed": sum(1 for e in self.entries if e.get("status") == "auto")}
