"""API для встраивания Polytome в приложения (читалки, боты, GUI).

Всё, что делает CLI, но без консоли: входы и выходы — словари/строки (JSON-совместимые), прогресс — события.

    from polytome.api import Polytome
    h = Polytome({"slots": {"strong": "openrouter:<модель>", "fast": "openrouter:<модель>"},
               "providers": {"openrouter": {"api_key": "sk-..."}}, "profile": "economy"},
              home="/data/user/0/app/files/polytome")
    book = h.import_book("/sdcard/Download/книга.epub")             # → {"path": ..., "chapters": ...}
    h.translate(book["path"], chapters="1-5", on_event=print)        # блокирует; wait=False — в фоне
    h.chapter(book["path"], 1)                                       # оригинал и перевод по абзацам
    h.export(book["path"], "epub")

Для других языков (Kotlin через Chaquopy, Node, C#…) — одна точка входа `call(method, params_json) -> json`
и сервер `polytome serve` (JSON-RPC 2.0 по stdin/stdout, события — уведомлениями). Описание: docs/INTEGRATION.md.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .i18n import _
from .config import PROFILES, PROVIDER_CATALOG, Config
from .engine import Engine
from .formats import create_book, export_book
from .jobs import request_stop, status as job_status
from .llm import LLMClient
from .workspace import Workspace, is_book, parse_range, read_json, split_paragraphs

API_VERSION = 1

EventCallback = Callable[[dict], Any]


class PolytomeError(Exception):
    """Ошибка, понятная пользователю приложения (нет книги, нет ключа, задача уже идёт…)."""


class Polytome:
    def __init__(self, config: dict | str | Path | None = None, *, home: str | Path | None = None,
                 on_event: EventCallback | Any = None):
        """config: None — конфиг как у CLI (polytome.json + secrets.json); dict — целиком в памяти (ключи в
        providers.<id>.api_key, на диск ничего не пишется); путь — свой polytome.json.
        home: рабочая папка (библиотека по умолчанию, черновики translate_text). on_event — слушатель всех
        событий: функция от dict или объект с методом onEvent(json-строка) (удобно из Kotlin/Java)."""
        if isinstance(config, dict):
            self.cfg = Config.from_dict(config, home)
        else:
            self.cfg = Config.load(config)
        self.home = Path(home) if home else self.cfg.base_dir
        self.llm = LLMClient(self.cfg.parallel)
        self.listener = on_event
        self._active: dict[str, Engine] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._bench_cancel: threading.Event | None = None

    @classmethod
    def from_json(cls, config_json: str | None, home: str | None = None, on_event: Any = None) -> "Polytome":
        """То же из JSON-строки — удобно из Kotlin/Java (Chaquopy), где собирать dict неудобно."""
        return cls(json.loads(config_json) if config_json else None, home=home, on_event=on_event)

    # ------------------------------------------------------------------ служебное
    def _emit(self, book: str | None, e: dict, cb: EventCallback | None = None):
        ev = {**e, "book": book, "t": round(time.time(), 3)} if book else {**e, "t": round(time.time(), 3)}
        for target in (cb, self.listener):
            if target is None:
                continue
            try:
                if hasattr(target, "onEvent"):
                    target.onEvent(json.dumps(ev, ensure_ascii=False, default=str))
                else:
                    target(ev)
            except Exception:  # noqa: BLE001 — слушатель не должен ронять перевод
                pass

    def _ws(self, book: str | Path) -> Workspace:
        p = Path(book)
        if not p.is_dir() or not is_book(p):
            raise PolytomeError(_("Не книга polytome: {0} (сначала import_book)").format(p))
        return Workspace(p)

    def _engine(self, book: str | Path, profile: str | None = None, cb: EventCallback | None = None,
                own_llm: bool = False) -> Engine:
        ws = self._ws(book)
        key = str(ws.root)
        llm = LLMClient(self.cfg.parallel) if own_llm else self.llm  # своя задача — свой счётчик токенов
        return Engine(ws, self.cfg, llm, on_event=lambda e: self._emit(key, e, cb), profile=profile)

    def _chapters(self, ws: Workspace, spec) -> list[int]:
        if spec is None or spec == "all":
            return list(ws.chapters())
        if isinstance(spec, int):
            return [spec]
        if isinstance(spec, (list, tuple)):
            return [int(x) for x in spec]
        return parse_range(str(spec), list(ws.chapters()))

    # ------------------------------------------------------------------ сведения
    def version(self) -> dict:
        return {"polytome": __version__, "api": API_VERSION}

    def providers(self) -> list[dict]:
        """Каталог провайдеров для экрана настроек: есть ли ключ, где его взять, локальный ли."""
        out = []
        for pid, p in self.cfg.providers().items():
            out.append({"id": pid, "title": p.get("title", pid), "base_url": p.get("base_url"),
                        "local": bool(p.get("local")), "needs_key": not p.get("local"),
                        "has_key": bool(self.cfg.provider_key(pid)), "keys_url": p.get("keys_url"),
                        "suggest": p.get("suggest", {}), "builtin": pid in PROVIDER_CATALOG})
        return out

    def profiles(self) -> list[dict]:
        return [{"id": k, "desc": v["desc"], "current": k == self.cfg.profile_name} for k, v in PROFILES.items()]

    def config_summary(self) -> dict:
        return self.cfg.summary()

    def models(self, provider: str) -> list[dict]:
        """Модели провайдера прямо из его /models (всегда актуальный список): [{"id", "name", "context"?,
        "price_in"?, "price_out"? (USD за 1 млн токенов), "free"?}]."""
        prov = self.cfg.providers().get(provider)
        if prov is None:
            raise PolytomeError(_("Нет провайдера {0}").format(provider))
        return self.llm.list_models_info(self.cfg.resolve(f"{provider}:_"))

    def check(self, ping: bool = False) -> dict:
        """Готовность к переводу: у каждой роли есть модель и ключ; ping=True — короткий запрос к каждой модели."""
        roles = {}
        for role in ("translator", "reviewer", "extractor"):
            try:
                refs = self.cfg.role(role)
            except ValueError as e:
                roles[role] = {"ok": False, "error": str(e)}
                continue
            r0 = refs[0]
            local = bool(self.cfg.providers().get(r0.provider, {}).get("local"))
            item = {"model": r0.spec, "has_key": bool(r0.api_key) or local}
            item["ok"] = item["has_key"]
            if ping and item["ok"]:
                t0 = time.time()
                try:
                    self.llm.chat(r0, [{"role": "user", "content": "Ответь одним словом: ок"}], max_tokens=2000,  # i18n: skip
                                  role="check")
                    item["seconds"] = round(time.time() - t0, 1)
                except Exception as e:  # noqa: BLE001
                    item.update(ok=False, error=str(e)[:300])
            roles[role] = item
        return {"ready": all(v.get("ok") for v in roles.values()), "roles": roles, "profile": self.cfg.profile_name}

    # ------------------------------------------------------------------ библиотека
    def import_book(self, source: str | Path, library: str | Path | None = None, title: str | None = None,
                    fix_breaks: bool = False, target_lang: str | None = None, source_lang: str | None = None) -> dict:
        """EPUB / TXT / папка глав → книга polytome (папка с оригиналом по главам). Уже импортированная папка
        просто открывается. target_lang — язык, на котором читать (по умолчанию из конфига или язык системы);
        source_lang — язык оригинала (по умолчанию определяется по тексту)."""
        src = Path(source)
        if src.is_dir() and is_book(src):
            if target_lang or source_lang:
                return self.set_languages(src, source_lang=source_lang, target_lang=target_lang)
            return self.book_info(src)
        lib = Path(library) if library else None
        if lib is None and src.is_file():
            lib = self.home / "books"
        if lib:
            lib.mkdir(parents=True, exist_ok=True)
        try:
            ws, rep = create_book(src, lib, title, fix_breaks=fix_breaks, source_lang=source_lang,
                                  target_lang=target_lang or self.cfg.target_lang,
                                  on_progress=lambda stage, done, total: self._emit(None, {
                                      "ev": "import_progress", "source": str(src), "stage": stage, "done": done,
                                      "total": total}))
        except ValueError as e:
            raise PolytomeError(str(e)) from None
        info = self.book_info(ws.root)
        info["import"] = rep
        return info

    def languages(self, source_lang: str = "zh") -> list[dict]:
        """Языки для экрана выбора: код, название, самоназвание, письмо и какие детерминированные проверки есть
        для пары source_lang → этот язык. Перевод возможен для любой пары."""
        from .lang import LANGS, Pair
        out = []
        for code, lg in LANGS.items():
            pr = Pair.of(source_lang, code)
            out.append({"code": code, "name": lg.name, "native": lg.native, "script": lg.script,
                        "name_system": pr.name_system, "gender_check": pr.ru_target,
                        "default": code == self.cfg.target_lang})
        return out

    def set_languages(self, book: str | Path, source_lang: str | None = None, target_lang: str | None = None,
                      force: bool = False) -> dict:
        """Сменить язык перевода (или исправить язык оригинала). Если уже есть переведённые главы — только с
        force=True: иначе в книге смешаются языки."""
        from .lang import normalize
        ws = self._ws(book)
        done = sum(1 for n in ws.chapters() if ws.is_translated(n))
        if target_lang and normalize(target_lang) != ws.meta.get("target_lang") and done and not force:
            raise PolytomeError(_("В книге уже переведено глав: {0}. Сменить язык перевода — force=true (переведённое "
                                  "останется на старом языке; лучше импортировать книгу заново)").format(done))
        if source_lang:
            ws.meta["source_lang"] = normalize(source_lang)
        if target_lang:
            ws.meta["target_lang"] = normalize(target_lang)
        ws.save_meta()
        return self.book_info(ws.root)

    def books(self, library: str | Path | None = None) -> list[dict]:
        lib = Path(library) if library else self.home / "books"
        if not lib.is_dir():
            return []
        return [self.book_info(p) for p in sorted(lib.iterdir())
                if p.is_dir() and not p.name.startswith(".") and is_book(p)]

    def book_info(self, book: str | Path) -> dict:
        ws = self._ws(book)
        st = ws.status()
        cover = next((ws.root / n for n in ("cover.jpg", "cover.jpeg", "cover.png", "cover.webp")
                      if (ws.root / n).is_file()), None)
        m = ws.meta
        tl = m.get("target_lang", "ru")
        return {"path": str(ws.root), "title": m.get(f"title_{tl}") or m.get("title"),
                "meta_translated": bool(m.get(f"title_{tl}")) or m.get("source_lang") == tl,
                "original_title": m.get("original_title") or m.get("title"), "author": m.get(f"author_{tl}") or
                m.get("author", ""), "source_lang": m.get("source_lang"), "target_lang": tl,
                "chapters_total": st["chapters_total"], "chapters_translated": st["chapters_translated"],
                "cover": str(cover) if cover else None, "profile": m.get("profile"),
                "job": (job_status(ws.root).get("job") or None)}

    def chapters(self, book: str | Path) -> list[dict]:
        """Оглавление: номер, заголовок оригинала и перевода, готовность главы."""
        ws = self._ws(book)
        src_titles = {x["n"]: x["title"] for x in (read_json(ws.meta_dir / "source_map.json", []) or [])}
        toc_tr = {int(k): v for k, v in (read_json(ws.meta_dir / f"toc_{ws.meta.get('target_lang', 'ru')}.json", {})
                                        or {}).items()}
        job = ws.interrupted_job()
        partial = set(job["partial"]) if job else set()
        out = []
        for n in ws.chapters():
            tr_title = None
            if ws.is_translated(n):
                with open(ws.target_path(n), encoding="utf-8-sig") as f:
                    tr_title = next((ln.strip() for ln in f if ln.strip()), None)
            out.append({"n": n, "title": tr_title or toc_tr.get(n) or src_titles.get(n) or _("Глава {0}").format(n),
                        "source_title": src_titles.get(n), "translated": ws.is_translated(n) and n not in partial,
                        "partial": n in partial})
        return out

    def chapter(self, book: str | Path, n: int, which: str = "both") -> dict:
        """Текст главы по абзацам: source / translation / both. Абзацы оригинала и перевода идут 1:1 —
        удобно для параллельного чтения и переключения «оригинал ↔ перевод» по абзацу."""
        ws = self._ws(book)
        n = int(n)
        if n not in ws.chapters():
            raise PolytomeError(_("Нет главы {0}").format(n))
        src = ws.source_paragraphs(n)
        out: dict = {"n": n, "translated": ws.is_translated(n)}
        if which in ("source", "both"):
            out["source"] = src
        if which in ("translation", "both"):
            out["translation"], out["aligned"] = None, False
            if ws.is_translated(n):
                tr = split_paragraphs(ws.target_path(n).read_text(encoding="utf-8-sig"))
                if len(tr) != len(src):  # абзац перевода с переносом внутри или чужой перевод — берём по сегментам
                    st = ws.load_state(n) or {}
                    segs = st.get("segments") or []
                    if len(segs) == len(src):
                        tr = [s.get("tr") or "" for s in segs]
                out["translation"], out["aligned"] = tr, len(tr) == len(src)
        return out

    # ------------------------------------------------------------------ перевод
    def estimate(self, book: str | Path, chapters: Any = None, profile: str | None = None) -> dict:
        eng = self._engine(book, profile)
        return eng.estimate(self._chapters(eng.ws, chapters))

    def translate(self, book: str | Path, chapters: Any = None, *, profile: str | None = None, review: bool = True,
                  retranslate: bool = False, prepare: bool = True, export: str | None = None,
                  on_event: EventCallback | None = None, wait: bool = True) -> dict:
        """Перевести главы (по умолчанию все непереведённые). Готовое не переводится заново; прерванная задача
        продолжается с места остановки. wait=False — запустить в фоновом потоке и сразу вернуться (прогресс —
        событиями и status()). События: job_start, prepare_start/done, chapter_start, batch_done, chapter_done,
        chapter_cancelled, job_done, job_error, log."""
        eng = self._engine(book, profile, on_event, own_llm=True)
        key = str(eng.ws.root)
        chs = self._chapters(eng.ws, chapters)
        if not chs:
            raise PolytomeError(_("Нет глав для перевода"))
        with self._lock:
            if key in self._active:
                raise PolytomeError(_("Для этой книги уже идёт перевод"))
            job = eng.ws.load_job() or {}
            # «running» с нашим же pid, но без активной задачи — след упавшего прошлого запуска, а не чужой процесс
            if (job.get("status") in ("running", "starting") and job.get("pid") != os.getpid()
                    and job_status(key).get("job", {}).get("alive")):
                raise PolytomeError(_("Эту книгу уже переводит другой процесс (polytome CLI / Hermes)"))
            self._active[key] = eng

        def work() -> dict:
            try:
                r = eng.run(chs, retranslate=retranslate, review=review, prepare=prepare)
                if export and not r.get("cancelled") and not r.get("HALTED"):
                    r["export"] = self._export(eng, export)
                self._emit(key, {"ev": "job_done", "report": _brief(r)}, on_event)
                return r
            except Exception as e:  # noqa: BLE001
                _mark_failed(eng.ws, e)
                self._emit(key, {"ev": "job_error", "error": f"{type(e).__name__}: {e}"}, on_event)
                raise
            finally:
                with self._lock:
                    self._active.pop(key, None)
                    self._threads.pop(key, None)

        if wait:
            return work()
        th = threading.Thread(target=lambda: _swallow(work), name=f"polytome:{eng.ws.root.name}", daemon=True)
        self._threads[key] = th
        th.start()
        return {"started": True, "book": key, "chapters": len(chs)}

    def resume(self, book: str | Path, on_event: EventCallback | None = None, wait: bool = True) -> dict:
        """Продолжить прерванную задачу (сбой, остановка, приложение закрыто) с теми же настройками."""
        ws = self._ws(book)
        job = ws.interrupted_job()
        if not job:
            return {"nothing_to_resume": True}
        if job.get("kind") == "edit":
            raise PolytomeError(_("Прерванная задача — редактура; продолжите её командой polytome continue"))
        return self.translate(ws.root, job["chapters"], profile=job.get("profile") if job.get("profile") in PROFILES
                              else None, review=job.get("review", True), retranslate=job.get("retranslate", False),
                              prepare=False, on_event=on_event, wait=wait)

    def cancel(self, book: str | Path) -> dict:
        """Мягкая остановка: текущие батчи допишутся и сохранятся (секунды, до ~1 мин), потом resume()."""
        key = str(Path(book).resolve())
        with self._lock:
            eng = self._active.get(key)
        if eng:
            eng.cancel.set()
            return {"stopping": True}
        r = request_stop(Path(book))
        return {"stopping": bool(r.get("stopped")), **({"reason": r["reason"]} if r.get("reason") else {})}

    def wait(self, book: str | Path, timeout: float | None = None) -> bool:
        """Дождаться фоновой задачи (wait=False). True — завершилась."""
        th = self._threads.get(str(Path(book).resolve()))
        if th:
            th.join(timeout)
            return not th.is_alive()
        return True

    def status(self, book: str | Path) -> dict:
        """Прогресс без LLM и без токенов: глав готово/осталось, ETA, токены, состояние задачи."""
        self._ws(book)
        out = job_status(Path(book))
        out["active_here"] = str(Path(book).resolve()) in self._active
        return out

    def translate_text(self, text: str, book: str | Path | None = None, source_lang: str | None = None,
                       target_lang: str | None = None, profile: str | None = None) -> str:
        """Перевести фрагмент (выделенный абзац, «перевести по тапу»). С book — по глоссарию и библии книги,
        имена будут как в переводе книги. Без book: source_lang определяется по тексту, target_lang — из конфига."""
        if book:
            return self._engine(book, profile).translate_text(text)
        from .formats import detect_lang
        from .lang import normalize
        src = normalize(source_lang) or detect_lang(text)
        tgt = normalize(target_lang) or self.cfg.target_lang
        ws = Workspace(self.home / ".scratch" / f"{src}-{tgt}")
        ws.meta.update({"source_lang": src, "target_lang": tgt})
        if not ws.meta_dir.is_dir():
            ws.init()
        cfg = self.cfg.with_overrides(profile=profile) if profile else self.cfg
        return Engine(ws, cfg, self.llm).translate_text(text)

    def translate_meta(self, book: str | Path, toc: bool = True) -> dict:
        """Название и автор на языке перевода (автор из иероглифов — транскрипцией, без модели) и, если toc,
        заголовки ещё не переведённых глав — чтобы библиотека и оглавление сразу были на языке читателя.
        Уже переведённое не переводится заново. Прогресс оглавления — событиями toc_progress."""
        eng = self._engine(book)
        eng.ensure_title_meta()
        n = eng.translate_toc() if toc else 0
        info = self.book_info(book)
        return {"title": info["title"], "author": info["author"], "toc_translated": n}

    # ------------------------------------------------------------------ помощь читателю
    def explain(self, book: str | Path, n: int, paragraph: int, fragment: str = "", question: str = "") -> dict:
        """Объяснить фрагмент абзаца (слова, имена, термины, идиомы) с учётом глоссария и библии книги.
        paragraph — номер абзаца главы с 0; fragment — то, что выделил читатель (пусто — весь абзац)."""
        ch = self.chapter(book, n)
        src = _nth(ch.get("source"), paragraph)
        tr = _nth(ch.get("translation"), paragraph) if ch.get("aligned") else ""
        return {"answer": self._engine(book).explain(src, tr, fragment, question)}

    def suggest_fix(self, book: str | Path, n: int, paragraph: int, instruction: str = "",
                    mode: str = "fix") -> dict:
        """Вариант перевода абзаца — не сохраняется (сохранить: set_paragraph). mode: fix — поправить готовый
        перевод по указанию; retranslate — перевести абзац заново. → {"source", "old", "new"}."""
        r = self._engine(book).suggest_segment(int(n), int(paragraph), instruction, mode)
        if not r["new"]:
            raise PolytomeError(_("Модель не вернула перевод абзаца"))
        return r

    def set_paragraph(self, book: str | Path, n: int, paragraph: int, text: str) -> dict:
        """Сохранить перевод абзаца (paragraph — с 0). Прежний текст остаётся в истории главы."""
        ws = self._ws(book)
        st = ws.load_state(int(n)) or {}
        segs = st.get("segments") or []
        if not 0 <= int(paragraph) < len(segs):
            raise PolytomeError(_("В главе {0} нет абзаца {1}").format(n, int(paragraph) + 1))
        ws.set_segment(int(n), segs[int(paragraph)]["id"], text, by="user")
        return {"ok": True}

    # ------------------------------------------------------------------ замеры моделей
    def bench_info(self) -> dict:
        """Набор для замеров: версия, пары языков, размер и грубая оценка токенов на пару."""
        from . import bench
        return bench.info()

    def bench(self, model: str | None = None, pair: str = "zh-ru", save_dir: str | Path | None = None,
              keep: bool = False, budget: int | None = None) -> dict:
        """Проверить модель на наборе bench (одинаковые тексты, качество — проверками кода). model —
        «провайдер:модель» (по умолчанию — переводчик из конфига). Прогресс — события bench_progress, итог —
        bench_done. Возвращает запись результата (JSON без ключей и адресов); save_dir — ещё и сохранить файл.
        budget — лимит токенов на прогон (по умолчанию bench.BUDGET); превышен — error="budget"."""
        from . import bench
        spec = model or self.cfg.role_specs("translator")[0][0]
        if ":" not in spec:
            raise PolytomeError(_("Модель указывается как провайдер:модель, например openrouter:deepseek/deepseek-chat"))
        cancel = threading.Event()
        self._bench_cancel = cancel
        try:
            rec = bench.run(self.cfg, spec, pair, self.home, on_event=lambda e: self._emit(None, e), cancel=cancel,
                            keep=keep, budget=budget or bench.BUDGET)
        finally:
            self._bench_cancel = None
        if save_dir:
            rec["file"] = str(bench.save(rec, Path(save_dir)))
        return rec

    def bench_cancel(self) -> dict:
        ev = self._bench_cancel
        if ev:
            ev.set()
        return {"stopping": bool(ev)}

    # ------------------------------------------------------------------ сборка
    def export(self, book: str | Path, fmt: str = "epub", out: str | Path | None = None,
               chapters: Any = None) -> dict | list[dict]:
        """Собрать переведённую книгу: epub | fb2 | txt | all."""
        eng = self._engine(book)
        return self._export(eng, fmt, out, chapters)

    def _export(self, eng: Engine, fmt: str, out: str | Path | None = None, chapters: Any = None):
        eng.ensure_title_meta()
        chs = self._chapters(eng.ws, chapters) if chapters is not None else None
        fmts = ["epub", "fb2", "txt"] if fmt == "all" else [fmt]
        try:
            res = [export_book(eng.ws, f, Path(out) if out and len(fmts) == 1 else None, chs) for f in fmts]
        except ValueError as e:
            raise PolytomeError(str(e)) from None
        return res[0] if len(res) == 1 else res

    # ------------------------------------------------------------------ JSON-вход для любых языков
    METHODS = ("version", "providers", "profiles", "languages", "set_languages", "config_summary", "models", "check",
               "import_book", "books",
               "book_info", "chapters", "chapter", "estimate", "translate", "resume", "cancel", "status",
               "translate_text", "translate_meta", "explain", "suggest_fix", "set_paragraph", "export",
               "bench_info", "bench", "bench_cancel")

    def call(self, method: str, params: dict | str | None = None) -> str:
        """Вызов по имени с JSON-параметрами → JSON-строка {"ok": true, "result": ...} или
        {"ok": false, "error": {"type": ..., "message": ...}}. Для Chaquopy/JNI/stdio: одна функция на всё."""
        try:
            if method not in self.METHODS:
                raise PolytomeError(_("Нет метода {0}. Есть: {1}").format(method, ', '.join(self.METHODS)))
            kw = json.loads(params) if isinstance(params, str) else dict(params or {})
            if method in ("translate", "resume"):
                kw.setdefault("wait", False)  # из JSON-мира — всегда в фоне, прогресс событиями
            result = getattr(self, method)(**kw)
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False, default=str)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"ok": False, "error": {"type": type(e).__name__, "message": str(e)}},
                              ensure_ascii=False)


def _nth(items: list | None, i: int) -> str:
    items = items or []
    return items[int(i)] if 0 <= int(i) < len(items) else ""


def _brief(r: dict) -> dict:
    keys = ("translated", "failed", "skipped", "cancelled", "HALTED", "hard_issues_total", "review_fixes_total",
            "seconds", "tokens", "note", "export")
    b = {k: r[k] for k in keys if k in r}
    errors = [c["error"] for c in r.get("chapters") or [] if c.get("error")]
    if r.get("failed") and errors:
        b["failed_error"] = str(errors[0])[:300]  # почему главы не перевелись — приложению для сообщения
    return b


def _mark_failed(ws: Workspace, err: Exception):
    """Задача упала с исключением (например, нет сети при подготовке книги): job.json не должен остаться «running» —
    иначе следующий запуск счёл бы, что книгу переводит другой процесс, а resume не нашёл бы прерванную задачу."""
    job = ws.load_job() or {}
    if job.get("status") in ("running", "starting") and job.get("pid") == os.getpid():
        job.update({"status": "failed", "halt_reason": f"{type(err).__name__}: {err}"[:300], "in_progress": []})
        ws.save_job(job)


def _swallow(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001 — уже отправлено событием job_error
        pass


__all__ = ["Polytome", "PolytomeError", "API_VERSION"]
