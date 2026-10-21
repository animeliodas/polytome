"""Папка книги: оригинал, перевод и служебное состояние .hanru/."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path


def atomic_write(path: Path, text: str, encoding: str = "utf-8"):
    """Запись через временный файл + os.replace: при отключении питания файл либо старый, либо новый,
    но никогда не обрезанный наполовину."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding=encoding, newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(5):  # Windows: файл может быть на миг занят антивирусом/читалкой
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.2 * (attempt + 1))
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    """Чтение JSON; битый файл (старый сбой) откладывается в *.corrupt-*, а не роняет программу."""
    path = Path(path)
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        path.replace(path.with_name(path.name + f".corrupt-{int(time.time())}"))
        return default

META_DIR = ".hanru"          # служебная папка книги
LEGACY_META_DIR = ".codek"   # так она называлась до переименования проекта


def is_book(path: str | Path) -> bool:
    """Папка уже является книгой hanru (в том числе созданной под старым именем)."""
    p = Path(path)
    return (p / META_DIR).is_dir() or (p / LEGACY_META_DIR).is_dir()


SOURCE_DIR_NAMES = ["Оригинал", "Оригенал", "Source", "source", "original", "Original", "src"]
TARGET_DIR_NAMES = ["Перевод", "Translated", "translated", "translation", "Translation", "ru"]

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "萬": 10000}


def cn_to_int(s: str) -> int | None:
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                section = (section + num) * unit
                total += section
                section = 0
            else:
                section += (num or 1) * unit
            num = 0
        else:
            return None
    return total + section + num


def chapter_number(name: str) -> int | None:
    m = re.search(r"(?:Глава|Chapter|ch|гл)\.?\s*(\d+)", name, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"第([0-9零〇一二两兩三四五六七八九十百千万萬]+)[章节回]", name)
    if m:
        return cn_to_int(m.group(1))
    m = re.search(r"(\d+)", name)
    return int(m.group(1)) if m else None


def split_paragraphs(text: str) -> list[str]:
    text = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    return [p.strip().replace("　", " ").strip() for p in text.split("\n") if p.strip()]


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.meta_dir = self.root / META_DIR
        legacy = self.root / LEGACY_META_DIR
        if not self.meta_dir.exists() and legacy.is_dir():  # книга создана, когда проект назывался Codek
            try:
                legacy.rename(self.meta_dir)
            except OSError:
                self.meta_dir = legacy
        self.state_dir = self.meta_dir / "state"
        self._lock = threading.RLock()
        self.meta = self._load_meta()

    # ---------- метаданные ----------
    def _load_meta(self) -> dict:
        meta = read_json(self.meta_dir / "book.json", {}) or {}
        meta.setdefault("title", self.root.name)
        meta.setdefault("source_lang", "zh")
        meta.setdefault("target_lang", "ru")
        if "source_dir" not in meta:
            meta["source_dir"] = next((d for d in SOURCE_DIR_NAMES if (self.root / d).is_dir()), "Оригинал")
        if "target_dir" not in meta:
            meta["target_dir"] = next((d for d in TARGET_DIR_NAMES if (self.root / d).is_dir()), "Перевод")
        return meta

    def save_meta(self):
        atomic_write(self.meta_dir / "book.json", json.dumps(self.meta, ensure_ascii=False, indent=2))

    def init(self):
        for d in (self.meta_dir, self.state_dir, self.source_dir, self.target_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.save_meta()
        if not self.bible_path.exists():
            from .prompts import BIBLE_TEMPLATE
            atomic_write(self.bible_path, BIBLE_TEMPLATE.format(title=self.meta["title"]))

    @property
    def source_dir(self) -> Path:
        return self.root / self.meta["source_dir"]

    @property
    def target_dir(self) -> Path:
        return self.root / self.meta["target_dir"]

    @property
    def bible_path(self) -> Path:
        return self.meta_dir / "bible.md"

    @property
    def glossary_path(self) -> Path:
        return self.meta_dir / "glossary.json"

    def bible(self) -> str:
        return self.bible_path.read_text(encoding="utf-8") if self.bible_path.is_file() else ""

    def write_bible(self, text: str):
        atomic_write(self.bible_path, text)

    # ---------- главы ----------
    def chapters(self) -> dict[int, Path]:
        """Номер → файл оригинала. Кэш по mtime папки: на романе в 900 глав это вызывается тысячи раз."""
        d = self.source_dir
        try:
            mtime = d.stat().st_mtime_ns
        except OSError:
            return {}
        cache = getattr(self, "_ch_cache", None)
        if cache and cache[0] == mtime:
            return cache[1]
        out: dict[int, Path] = {}
        for p in d.iterdir():
            if p.is_file() and p.suffix.lower() in (".txt", ".md"):
                n = chapter_number(p.stem)
                if n is not None and n not in out:
                    out[n] = p
        result = dict(sorted(out.items()))
        self._ch_cache = (mtime, result)
        return result

    def source_paragraphs(self, n: int) -> list[str]:
        p = self.chapters().get(n)
        if p is None:
            raise KeyError(f"Глава {n} не найдена в {self.source_dir}")
        key = (n, p.stat().st_mtime_ns)
        cache = self.__dict__.setdefault("_src_cache", {})
        if key not in cache:
            if len(cache) > 3000:
                cache.clear()
            cache[key] = split_paragraphs(p.read_text(encoding="utf-8-sig", errors="replace"))
        return list(cache[key])

    def target_path(self, n: int) -> Path:
        return self.target_dir / f"Глава {n}.txt"

    def is_translated(self, n: int) -> bool:
        return self.target_path(n).is_file()

    # ---------- посегментное состояние ----------
    def state_path(self, n: int) -> Path:
        return self.state_dir / f"ch{n:05d}.json"

    def load_state(self, n: int) -> dict | None:
        st = read_json(self.state_path(n))
        if st is not None:
            return st
        # Перевод есть, а состояния нет (импорт чужого перевода) — выравниваем по абзацам, если совпадает число.
        if self.is_translated(n):
            src = self.source_paragraphs(n)
            tr = split_paragraphs(self.target_path(n).read_text(encoding="utf-8-sig"))
            from .align import align
            aligned = align(src, tr)
            return {"chapter": n, "imported": True, "auto_aligned": len(src) != len(tr),
                    "segments": [{"id": i + 1, "src": s, "tr": t or "(склеено с предыдущим абзацем)",
                                  "history": []} for i, (s, t) in enumerate(zip(src, aligned))]}
        return None

    def save_state(self, n: int, state: dict, write_text: bool = True):
        with self._lock:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            state["chapter"] = n
            state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            src_joined = "\n".join(s["src"] for s in state["segments"])
            state["source_hash"] = hashlib.sha1(src_joined.encode("utf-8")).hexdigest()[:12]
            final = write_text and all(s.get("tr") for s in state["segments"])
            if final:
                state["stage"] = "done"
                # Сначала текст главы, потом состояние «done»: сбой между ними оставит состояние
                # незавершённым — глава просто досохранится при следующем запуске.
                text = "\n\n".join(s["tr"].strip() for s in state["segments"])
                atomic_write(self.target_path(n), text, encoding="utf-8-sig")
            atomic_write(self.state_path(n), json.dumps(state, ensure_ascii=False, indent=1))

    @staticmethod
    def source_hash(paragraphs: list[str]) -> str:
        return hashlib.sha1("\n".join(paragraphs).encode("utf-8")).hexdigest()[:12]

    def load_partial(self, n: int, src: list[str]) -> dict | None:
        """Незавершённый перевод главы (после сбоя/остановки), если оригинал с тех пор не менялся."""
        st = read_json(self.state_path(n))
        if not st or st.get("stage") in (None, "done") or st.get("source_hash") != self.source_hash(src):
            return None
        return st

    # ---------- журнал задачи (продолжение после сбоя) ----------
    @property
    def job_path(self) -> Path:
        return self.meta_dir / "job.json"

    def load_job(self) -> dict | None:
        return read_json(self.job_path)

    def save_job(self, job: dict):
        job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        atomic_write(self.job_path, json.dumps(job, ensure_ascii=False, indent=1))

    def interrupted_job(self) -> dict | None:
        """Незавершённая задача перевода: running — процесс умер (сбой, выключение), cancelled — остановлена."""
        job = self.load_job()
        if not job or job.get("status") not in ("running", "starting", "cancelled"):
            return None
        left = [n for n in job.get("chapters", []) if n not in set(job.get("done", []))]
        if not left:
            return None
        partial = [n for n in left if (read_json(self.state_path(n)) or {}).get("stage") not in (None, "done")]
        return {**job, "remaining": left, "partial": partial}

    def set_segment(self, n: int, seg_id: int, text: str, by: str) -> dict:
        with self._lock:
            st = self.load_state(n)
            if st is None:
                raise KeyError(f"Глава {n} ещё не переведена")
            seg = next((s for s in st["segments"] if s["id"] == seg_id), None)
            if seg is None:
                raise KeyError(f"В главе {n} нет абзаца {seg_id}")
            if seg.get("tr"):
                seg.setdefault("history", []).append({"t": time.strftime("%H:%M:%S"), "by": by, "old": seg["tr"]})
            seg["tr"] = text.strip()
            self.save_state(n, st)
            return seg

    def status(self) -> dict:
        ch = self.chapters()
        translated = [n for n in ch if self.is_translated(n)]
        return {"title": self.meta["title"], "root": str(self.root),
                "source_dir": self.meta["source_dir"], "target_dir": self.meta["target_dir"],
                "chapters_total": len(ch), "chapters_translated": len(translated),
                "first": min(ch) if ch else None, "last": max(ch) if ch else None,
                "untranslated_head": [n for n in ch if n not in set(translated)][:15],
                "interrupted_job": self._job_summary()}

    def _job_summary(self) -> dict | None:
        j = self.interrupted_job()
        if not j:
            return None
        return {"status": j["status"], "chapters": f"{j['chapters'][0]}–{j['chapters'][-1]}",
                "done": len(j.get("done", [])), "remaining": len(j["remaining"]), "partial": j["partial"][:10],
                "updated_at": j.get("updated_at"),
                "hint": "продолжить: /continue или translate с теми же главами — готовое заново не переводится"}


def parse_range(spec, available: list[int]) -> list[int]:
    """'1-5,8,10-12' | [1,2] | 'all' | 'next:5' (5 первых непереведённых не поддерживается здесь)."""
    if spec is None or spec == "all":
        return list(available)
    if isinstance(spec, int):
        return [spec] if spec in available else []
    if isinstance(spec, list):
        return [int(x) for x in spec if int(x) in available]
    out: list[int] = []
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(n for n in available if int(a) <= n <= int(b))
        else:
            if int(part) in available:
                out.append(int(part))
    seen = set()
    return [n for n in out if not (n in seen or seen.add(n))]
