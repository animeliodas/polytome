"""ПРОТОКОЛ: инструменты, которые LLM-агент вызывает для перевода книги.

Каждый инструмент = имя + описание + JSON-схема аргументов + функция.
Тот же реестр можно отдать по MCP (см. mcp_server.py), чтобы им пользовались Hermes/Claude и т.п.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from .checks import check_chapter, name_variants_report
from .engine import Engine
from .glossary import KINDS
from .palladius import transcribe
from .workspace import parse_range, split_paragraphs

MAX_RESULT_CHARS = 12000


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    fn: Callable[..., object]
    required: tuple[str, ...] = ()
    mutating: bool = False

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": list(self.required)}}}


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"… [+{len(s) - n} симв.]"


class Toolbox:
    def __init__(self, engine: Engine):
        self.eng = engine
        self.ws = engine.ws
        self.gl = engine.gl
        self.tools: dict[str, Tool] = {}
        self._register()

    # ---------- исполнение ----------
    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def call(self, name: str, args: dict) -> str:
        t = self.tools.get(name)
        if t is None:
            return json.dumps({"error": f"нет инструмента {name}. Есть: {', '.join(self.tools)}"}, ensure_ascii=False)
        if "__invalid_json__" in args:
            return json.dumps({"error": "аргументы — невалидный JSON", "raw": args["__invalid_json__"][:300]},
                              ensure_ascii=False)
        missing = [r for r in t.required if r not in args]
        if missing:
            return json.dumps({"error": f"не хватает аргументов: {missing}"}, ensure_ascii=False)
        try:
            res = t.fn(**args)
        except TypeError as e:
            return json.dumps({"error": f"неверные аргументы: {e}"}, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001 — модель должна увидеть ошибку и исправиться
            return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
        out = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False, indent=1)
        return _clip(out, MAX_RESULT_CHARS)

    def add(self, name, description, params, required=(), mutating=False):
        def deco(fn):
            self.tools[name] = Tool(name, description, params, fn, tuple(required), mutating)
            return fn
        return deco

    def _chapters(self, spec) -> list[int]:
        return parse_range(spec, list(self.ws.chapters()))

    # ---------- регистрация ----------
    def _register(self):
        ws, gl, eng, add = self.ws, self.gl, self.eng, self.add
        S = {"type": "string"}
        I = {"type": "integer"}
        B = {"type": "boolean"}
        RANGE = {"type": "string", "description": "главы: '1-5,8' или 'all'"}

        @add("workspace_status", "Состояние книги: главы, сколько переведено, глоссарий, библия.", {})
        def workspace_status():
            st = ws.status()
            st["glossary"] = gl.stats()
            disputed = [f"{e['source']} {e['target']}: в глоссарии {e.get('gender')}, улики → {e['gender_conflict']}"
                        for e in gl.entries if e.get("gender_conflict")]
            if disputed:
                st["GENDER_CONFLICTS"] = disputed + ["→ verify_genders, исправь glossary_set и библию"]
            bible = ws.bible()
            st["bible_chars"] = len(bible)
            st["bible_filled"] = "(жанр" not in bible and len(bible) > 400
            st["models"] = {r: eng.cfg.role_spec(r) for r in ("agent", "translator", "reviewer", "extractor")}
            return st

        @add("list_chapters", "Список глав с размером и статусом перевода.",
             {"chapters": RANGE, "only": {"type": "string", "enum": ["all", "translated", "untranslated"]}})
        def list_chapters(chapters="all", only="all"):
            out = []
            for n in self._chapters(chapters):
                tr = ws.is_translated(n)
                if (only == "translated" and not tr) or (only == "untranslated" and tr):
                    continue
                paras = ws.source_paragraphs(n)
                out.append({"n": n, "title": paras[0][:40] if paras else "", "paragraphs": len(paras),
                            "translated": tr})
            return {"count": len(out), "chapters": out[:200]}

        @add("read_source", "Прочитать оригинал главы (абзацы с номерами). Диапазон абзацев необязателен.",
             {"chapter": I, "from_id": I, "to_id": I}, required=("chapter",))
        def read_source(chapter, from_id=1, to_id=10**6):
            paras = ws.source_paragraphs(chapter)
            return "\n".join(f"[{i}] {p}" for i, p in enumerate(paras, 1) if from_id <= i <= to_id)

        @add("read_translation", "Прочитать перевод главы рядом с оригиналом (по абзацам).",
             {"chapter": I, "from_id": I, "to_id": I, "with_source": B}, required=("chapter",))
        def read_translation(chapter, from_id=1, to_id=10**6, with_source=True):
            st = ws.load_state(chapter)
            if not st:
                return {"error": f"глава {chapter} не переведена"}
            if st.get("unaligned"):
                return {"warning": "перевод не выровнен с оригиналом по абзацам",
                        "translation": "\n".join(st["target_paragraphs"][from_id - 1:to_id])}
            lines = []
            for s in st["segments"]:
                if from_id <= s["id"] <= to_id:
                    if with_source:
                        lines.append(f"[{s['id']}] ОРИГ: {s['src']}\n[{s['id']}] ПЕР:  {s.get('tr') or '—'}")
                    else:
                        lines.append(f"[{s['id']}] {s.get('tr') or '—'}")
            return "\n".join(lines)

        @add("search", "Поиск по книге (подстрока или regex) в оригинале или переводе. Для проверки согласованности.",
             {"query": S, "scope": {"type": "string", "enum": ["source", "translation"]}, "chapters": RANGE,
              "regex": B, "limit": I}, required=("query",))
        def search(query, scope="translation", chapters="all", regex=False, limit=40):
            pat = re.compile(query if regex else re.escape(query), re.I)
            hits, total = [], 0
            for n in self._chapters(chapters):
                if scope == "source":
                    paras = list(enumerate(ws.source_paragraphs(n), 1))
                else:
                    st = ws.load_state(n)
                    if not st or st.get("unaligned"):
                        continue
                    paras = [(s["id"], s.get("tr") or "") for s in st["segments"]]
                for i, p in paras:
                    for m in pat.finditer(p):
                        total += 1
                        if len(hits) < limit:
                            a, b = max(0, m.start() - 40), min(len(p), m.end() + 40)
                            hits.append(f"гл.{n} [{i}] …{p[a:b]}…")
            return {"total": total, "hits": hits}

        @add("transcribe", "Детерминированная транскрипция китайских имён/топонимов: Палладий для кириллицы, "
                           "пиньинь для латиницы (по языку перевода книги). Используй вместо собственной памяти.",
             {"names": {"type": "array", "items": S}, "kind": {"type": "string", "enum": ["auto", "person", "place", "other"]}},
             required=("names",))
        def transcribe_(names, kind="auto"):
            system = eng.pair.name_system
            if not system:
                return {"note": f"для пары {eng.pair.src.code}→{eng.pair.tgt.code} детерминированной транскрипции "
                                "нет — пиши имена по принятой в языке перевода традиции"}
            return [transcribe(n, kind, system) for n in names]

        @add("glossary_list", "Показать глоссарий (фильтр по подстроке/типу/статусу).",
             {"filter": S, "kind": S, "status": {"type": "string", "enum": ["any", "confirmed", "auto", "candidate"]},
              "limit": I})
        def glossary_list(filter="", kind="", status="any", limit=150):
            rows = [e for e in gl.entries
                    if (not filter or filter in e["source"] or filter.lower() in e["target"].lower())
                    and (not kind or e.get("kind") == kind) and (status == "any" or e.get("status") == status)]
            return {"total": len(rows), "entries": rows[:limit]}

        @add("glossary_set", "Добавить/обновить термины глоссария (пакетом). Имена — kind=person/place/org: их "
                             "написание потом проверяется кодом во всех главах. status: confirmed (по умолчанию) | candidate.",
             {"entries": {"type": "array", "items": {"type": "object", "properties": {
                 "source": S, "target": S, "kind": {"type": "string", "enum": list(KINDS)},
                 "gender": {"type": "string", "enum": ["m", "f", "n"]}, "note": S,
                 "status": {"type": "string", "enum": ["confirmed", "candidate"]},
                 "confirm_gender": {"type": "boolean", "description": "подтвердить пол вопреки предупреждению"}},
                 "required": ["source", "target"]}}}, required=("entries",), mutating=True)
        def glossary_set(entries):
            done = []
            for e in entries:
                ent = gl.upsert(e["source"], e["target"], e.get("kind", "other"), e.get("gender"), e.get("note"),
                                status=e.get("status", "confirmed"))
                if e.get("confirm_gender"):
                    ent["gender_confirmed"] = True
                done.append(f"{e['source']} → {e['target']}")
            gl.save()
            out = {"saved": done, "glossary_total": len(gl.entries)}
            if any(e.get("gender") for e in entries):
                conf = eng.verify_genders()
                touched = {e["source"] for e in entries}
                warn = [c for c in conf if c["source"] in touched]
                if warn:
                    out["GENDER_WARNING"] = {"msg": "пол противоречит уликам оригинала; проверь (search в оригинале) и "
                                                    "исправь, или повтори с confirm_gender=true", "items": warn}
            return out

        @add("verify_genders", "Сверить пол всех персонажей глоссария с уликами ОРИГИНАЛА (родство: «X，Y的弟弟», "
                               "обращения 姐姐/哥哥, титулы 先生/小姐, 名叫X的女子). Делай после записи персонажей.",
             {})
        def verify_genders():
            conf = eng.verify_genders()
            return {"conflicts": conf} if conf else {"ok": "противоречий не найдено"}

        @add("glossary_remove", "Удалить термины из глоссария.", {"sources": {"type": "array", "items": S}},
             required=("sources",), mutating=True)
        def glossary_remove(sources):
            removed = [s for s in sources if gl.remove(s)]
            gl.save()
            return {"removed": removed}

        @add("extract_terms", "Найти в главах новые имена и термины (модель-извлекатель). Имена добавляются "
                              "автоматически со status=auto (транскрипция по Палладию), прочие термины — status=candidate "
                              "(не используются, пока не подтвердишь через glossary_set).",
             {"chapters": RANGE}, required=("chapters",), mutating=True)
        def extract_terms(chapters):
            r = eng.extract_terms(self._chapters(chapters))
            return {"names_added": r["added"], "term_candidates": r.get("pending_terms", [])}

        @add("resolve_candidates", "Разобрать все неподтверждённые записи глоссария (status=candidate/auto) ОДНИМ "
                                   "вызовом модели-редактора с контекстом из оригинала: keep / fix / drop. Гораздо "
                                   "дешевле, чем разбирать вручную. Возвращает список изменений — просмотри его.",
             {"chapters": RANGE}, mutating=True)
        def resolve_candidates(chapters="all"):
            return eng.resolve_candidates(self._chapters(chapters))

        @add("bible_read", "Прочитать библию книги (правила перевода этой книги).", {})
        def bible_read():
            return ws.bible() or "(пусто)"

        @add("bible_write", "Записать библию книги целиком (markdown). Коротко и конкретно: о книге, персонажи "
                            "(пол, отношения, обращения), стиль, особые правила.",
             {"content": S}, required=("content",), mutating=True)
        def bible_write(content):
            ws.write_bible(content)
            return {"saved": True, "chars": len(content)}

        @add("bible_edit", "Точечная правка библии: заменить фрагмент old на new.", {"old": S, "new": S},
             required=("old", "new"), mutating=True)
        def bible_edit(old, new):
            b = ws.bible()
            if old not in b:
                return {"error": "фрагмент не найден"}
            ws.write_bible(b.replace(old, new, 1))
            return {"saved": True}

        @add("translate", "Перевести главы движком (батчи, шов контекста, авто-починка, рецензент). Возвращает "
                          "отчёт по главам. Уже переведённые пропускаются, если не retranslate=true.",
             {"chapters": RANGE, "retranslate": B, "review": B, "parallel_chapters": I},
             required=("chapters",), mutating=True)
        def translate(chapters, retranslate=False, review=True, parallel_chapters=None):
            r = eng.translate_chapters(self._chapters(chapters), retranslate=retranslate, review=review,
                                       parallel_chapters=parallel_chapters)
            for c in r["chapters"]:
                if "issues" in c:
                    c["issues"] = dict(list(c["issues"].items())[:6])
            return r

        @add("check", "Детерминированная проверка перевода глав: пропуски, иероглифы, обрезания, имена не по "
                      "глоссарию, числа. hard — точно ошибки, soft — подозрения.",
             {"chapters": RANGE, "include_soft": B}, required=("chapters",))
        def check(chapters, include_soft=False):
            out = {}
            for n in self._chapters(chapters):
                st = ws.load_state(n)
                if not st:
                    continue
                if st.get("unaligned"):
                    out[n] = {"warning": "перевод не выровнен по абзацам — проверка невозможна"}
                    continue
                rep = check_chapter(st, gl, pair=ws.pair)
                iss = {sid: [i["msg"] for i in v if include_soft or i["level"] == "hard"]
                       for sid, v in rep["issues"].items()}
                iss = {k: v for k, v in iss.items() if v}
                out[n] = {"hard": rep["hard"], "soft": rep["soft"], "issues": dict(list(iss.items())[:15])}
            return out

        @add("name_consistency", "Межглавная проверка написания имён: ищет в переводе формы, похожие на имена "
                                 "из глоссария, но не являющиеся их правильными формами.",
             {"chapters": RANGE})
        def name_consistency(chapters="all"):
            texts = {}
            for n in self._chapters(chapters):
                if ws.is_translated(n):
                    texts[n] = ws.target_path(n).read_text(encoding="utf-8-sig")
            if eng.pair.name_system != "palladius":
                return {"note": "межглавная проверка имён пока есть только для пары китайский → русский; "
                                "используй check по главам"}
            return name_variants_report(texts, gl)

        @add("review", "Прогнать рецензента по уже переведённой главе (с доп. указанием, например «проверь пол "
                       "персонажа X»). Правки применяются, если проходят проверки.",
             {"chapter": I, "instruction": S}, required=("chapter",), mutating=True)
        def review(chapter, instruction=""):
            return eng.review_chapter(chapter, instruction)

        @add("edit_segment", "Точечная правка перевода абзаца: заменить фрагмент old на new (или весь текст, "
                             "если old не задан).",
             {"chapter": I, "id": I, "old": S, "new": S}, required=("chapter", "id", "new"), mutating=True)
        def edit_segment(chapter, id, new, old=None):
            st = ws.load_state(chapter)
            seg = next((s for s in (st or {}).get("segments", []) if s["id"] == id), None)
            if not seg:
                return {"error": "абзац не найден"}
            cur = seg.get("tr") or ""
            if old:
                if old not in cur:
                    return {"error": "фрагмент old не найден в абзаце", "current": cur}
                text = cur.replace(old, new, 1)
            else:
                text = new
            ws.set_segment(chapter, id, text, by="agent")
            return {"ok": True, "text": text}

        @add("retranslate", "Перевести заново указанные абзацы главы с инструкцией (mode=retranslate) или "
                            "исправить их по инструкции, сохранив остальное (mode=fix).",
             {"chapter": I, "ids": {"type": "array", "items": I}, "instruction": S,
              "mode": {"type": "string", "enum": ["retranslate", "fix"]}},
             required=("chapter", "ids"), mutating=True)
        def retranslate(chapter, ids, instruction="", mode="retranslate"):
            return eng.retranslate_segments(chapter, ids, instruction, mode)

        @add("replace_in_translation", "Массовая замена в переводе (например, переименование персонажа). "
                                       "Сначала dry_run=true — покажет, что изменится.",
             {"old": S, "new": S, "chapters": RANGE, "regex": B, "dry_run": B},
             required=("old", "new"), mutating=True)
        def replace_in_translation(old, new, chapters="all", regex=False, dry_run=True):
            pat = re.compile(old if regex else re.escape(old))
            changed, preview = 0, []
            for n in self._chapters(chapters):
                st = ws.load_state(n)
                if not st or st.get("unaligned"):
                    continue
                touched = False
                for s in st["segments"]:
                    if s.get("tr") and pat.search(s["tr"]):
                        new_t = pat.sub(new, s["tr"])
                        changed += 1
                        if len(preview) < 10:
                            preview.append(f"гл.{n} [{s['id']}] {_clip(new_t, 160)}")
                        if not dry_run:
                            s.setdefault("history", []).append({"by": "agent-replace", "old": s["tr"]})
                            s["tr"] = new_t
                            touched = True
                if touched:
                    ws.save_state(n, st)
            return {"dry_run": dry_run, "segments_changed": changed, "preview": preview}

        @add("translate_text", "Перевести произвольный текст (не главу книги) с учётом глоссария и библии.",
             {"text": S}, required=("text",))
        def translate_text(text):
            return eng.translate_text(text)

        @add("import_text", "Импортировать оригинал из одного большого txt-файла: разрезать на главы по "
                            "заголовкам (第N章 / Глава N / Chapter N) и сохранить в папку оригинала.",
             {"path": S, "pattern": S}, required=("path",), mutating=True)
        def import_text(path, pattern=r"^\s*(第[0-9零〇一二两三四五六七八九十百千万]+[章节回].*|Глава\s*\d+.*|Chapter\s*\d+.*)$"):
            from pathlib import Path as P
            from .workspace import chapter_number
            text = P(path).read_text(encoding="utf-8-sig", errors="replace")
            rx = re.compile(pattern, re.M | re.I)
            marks = list(rx.finditer(text))
            if not marks:
                return {"error": "заголовки глав не найдены — уточни pattern"}
            ws.source_dir.mkdir(parents=True, exist_ok=True)
            saved = []
            for i, m in enumerate(marks):
                body = text[m.start(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
                n = chapter_number(m.group(0)) or (i + 1)
                (ws.source_dir / f"Глава {n}.txt").write_text("\n".join(split_paragraphs(body)), encoding="utf-8")
                saved.append(n)
            return {"chapters": len(saved), "first": saved[0], "last": saved[-1]}

        @add("export_book", "Собрать книгу из переведённых глав: EPUB (с обложкой и оглавлением), FB2 или TXT.",
             {"format": {"type": "string", "enum": ["epub", "fb2", "txt"]}, "chapters": RANGE, "path": S,
              "title": S, "author": S})
        def export_book(format="epub", chapters="all", path="", title="", author=""):
            from pathlib import Path as P
            from .formats import export_book as _export
            chs = None if chapters in (None, "", "all") else self._chapters(chapters)
            return _export(ws, format, P(path) if path else None, chs, title or None, author or None)

        @add("prepare_book", "Подготовить новую книгу одной операцией (без пошаговой работы агента): имена и термины "
                             "из первых глав → разбор кандидатов → сверка пола → черновик библии.",
             {"sample_chapters": I}, mutating=True)
        def prepare_book(sample_chapters=3):
            return eng.prepare(sample_chapters)

        @add("estimate", "Оценить токены и время перевода глав в текущем профиле (без LLM).", {"chapters": RANGE})
        def estimate(chapters="all"):
            return eng.estimate(self._chapters(chapters))

        @add("set_profile", "Профиль работы этой книги: quality | balanced | economy | local.",
             {"profile": {"type": "string", "enum": ["quality", "balanced", "economy", "local"]}},
             required=("profile",), mutating=True)
        def set_profile(profile):
            ws.meta["profile"] = profile
            ws.save_meta()
            return {"profile": profile, "note": "вступит в силу со следующего запуска перевода"}

        @add("repair_chapters", "Починить готовый перевод глав (свой или импортированный чужой): нормализация чисел и "
                                "пунктуации + адресная починка hard-ошибок. Корректные абзацы не трогаются.",
             {"chapters": RANGE, "review": B}, required=("chapters",), mutating=True)
        def repair_chapters(chapters, review=False):
            out = [eng.repair_chapter(n, review) for n in self._chapters(chapters) if ws.is_translated(n)]
            return {"chapters": len(out), "hard_before": sum(r["hard_before"] for r in out),
                    "hard_after": sum(r["hard_after"] for r in out), "details": out[:30]}

        @add("clean_book", "Найти и (apply=true) убрать мусор: служебные строки сайтов, заметки/благодарности автора "
                           "в конце глав. target: translation | source. Без apply — только отчёт.",
             {"target": {"type": "string", "enum": ["translation", "source"]}, "apply": B,
              "drop_lines": {"type": "array", "items": S}, "drop_regex": {"type": "array", "items": S}},
             mutating=True)
        def clean_book(target="translation", apply=False, drop_lines=None, drop_regex=None):
            from .cleanup import apply as _apply, detect, load_rules, save_rules
            rules = load_rules(ws)
            if drop_lines or drop_regex:
                rules["drop_lines"] += drop_lines or []
                rules["drop_regex"] += drop_regex or []
                save_rules(ws, rules)
            rep = _apply(ws, target, rules) if apply else detect(ws, target, rules)
            return {k: v for k, v in rep.items() if not k.startswith("_")}

        @add("edit_genders", "Исправить род персонажей в ГОТОВОМ переводе глав (когда оригинала нет или он не нужен): "
                             "по таблице персонажей (glossary kind=person с gender). Правит только род.",
             {"chapters": RANGE}, required=("chapters",), mutating=True)
        def edit_genders(chapters):
            return eng.edit_chapters(self._chapters(chapters))

        @add("job_start", "Запустить перевод ФОНОВЫМ процессом и сразу вернуться (для долгих задач и клиентов с "
                          "таймаутом вызова, например MCP/Hermes). Дальше — job_status.",
             {"chapters": RANGE, "profile": {"type": "string", "enum": ["quality", "balanced", "economy", "local"]},
              "export": {"type": "string", "enum": ["epub", "fb2", "txt", "all"]}}, mutating=True)
        def job_start(chapters="all", profile="", export=""):
            from .jobs import spawn
            args = ["run", str(ws.root), "-c", str(chapters)]
            if profile:
                args += ["--profile", profile]
            if export:
                args += ["--export", export]
            return spawn(ws.root, args)

        @add("job_status", "Статус фонового перевода: готово/осталось, ETA, токены, хвост лога (без LLM).", {})
        def job_status():
            from .jobs import status
            return status(ws.root)

        @add("job_stop", "Мягко остановить фоновый перевод (прогресс сохранится; продолжить — job_start/continue).", {},
             mutating=True)
        def job_stop():
            from .jobs import request_stop
            return request_stop(ws.root)
