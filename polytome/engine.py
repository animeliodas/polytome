"""Движок перевода: глава → батчи → починка → рецензент → состояние.

Агент вызывает его через инструменты и получает только краткие отчёты.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import checks as _checks
from .i18n import _
from .checks import (check_chapter, check_segment, glossary_stems, han_count, normalize_numbers,
                     normalize_punct)
from .config import Config, ModelRef
from .glossary import Glossary
from .llm import LLMClient, LLMError
from .palladius import transcribe
from .prompting import Prompts
from .workspace import Workspace, atomic_write, read_json

_THINK = re.compile(r"<think>.*?</think>", re.S)
_LINE = re.compile(r"^\s*\[(\d+)\]\s?(.*)$")
_PLACE_SUFFIX = set("市省县縣村镇鎮山河湖江路街区區州府城国國岛島")
# Должности и обращения после фамилии: 余經理 → «управляющий Юй», 葉少 → «молодой господин Е».
_TITLE_SUFFIXES = ("經理", "经理", "總", "总", "董", "少", "少爺", "少爷", "哥", "姐", "弟", "妹", "叔", "嬸", "婶",
                   "姨", "伯", "爺", "爷", "老師", "老师", "醫生", "医生", "先生", "小姐", "女士", "太太", "夫人",
                   "同學", "同学", "師傅", "师傅", "局長", "局长", "隊長", "队长", "主任", "校長", "校长", "教授",
                   "老闆", "老板", "掌櫃", "掌柜", "大師", "大师", "道長", "道长", "警官", "隊", "總裁", "总裁")
_SURNAME_CHARS = set("李王張张劉刘陳陈楊杨趙赵黃黄周吳吴徐孫孙胡朱高林何郭馬马羅罗梁宋鄭郑謝谢韓韩唐馮冯于董蕭萧程"
                     "曹袁鄧邓許许傅沈曾彭呂吕蘇苏盧卢蔣蒋蔡賈贾丁魏薛葉叶閻阎余潘杜戴夏鍾钟汪田任姜范方石姚譚谭"
                     "廖鄒邹熊金陸陆郝孔白崔康毛邱秦江史顧顾侯邵孟龍龙萬万段雷錢钱湯汤尹黎易常武喬乔賀贺賴赖龔龚文")


# «Автор неизвестен» по-китайски — переводить как пометку, а не транскрибировать как имя
ANONYMOUS_AUTHORS = {"佚名", "未知", "匿名", "无名氏", "無名氏", "未知作者", "作者未知"}


def strip_think(s: str) -> str:
    s = _THINK.sub("", s or "")
    if "</think>" in s:
        s = s.split("</think>", 1)[1]
    return s.strip()


def parse_numbered(text: str, ids: list[int]) -> dict[int, str]:
    want = set(ids)
    out: dict[int, str] = {}
    cur: int | None = None
    for line in strip_think(text).replace("\r", "").split("\n"):
        m = _LINE.match(line)
        if m:
            cur = int(m.group(1)) if int(m.group(1)) in want else None  # чужой id — не клеим к предыдущему
            if cur is not None:
                out[cur] = m.group(2).strip()
        elif cur is not None and line.strip() and not line.strip().startswith("```"):
            out[cur] = (out[cur] + " " + line.strip()).strip()
    return {k: v for k, v in out.items() if v}


def parse_json_lenient(text: str) -> dict | None:
    s = strip_think(text)
    s = re.sub(r"^```(?:json)?|```$", "", s.strip(), flags=re.M).strip()
    start = s.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                chunk = s[start:i + 1]
                for cand in (chunk, re.sub(r",\s*([}\]])", r"\1", chunk)):
                    try:
                        return json.loads(cand)
                    except json.JSONDecodeError:
                        pass
                return None
    return None


class Engine:
    def __init__(self, ws: Workspace, cfg: Config, llm: LLMClient, on_event=None, profile: str | None = None):
        self.ws = ws
        # Профиль: явный на этот запуск → профиль книги (.polytome/book.json) → из конфига.
        self.cfg = cfg.with_overrides(ws.meta.get("roles"), profile or cfg.forced_profile or ws.meta.get("profile"))
        self.opts = self.cfg.engine_options()
        self.llm = llm
        self.gl = Glossary(ws.glossary_path)
        self.on_event = on_event or (lambda e: None)
        self.batch_chars = int(ws.meta.get("batch_chars") or self.opts.get("batch_chars", 1500))
        self.review_mode = self.opts.get("review", "full")  # full | flagged | off
        self.repair_attempts = int(self.opts.get("repair_attempts", 2))
        self.seam = 4
        self.lookahead = 2
        self.temperature = 0.3
        self._rr = itertools.count()
        self.cancel = threading.Event()
        self.job_id: str | None = None
        self.pair = ws.pair
        self.P = Prompts(self.pair)
        self.digits = self.cfg.numbers_style(self.pair) == "digits"
        _checks.NUMBERS_DIGITS = self.digits

    # ---------- служебное ----------
    def emit(self, ev: str, **kw):
        self.on_event({"ev": ev, **kw})

    def log(self, msg: str):
        self.emit("log", msg=msg)

    def _translator(self, shift: int = 0) -> ModelRef:
        lst = self.cfg.role("translator")
        return lst[(next(self._rr) + shift) % len(lst)]

    def translit(self, src: str, kind: str) -> str | None:
        """Детерминированная транскрипция имени для пары (Палладий / пиньинь) или None — решает модель."""
        if not self.pair.name_system:
            return None
        return transcribe(src, kind, self.pair.name_system).get("target")

    def norm_text(self, tr: str) -> str:
        """Детерминированные правки перевода без LLM, если они применимы к паре."""
        if self.pair.cjk_to_alphabetic:
            tr = normalize_punct(tr)
        return normalize_numbers(tr) if self.digits and self.pair.ru_target else tr

    def _style(self) -> str:
        return self.cfg.style_text(self.pair)

    def _bible_for(self, entries: list[dict], text: str = "") -> str:
        """Библия для конкретного фрагмента: ядро целиком + из авто-разделов «Новые персонажи» только строки о
        тех, кто есть во фрагменте. Иначе на длинном романе библия растёт с каждой волной (14k символов к 65-й
        главе) и уходит целиком в каждый запрос."""
        b = self.ws.bible().strip()
        i = min((k for k in (b.find("\n## Новые персонажи"), b.find("\n## New characters")) if k >= 0),
                default=-1)
        if i < 0:
            return b
        core, auto = b[:i].rstrip(), b[i:]
        keys = {e["source"] for e in entries} | {e["target"] for e in entries if len(e.get("target", "")) >= 3}
        picked = []
        for line in auto.splitlines():
            if not line.startswith("- "):
                continue
            toks = {t for pair in re.findall(r"\*\*([^*]+)\*\*|\(([^)]{1,12})\)", line) for t in pair if t}
            if any(k in line for k in keys) or (text and any(t in text for t in toks)):
                picked.append(line)
        if picked:
            core += "\n\n" + self.P.m("picked_heading") + "\n" + "\n".join(picked[:25])
        return core

    def _system_prompt(self, entries: list[dict], text: str = "") -> str:
        parts = [self.P.translator()]
        bible = self._bible_for(entries, text)
        if bible:
            parts.append(self.P.m("bible_header") + bible)
        # Правила заказчика — после библии и важнее её: агент однажды записал в библию «500万 = 5 миллионов»,
        # и она перекрыла требование «числа цифрами» (см. JOURNAL.md).
        style = self._style().strip()
        if style:
            parts.append(self.P.m("house_rules") + style)
        if self.digits:
            parts.append(self.P.digits_rule())
        if entries:
            parts.append(self.P.m("glossary_header") + self._glossary_text(entries))
        parts.append(self.P.format_rules)
        return "\n\n".join(parts)

    def _glossary_text(self, entries: list[dict]) -> str:
        return Glossary.format_for_prompt(entries, {"m": self.P.m("gl_male"), "f": self.P.m("gl_female"),
                                                    "disputed": self.P.m("gl_disputed")})

    @staticmethod
    def _size(text: str) -> int:
        """Объём абзаца для батчей и оценок: иероглиф ≈ слово, для алфавитных языков — 2 символа."""
        return max(han_count(text), len(text) // 2)

    def _batches(self, segs: list[dict]) -> list[list[dict]]:
        out, cur, size = [], [], 0
        for s in segs:
            l = self._size(s["src"])
            if cur and (size + l > self.batch_chars or len(cur) >= 60):
                out.append(cur)
                cur, size = [], 0
            cur.append(s)
            size += l
        if cur:
            out.append(cur)
        # Не оставляем крошечный хвост: приклеиваем к предыдущему батчу.
        if len(out) >= 2 and sum(self._size(s["src"]) for s in out[-1]) < self.batch_chars * 0.25:
            out[-2].extend(out.pop())
        return out

    # ---------- перевод батча ----------
    def _translate_batch(self, n: int, batch: list[dict], seam: list[dict], look: list[dict],
                         entries: list[dict], instruction: str = "", model: ModelRef | None = None) -> dict[int, str]:
        model = model or self._translator()
        user = []
        if seam:
            user.append(self.P.m("prev")
                        + "\n".join(s["tr"] for s in seam if s.get("tr")))
        user.append(self.P.m("translate", n=n) + "\n".join(f"[{s['id']}] {s['src']}" for s in batch))
        if look:
            user.append(self.P.m("ahead")
                        + "\n".join(s["src"] for s in look))
        if instruction:
            user.append(self.P.m("special") + instruction)
        msgs = [{"role": "system", "content": self._system_prompt(entries, "\n".join(s["src"] for s in batch))},
                {"role": "user", "content": "\n\n".join(user)}]
        # Лимит вывода: без него модель в редких случаях зацикливается до предела контекста.
        budget = 3000 + 6 * sum(self._size(s["src"]) for s in batch)
        res = self.llm.chat(model, msgs, temperature=self.temperature, max_tokens=budget, role="translator")
        got = parse_numbered(res.content, [s["id"] for s in batch])
        if res.finish_reason == "length" and len(batch) > 1 and len(got) < len(batch):
            mid = len(batch) // 2
            got.update(self._translate_batch(n, batch[:mid], seam, batch[mid:mid + 2], entries, instruction, model))
            rest = [s for s in batch[mid:] if s["id"] not in got]
            if rest:
                got.update(self._translate_batch(n, rest, seam, look, entries, instruction, model))
        return got

    def _fill(self, n: int, batch: list[dict], seam: list[dict], look: list[dict], entries: list[dict]):
        """Переводит батч; недостающие id добирает повтором (сначала группой, потом поштучно)."""
        todo = batch
        for attempt in range(3):
            if not todo or self.cancel.is_set():
                break
            try:
                got = self._translate_batch(n, todo, seam, look, entries, model=self._translator(attempt))
            except LLMError as e:
                self.log(_("гл.{0}: ошибка батча ({1}); повтор").format(n, e))
                got = {}
            for s in todo:
                if s["id"] in got:
                    s["tr"] = got[s["id"]]
                    s["by"] = "translator"
            todo = [s for s in todo if not s.get("tr")]
            if todo:
                self.log(_("гл.{0}: модель пропустила абзацы {1}, добираю").format(n, [s['id'] for s in todo]))
        for s in todo:  # поштучно
            try:
                got = self._translate_batch(n, [s], seam, look, entries, model=self._translator(1))
                if s["id"] in got:
                    s["tr"] = got[s["id"]]
                    s["by"] = "translator"
            except LLMError as e:
                self.log(_("гл.{0} абз.{1}: не удалось перевести ({2})").format(n, s['id'], e))

    # ---------- адресная правка ----------
    def _fix_segment(self, src: str, tr: str, instruction: str, entries: list[dict],
                     model: ModelRef | None = None) -> str | None:
        msgs = [{"role": "system", "content": self._system_prompt(entries, src).replace(self.P.format_rules, "")
                 + self.P.m("fix_system")},
                {"role": "user", "content": self.P.m("fix_user", src=src, tr=tr, instruction=instruction)}]
        res = self.llm.chat(model or self._translator(), msgs, temperature=0.2, max_tokens=3000, role="fixer")
        out = strip_think(res.content).strip().strip("`").strip()
        return out or None

    def _hint_for(self, issues: list[dict]) -> tuple[str, str]:
        """→ (режим, указание). режим: retranslate | fix"""
        types = {i["type"] for i in issues if i["level"] == "hard"}
        hints = []
        if types & {"missing", "short", "duplicate"}:
            return "retranslate", self.P.m("hint_full")
        if "untranslated" in types or "punct" in types:
            hints.append(self.P.m("hint_script", tgt=self.pair.tgt.name) if not self.P.ru
                         else self.P.m("hint_script"))
        for i in issues:
            if i["type"] == "gender":
                hints.append(self.P.m("hint_gender", msg=i["msg"]))
        if "number_words" in types:
            hints.append(self.P.m("hint_digits"))
        for i in issues:
            if i["type"] == "unknown_name":
                hints.append(self.P.m("hint_unknown_name", name=i["msg"].split("»")[0] + "»"))
            if i["type"] == "name_variant":
                e = self.gl.get(i.get("term", ""))
                if e:
                    hints.append(self.P.m("hint_name_variant", source=e["source"], target=e["target"]))
                else:
                    hints.append(self.P.m("hint_name_fix", msg=i["msg"]))
        return "fix", " ".join(hints)

    def _repair(self, n: int, segs: list[dict], entries_all: list[dict]):
        stems = glossary_stems(self.gl, self.pair)
        for s in segs:
            if self.cancel.is_set():
                return
            ents = self.gl.relevant(s["src"])
            issues = [i for i in check_segment(s["src"], s.get("tr"), ents, stems, self.gl, self.pair)
                      if i["level"] == "hard"]
            if not issues:
                continue
            for attempt in range(self.repair_attempts):
                mode, hint = self._hint_for(issues)
                try:
                    if mode == "retranslate" or not s.get("tr"):
                        idx = segs.index(s)
                        seam = [x for x in segs[max(0, idx - 2):idx] if x.get("tr")]
                        got = self._translate_batch(n, [s], seam, segs[idx + 1:idx + 2], ents, hint,
                                                    model=self._translator(attempt + 1))
                        new = got.get(s["id"])
                    else:
                        new = self._fix_segment(s["src"], s["tr"], hint, ents, self._translator(attempt))
                except LLMError as e:
                    self.log(_("гл.{0} абз.{1}: починка не удалась ({2})").format(n, s['id'], e))
                    break
                if not new:
                    continue
                new_issues = [i for i in check_segment(s["src"], new, ents, stems, self.gl, self.pair) if i["level"] == "hard"]
                if len(new_issues) < len(issues):
                    if s.get("tr"):
                        s.setdefault("history", []).append({"by": "repair", "old": s["tr"],
                                                            "why": "; ".join(i["msg"] for i in issues)})
                    s["tr"], s["by"] = new, "repair"
                    self.emit("repair", n=n, seg=s["id"], why=issues[0]["msg"])
                    issues = new_issues
                if not issues:
                    break

    # ---------- рецензент ----------
    def _review_window(self, n: int, win: list[dict], instruction: str = "") -> list[dict]:
        entries = self.gl.relevant("\n".join(s["src"] for s in win))
        extra = []
        bible = self._bible_for(entries, "\n".join(s["src"] for s in win))
        if bible:
            extra.append(self.P.m("rv_bible") + bible)
        if entries:
            extra.append(self.P.m("rv_glossary") + self._glossary_text(entries))
        if instruction:
            extra.append(self.P.m("rv_extra") + instruction)
        sys = self.P.reviewer("\n\n".join(extra))
        o, t = self.P.m("rv_orig"), self.P.m("rv_tr")
        body = "\n".join(f"[{s['id']}] {o}: {s['src']}\n[{s['id']}] {t}: {s.get('tr') or ''}" for s in win)
        res = self.llm.chat(self.cfg.role("reviewer")[0],
                            [{"role": "system", "content": sys},
                             {"role": "user", "content": self.P.m("rv_user", n=n, body=body)}],
                            temperature=0.2, json_mode=True, max_tokens=8000, role="reviewer")
        data = parse_json_lenient(res.content) or {}
        return [f for f in data.get("fixes", []) if isinstance(f, dict)]

    def _flagged_windows(self, segs: list[dict], ctx: int = 2) -> list[list[dict]]:
        """Профиль economy: рецензент видит только места с замечаниями проверок или после починки (±ctx абзацев),
        и ОДНИМ запросом на главу. Было: каждое место — отдельный запрос с библией и глоссарием, и на 100 главах
        economy съел почти столько же, сколько balanced (29k против 31k токенов на главу)."""
        from .checks import NOISY
        stems = glossary_stems(self.gl, self.pair)
        triggers = lambda x: x["level"] == "hard" or x["type"] in ("short", "long", "number", "name_not_in_source",  # noqa: E731
                                                                     "length_shift")
        hot = [i for i, s in enumerate(segs)
               if s.get("by") == "repair"
               or any(x["type"] not in NOISY and triggers(x)
                      for x in check_segment(s["src"], s.get("tr"), self.gl.relevant(s["src"]), stems, self.gl, self.pair))]
        ranges: list[list[int]] = []
        for i in hot:
            a, b = max(0, i - ctx), min(len(segs), i + ctx + 1)
            if ranges and a <= ranges[-1][1]:
                ranges[-1][1] = max(ranges[-1][1], b)
            else:
                ranges.append([a, b])
        merged = [s for a, b in ranges for s in segs[a:b]]
        return [merged[i:i + 40] for i in range(0, len(merged), 40)] if merged else []

    def _review(self, n: int, segs: list[dict], window: int = 20, instruction: str = "",
                flagged_only: bool = False) -> list[dict]:
        if flagged_only:
            wins = self._flagged_windows(segs)
            if not wins:
                return []
        else:
            wins = [segs[i:i + window] for i in range(0, len(segs), window)]
        applied: list[dict] = []
        stems = glossary_stems(self.gl, self.pair)
        with ThreadPoolExecutor(max_workers=min(4, len(wins) or 1)) as ex:
            results = list(ex.map(lambda w: self._safe_review(n, w, instruction), wins))
        by_id = {s["id"]: s for s in segs}
        for fixes in results:
            for f in fixes:
                try:
                    sid = int(f.get("id"))
                except (TypeError, ValueError):
                    continue
                s, new = by_id.get(sid), (f.get("fix") or "").strip()
                if not s or not new or new == s.get("tr"):
                    continue
                ents = self.gl.relevant(s["src"])
                old_hard = [i for i in check_segment(s["src"], s.get("tr"), ents, stems, self.gl, self.pair) if i["level"] == "hard"]
                new_hard = [i for i in check_segment(s["src"], new, ents, stems, self.gl, self.pair) if i["level"] == "hard"]
                if len(new_hard) > len(old_hard):
                    continue
                if s.get("tr") and len(new) < 0.6 * len(s["tr"]):
                    continue  # рецензент «сократил» абзац — не принимаем
                s.setdefault("history", []).append({"by": "reviewer", "old": s.get("tr"),
                                                    "why": str(f.get("problem", ""))[:200]})
                s["tr"], s["by"] = new, "reviewer"
                applied.append({"id": sid, "problem": str(f.get("problem", ""))[:160]})
        return applied

    def _safe_review(self, n, win, instruction):
        try:
            return self._review_window(n, win, instruction)
        except LLMError as e:
            self.log(_("гл.{0}: рецензент пропустил окно ({1})").format(n, e))
            return []

    def _checkpoint(self, n: int, segs: list[dict], stage: str):
        self.ws.save_state(n, {"segments": segs, "stage": stage, "job_id": self.job_id}, write_text=False)

    def _cancelled(self, n: int, segs: list[dict], stage: str) -> dict:
        self._checkpoint(n, segs, stage)
        done = sum(1 for s in segs if s.get("tr"))
        self.emit("chapter_cancelled", n=n, done=done, total=len(segs))
        return {"chapter": n, "cancelled": True, "progress": _("{0}/{1} абзацев сохранено").format(done, len(segs))}

    def _normalize(self, segs: list[dict]):
        """Детерминированные правки без LLM: пунктуация, «500 миллионов» → «500000000»."""
        for s in segs:
            if s.get("tr"):
                s["tr"] = self.norm_text(s["tr"])

    # ---------- глава ----------
    def translate_chapter(self, n: int, retranslate: bool = False, review: bool = True) -> dict:
        t0 = time.time()
        src = self.ws.source_paragraphs(n)
        # Незавершённый перевод (сбой питания, Ctrl+C) — продолжаем с места остановки.
        partial = self.ws.load_partial(n, src)
        if partial and retranslate and partial.get("job_id") != self.job_id:
            partial = None  # это старый недоделанный черновик, а просят перевести заново
        if self.ws.is_translated(n) and not retranslate and not partial:
            return {"chapter": n, "skipped": _("уже переведена")}
        if partial:
            segs = partial["segments"]
            stage = partial.get("stage", "draft")
            done = sum(1 for s in segs if s.get("tr"))
            self.emit("resume", n=n, done=done, total=len(segs), stage=stage)
        else:
            segs = [{"id": i + 1, "src": p, "tr": None, "history": []} for i, p in enumerate(src)]
            stage = "draft"
        entries = self.gl.relevant("\n".join(src))
        self.emit("chapter_start", n=n, segments=len(segs))
        batches = self._batches(segs)
        for bi, batch in enumerate(batches):
            if all(s.get("tr") for s in batch):
                continue  # переведено до сбоя
            if self.cancel.is_set():
                return self._cancelled(n, segs, stage)
            first = segs.index(batch[0])
            seam = [s for s in segs[max(0, first - self.seam):first] if s.get("tr")]
            look = batches[bi + 1][:self.lookahead] if bi + 1 < len(batches) else []
            self._fill(n, batch, seam, look, entries)
            self._checkpoint(n, segs, "draft")  # каждый батч на диске: сбой теряет максимум один батч
            self.emit("batch_done", n=n, batch=bi + 1, batches=len(batches))
        if self.cancel.is_set():
            return self._cancelled(n, segs, "draft")
        self._normalize(segs)
        if stage == "draft":
            self._repair(n, segs, entries)
            if self.cancel.is_set():
                return self._cancelled(n, segs, "draft")  # починка прервана — повторится целиком
            self._checkpoint(n, segs, "repaired")
        applied = []
        if review and self.review_mode != "off" and all(s.get("tr") for s in segs) and stage != "reviewed":
            if self.cancel.is_set():
                return self._cancelled(n, segs, "repaired")
            applied = self._review(n, segs, flagged_only=self.review_mode == "flagged")
            if applied:
                self._repair(n, segs, entries)  # вдруг рецензент что-то сломал
            if self.cancel.is_set():
                return self._cancelled(n, segs, "repaired")
            self._checkpoint(n, segs, "reviewed")
        self._normalize(segs)
        state = {"segments": segs, "models": {"translator": self.cfg.role_spec("translator"),
                                              "reviewer": self.cfg.role_spec("reviewer") if review else None}}
        complete = all(s.get("tr") for s in segs)
        self.ws.save_state(n, state, write_text=complete)
        rep = check_chapter(state, self.gl, include_noisy=True, pair=self.pair)
        # Имена, которые переводчик написал по Палладию и которые выводятся из иероглифов оригинала,
        # но которых нет в глоссарии, — добавляем (status=auto): дальше они будут контролироваться.
        new_names = {i["term"]: i["target"] for v in rep["issues"].values() for i in v if i["type"] == "new_name"}
        for src_name, target in new_names.items():
            if not self.gl.get(src_name):
                self.gl.upsert(src_name, target, "person", note=_("найдено в переводе гл.{0}").format(n), status="auto")
        if new_names:
            self.gl.save()
            rep = check_chapter(state, self.gl, pair=self.pair)
        out = {"chapter": n, "segments": len(segs), "complete": complete,
               "review_fixes": len(applied), "repairs": sum(1 for s in segs if s.get("by") == "repair"),
               "hard_issues": rep["hard"], "soft_issues": rep["soft"], "seconds": round(time.time() - t0, 1)}
        if applied:
            out["review_examples"] = [f"[{a['id']}] {a['problem']}" for a in applied[:6]]
        hard_only = {sid: [i["msg"] for i in iss if i["level"] == "hard"] for sid, iss in rep["issues"].items()}
        hard_only = {k: v for k, v in hard_only.items() if v}
        if hard_only:  # soft — только счётчик: разбирать их поштучно агенту невыгодно
            out["issues"] = dict(list(hard_only.items())[:12])
        self.emit("chapter_done", n=n, report=out)
        return out

    def translate_chapters(self, chapters: list[int], retranslate=False, review=True, scan_terms=True,
                           parallel_chapters: int | None = None, wave: int | None = None,
                           auto_glossary: bool = True, auto_bible: bool = False) -> dict:
        """Перевод набора глав ВОЛНАМИ (по умолчанию 25 глав).

        Перед каждой волной: новые имена/термины этих глав → авторазбор кандидатов → сверка пола → (опц.)
        пополнение библии новыми персонажами. Так глоссарий растёт вместе с книгой — для романа на 900 глав
        не нужно сначала 900 вызовов извлечения. Прогресс, токены, ETA — в .polytome/job.json после каждой главы.
        """
        t0 = time.time()
        prev = self.ws.interrupted_job()
        resume = bool(prev and set(chapters) <= set(prev["chapters"]) and prev.get("retranslate") == retranslate)
        self.job_id = prev["id"] if resume else time.strftime("%Y%m%d-%H%M%S")
        job = (dict(prev) if resume else {"id": self.job_id, "chapters": list(chapters), "retranslate": retranslate,
                                         "review": review, "done": [], "failed": [], "profile": self.cfg.profile_name})
        job.update({"status": "running", "phase": "translate", "pid": os.getpid(), "started_at": job.get("started_at") or
                    time.strftime("%Y-%m-%d %H:%M:%S"), "in_progress": []})
        job.pop("remaining", None)
        job.pop("partial", None)
        self.ws.save_job(job)
        job_lock = threading.Lock()
        tok0 = self.llm.usage.total()
        done_before = set(job["done"]) if resume else set()
        todo = [n for n in chapters if n not in done_before and (retranslate or not self.ws.is_translated(n)
                                                                 or self.ws.load_partial(n, self.ws.source_paragraphs(n)))]
        job["todo_total"] = len(todo)
        stop_watch = self._start_stop_watch()
        wave = wave or int(self.opts.get("wave", 25))
        par = parallel_chapters or max(1, min(len(todo) or 1, self.cfg.parallel // 2))
        reports: list[dict] = []
        added, pending, gender_conf, glossary_changes, bible_added = [], [], [], [], []
        self.emit("job_start", total=len(todo), resumed=resume)
        fail_streak = 0
        try:
            for w0 in range(0, len(todo), wave):
                chunk = todo[w0:w0 + wave]
                if self.cancel.is_set():
                    break
                if scan_terms:
                    ex = self.extract_terms(chunk, only_new_chapters=True)
                    added += ex["added"]
                    if auto_glossary and (ex["added"] or ex.get("pending_terms")):
                        rc = self.resolve_candidates(chunk)
                        glossary_changes += rc.get("changes", [])
                    elif not auto_glossary:
                        pending += ex.get("pending_terms", [])
                    if auto_bible and ex["added"]:
                        bible_added += self.refresh_bible(chunk)
                gender_conf = self.verify_genders()

                def run(n):
                    with job_lock:
                        job["in_progress"].append(n)
                        self.ws.save_job(job)
                    r = self._safe_chapter(n, retranslate, review)
                    with job_lock:
                        job["in_progress"] = [x for x in job["in_progress"] if x != n]
                        if r.get("complete"):
                            job["done"].append(n)
                        elif r.get("error"):
                            job["failed"].append(n)
                        self._job_progress(job, t0, tok0)
                        self.ws.save_job(job)
                    return r

                with ThreadPoolExecutor(max_workers=par) as ex_pool:
                    for r in ex_pool.map(run, chunk):
                        reports.append(r)
                        # Предохранитель: провайдер «лёг» — не сжигаем всю книгу на ошибках.
                        fail_streak = fail_streak + 1 if r.get("error") else 0
                        if fail_streak >= max(3, par):
                            self.log(_("несколько глав подряд упали с ошибкой провайдера — останавливаю задачу"))
                            job["halt_reason"] = "provider_errors: " + str(r.get("error"))[:200]
                            self.cancel.set()
        finally:
            stop_watch.set()
        reports.sort(key=lambda r: r["chapter"])
        cancelled = [r["chapter"] for r in reports if r.get("cancelled")]
        not_started = [n for n in todo if n not in {r["chapter"] for r in reports}]
        halted = bool(job.get("halt_reason"))
        job["status"] = "failed" if halted else "cancelled" if (cancelled or not_started) else "done"
        job["done"] = sorted(set(job["done"]) | {n for n in chapters if self.ws.is_translated(n)
                                                 and not self.ws.load_partial(n, self.ws.source_paragraphs(n))})
        job["in_progress"] = []
        self._job_progress(job, t0, tok0)
        self.ws.save_job(job)
        self.cancel.clear()
        big = len(reports) > 30  # длинные задачи: агенту — сводка, а не 900 отчётов
        chapter_reports = [r for r in reports if r.get("hard_issues") or r.get("error") or r.get("cancelled")] \
            if big else reports
        out = {"translated": _ranges([r["chapter"] for r in reports if r.get("complete")]),
               "failed": [r["chapter"] for r in reports if not r.get("complete") and not r.get("skipped")
                          and not r.get("cancelled")],
               "skipped": _ranges([n for n in chapters if n not in todo]),
               "hard_issues_total": sum(r.get("hard_issues", 0) for r in reports),
               "review_fixes_total": sum(r.get("review_fixes", 0) for r in reports),
               "new_names_auto_added": added[:40] + ([_("… ещё {0}").format(len(added) - 40)] if len(added) > 40 else []),
               "glossary_auto_changes": glossary_changes[:30],
               "chapters": chapter_reports[:60], "seconds": round(time.time() - t0, 1),
               "tokens": self.llm.usage.summary()}
        if pending:
            out["term_candidates_pending"] = pending[:40]
        if bible_added:
            out["bible_added"] = bible_added[:20]
        if gender_conf:
            out["GENDER_CONFLICTS"] = {
                "warning": _("пол в глоссарии противоречит оригиналу — при переводе пол этих персонажей не навязывался; "
                             "проверь улики и исправь glossary_set (и библию), затем перепроверь главы"),
                "items": gender_conf}
        if resume:
            out["resumed_job"] = self.job_id
        if halted:
            out["HALTED"] = job["halt_reason"] + _(" — проверьте провайдера/ключ (polytome doctor) и продолжите (continue)")
        elif cancelled or not_started:
            out["cancelled"] = _ranges(cancelled + not_started)
            out["note"] = _("остановлено; прогресс сохранён — повтор той же команды (или continue) продолжит")
        return out

    def _job_progress(self, job: dict, t0: float, tok0: int):
        done_now = len([n for n in job["done"] if n in set(job["chapters"])])
        elapsed = time.time() - t0
        finished_this_run = max(0, done_now - (len(job["chapters"]) - job.get("todo_total", len(job["chapters"]))))
        rate = finished_this_run / elapsed * 60 if elapsed > 30 and finished_this_run else None
        left = len(job["chapters"]) - done_now
        job.update({"chapters_done": done_now, "chapters_left": left,
                    "tokens_this_run": self.llm.usage.total() - tok0,
                    "chapters_per_min": round(rate, 2) if rate else None,
                    "eta_min": round(left / rate) if rate else None})

    def _start_stop_watch(self) -> threading.Event:
        """Остановка из другого процесса (`polytome stop`, Hermes): файл .polytome/STOP → мягкая отмена."""
        done = threading.Event()
        flag = self.ws.meta_dir / "STOP"
        flag.unlink(missing_ok=True)

        def watch():
            while not done.wait(2):
                if flag.exists():
                    self.log(_("получен сигнал остановки (STOP)"))
                    self.cancel.set()
                    flag.unlink(missing_ok=True)
                    return
        threading.Thread(target=watch, daemon=True).start()
        return done

    # ---------- подготовка книги без агента ----------
    def prepare(self, sample_chapters: int = 3) -> dict:
        """Глоссарий + библия для новой книги одной командой (без агента): extract → resolve → verify → bible."""
        chs = list(self.ws.chapters())[:sample_chapters]
        ex = self.extract_terms(chs)
        rc = self.resolve_candidates(chs)
        conf = self.verify_genders()
        bible = self.draft_bible(chs) if self._bible_is_template() else None
        return {"names": ex["added"], "glossary_changes": rc.get("changes", []), "gender_conflicts": conf,
                "bible_written": bool(bible), "glossary_total": len(self.gl.entries)}

    def needs_prepare(self) -> bool:
        return not self.gl.entries or self._bible_is_template()

    def run(self, chapters: list[int], retranslate: bool = False, review: bool = True, wave: int | None = None,
            prepare: bool = True) -> dict:
        """Полный цикл, как `polytome run`: подготовка новой книги (имена, библия) → перевод волнами."""
        prepared = None
        if prepare and self.needs_prepare():
            self.ws.save_job({"id": time.strftime("%Y%m%d-%H%M%S"), "chapters": chapters, "retranslate": retranslate,
                              "review": review, "done": [], "failed": [], "profile": self.cfg.profile_name,
                              "status": "running", "phase": "prepare", "pid": os.getpid(),
                              "started_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            self.emit("prepare_start")
            prepared = self.prepare()
            job = self.ws.load_job() or {}
            if job.get("phase") == "prepare":  # подготовка — не прерванный перевод: дальше новая задача, а не resume
                self.ws.save_job({**job, "status": "prepared"})
            self.emit("prepare_done", report=prepared)
        out = self.translate_chapters(chapters, retranslate=retranslate, review=review, wave=wave,
                                      auto_glossary=True, auto_bible=True)
        if prepared:
            out["prepared"] = prepared
        return out

    def ensure_title_meta(self):
        """Название и автор на языке перевода для обложки EPUB/FB2: название — переводчиком (1 короткий вызов),
        автор — детерминированной транскрипцией (Палладий/пиньинь), если она есть для пары."""
        ws = self.ws
        changed = False
        tk, ak = f"title_{self.pair.tgt.code}", f"author_{self.pair.tgt.code}"
        orig = ws.meta.get("original_title") or ws.meta.get("title", "")
        if not ws.meta.get(tk) and orig and self.pair.src.code != self.pair.tgt.code and (
                han_count(orig) or ws.meta.get("source_file")):
            try:
                ws.meta[tk] = self.translate_text(orig).strip().strip("«»\"") or orig
                changed = True
            except LLMError:
                pass
        au = ws.meta.get("author", "").strip()
        if not ws.meta.get(ak) and au and self.pair.src.code != self.pair.tgt.code:
            if au in ANONYMOUS_AUTHORS:  # «佚名» — не имя, а «автор неизвестен»
                ws.meta[ak] = self.P.m("anonymous_author") if self.P.ru else self._quiet_translate(au)
            elif 2 <= han_count(au) <= 4 and self.translit(au, "person"):
                ws.meta[ak] = self.translit(au, "person")
            else:
                ws.meta[ak] = self._quiet_translate(au)
            changed = bool(ws.meta.get(ak)) or changed
            if not ws.meta.get(ak):
                ws.meta.pop(ak, None)
        if changed:
            ws.save_meta()

    def _quiet_translate(self, text: str) -> str:
        try:
            return self.translate_text(text).strip().strip("«»\"")
        except LLMError:
            return ""

    def _bible_is_template(self) -> bool:
        b = self.ws.bible()
        return len(b) < 600 or "(жанр, время и место действия" in b or "(genre, time and place" in b

    def draft_bible(self, chapters: list[int]) -> str:
        sample = "\n".join("\n".join(self.ws.source_paragraphs(n)) for n in chapters)[:14000]
        persons = [e for e in self.gl.entries if e.get("kind") == "person"]
        g = {"m": self.P.m("male"), "f": self.P.m("female")}
        cast = "\n".join(f"{e['source']} → {e['target']} ({g.get(e.get('gender'), self.P.m('gender_unknown'))})"
                         + (self.P.m("gender_disputed") if e.get("gender_conflict") else "")
                         for e in persons[:60])
        msgs = [{"role": "system", "content": self.P.bible_writer(self._style().strip() or "—",
                                                                  self.P.bible_template(self.ws.meta["title"]))},
                {"role": "user", "content": self.P.m("bw_user", cast=cast or "—", sample=sample)}]
        res = self.llm.chat(self.cfg.role("agent")[0], msgs, temperature=0.3, max_tokens=6000, role="bible")
        text = strip_think(res.content).strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?|```$", "", text, flags=re.M).strip()
        if len(text) > 300:
            self.ws.write_bible(text)
        return text

    def refresh_bible(self, chapters: list[int]) -> list[str]:
        """Длинный роман: дописать в библию новых персонажей волны (одним коротким вызовом)."""
        known = self.ws.bible()
        new = [e for e in self.gl.entries if e.get("kind") == "person" and e["target"] not in known
               and e.get("status") in ("auto", "confirmed")]
        if len(new) < 2:
            return []
        texts = {n: self.ws.source_paragraphs(n) for n in chapters}
        lines = []
        for e in new[:15]:
            ctx = next((p for paras in texts.values() for p in paras if e["source"] in p), "")
            lines.append(self.P.m("rb_line", source=e["source"], target=e["target"], gender=e.get("gender") or "?",
                                  ctx=ctx[:160]))
        msgs = [{"role": "system", "content": self.P.bible_refresher()},
                {"role": "user", "content": "\n".join(lines)}]
        try:
            res = self.llm.chat(self.cfg.role("reviewer")[0], msgs, temperature=0.2, max_tokens=2500, role="bible")
        except LLMError:
            return []
        add = [l.strip() for l in strip_think(res.content).splitlines() if l.strip().startswith("- ")]
        if add:
            rng = f"{min(chapters)}–{max(chapters)}"
            self.ws.write_bible(known.rstrip() + "\n\n" + self.P.m("rb_heading", rng=rng) + "\n" + "\n".join(add) + "\n")
        return add

    # ---------- оценка ----------
    # Замеры 2026-10-21 (JOURNAL.md): токенов на иероглиф оригинала, включая поиск имён (для не-CJK — на 2 символа);
    # скорость — глав в минуту на одну параллельную главу.
    TOKENS_PER_CHAR = {"quality": 60, "balanced": 18, "economy": 9.5, "local": 9}
    WORKER_CH_PER_MIN = {"quality": 0.3, "balanced": 1.0, "economy": 2.7, "local": 0.1}

    def estimate(self, chapters: list[int]) -> dict:
        avail = self.ws.chapters()
        todo = [n for n in chapters if n in avail and not self.ws.is_translated(n)]
        chars = 0
        for n in todo:
            src = "\n".join(self.ws.source_paragraphs(n))
            chars += self._size(src)
        prof = self.cfg.profile_name
        tok = chars * self.TOKENS_PER_CHAR.get(prof, 13)
        rate = self.WORKER_CH_PER_MIN.get(prof, 1.0) * max(1, self.cfg.parallel // 2)
        return {"profile": prof, "chapters": len(todo), "source_chars": chars,
                "tokens_estimate": int(tok), "tokens_per_chapter": int(tok / max(1, len(todo))),
                "time_estimate_min": round(len(todo) / max(rate, 0.01)),
                "note": _("оценка по замерам на китайских веб-новеллах; реальные цифры видны в status во время работы")}

    def edit_chapters(self, chapters: list[int], task: str = "genders", parallel_chapters: int | None = None,
                      redo: bool = False) -> dict:
        """Редактура готового перевода по главам как задача: журнал (status/stop/continue), параллельно.
        Уже отредактированные главы (book.json → edited.<task>) пропускаются."""
        t0, tok0 = time.time(), self.llm.usage.total()
        done_set = set() if redo else set(self.ws.meta.get("edited", {}).get(task, []))
        todo = [n for n in chapters if n not in done_set and self.ws.is_translated(n)]
        prev = self.ws.interrupted_job()
        self.job_id = prev["id"] if prev and prev.get("kind") == "edit" else time.strftime("%Y%m%d-%H%M%S")
        job = {"id": self.job_id, "kind": "edit", "task": task, "chapters": list(chapters),
               "done": sorted(done_set & set(chapters)), "failed": [], "status": "running", "phase": "edit",
               "pid": os.getpid(), "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "in_progress": [],
               "profile": self.cfg.profile_name, "todo_total": len(todo)}
        self.ws.save_job(job)
        lock = threading.Lock()
        stop = self._start_stop_watch()
        fixed = 0
        par = parallel_chapters or max(1, self.cfg.parallel // 2)

        def run(n):
            nonlocal fixed
            if self.cancel.is_set():
                return {"chapter": n, "cancelled": True}
            with lock:
                job["in_progress"].append(n)
                self.ws.save_job(job)
            try:
                r = self.edit_genders(n)
            except Exception as e:  # noqa: BLE001
                r = {"chapter": n, "error": str(e)}
            with lock:
                job["in_progress"] = [x for x in job["in_progress"] if x != n]
                if r.get("error"):
                    job["failed"].append(n)
                else:
                    job["done"].append(n)
                    fixed += r.get("fixed", 0)
                    ed = self.ws.meta.setdefault("edited", {}).setdefault(task, [])
                    ed.append(n)
                    self.ws.save_meta()
                self._job_progress(job, t0, tok0)
                self.ws.save_job(job)
            self.emit("edit_done", n=n, report=r)
            return r

        try:
            with ThreadPoolExecutor(max_workers=par) as ex:
                reports = list(ex.map(run, todo))
        finally:
            stop.set()
        cancelled = [r["chapter"] for r in reports if r.get("cancelled")]
        job["status"] = "cancelled" if cancelled else "done"
        job["in_progress"] = []
        self._job_progress(job, t0, tok0)
        self.ws.save_job(job)
        self.cancel.clear()
        return {"edited": _ranges([r["chapter"] for r in reports if "fixed" in r]),
                "paragraphs_fixed": fixed, "failed": [r["chapter"] for r in reports if r.get("error")],
                "skipped": _ranges(sorted(done_set & set(chapters))), "cancelled": _ranges(cancelled),
                "seconds": round(time.time() - t0, 1), "tokens": self.llm.usage.summary()}

    def _safe_chapter(self, n, retranslate, review):
        try:
            return self.translate_chapter(n, retranslate, review)
        except Exception as e:  # noqa: BLE001 — отчёт агенту, а не падение всей задачи
            self.log(_("гл.{0}: ошибка {1}: {2}").format(n, type(e).__name__, e))
            return {"chapter": n, "complete": False, "error": f"{type(e).__name__}: {e}"}

    # ---------- точечные операции ----------
    def retranslate_segments(self, n: int, ids: list[int], instruction: str = "", mode: str = "retranslate") -> list[dict]:
        st = self._aligned_state(n)
        segs = st["segments"]
        out = []
        for sid in ids:
            s = next((x for x in segs if x["id"] == sid), None)
            if s is None:
                continue
            new = self._redo_segment(n, segs, s, instruction, mode)
            if new:
                if s.get("tr"):
                    s.setdefault("history", []).append({"by": "agent-retranslate", "old": s["tr"], "why": instruction})
                s["tr"], s["by"] = new, "agent"
                out.append({"id": sid, "new": new})
        self.ws.save_state(n, st)
        return out

    def suggest_segment(self, n: int, index: int, instruction: str = "", mode: str = "fix") -> dict:
        """Вариант перевода абзаца index (0 — первый абзац главы) — без сохранения: читатель сначала посмотрит."""
        segs = self._aligned_state(n)["segments"]
        if not 0 <= index < len(segs):
            raise ValueError(_("В главе {0} нет абзаца {1}").format(n, index + 1))
        s = segs[index]
        new = self._redo_segment(n, segs, s, instruction, mode)
        return {"id": s["id"], "source": s["src"], "old": s.get("tr") or "", "new": new or ""}

    def _aligned_state(self, n: int) -> dict:
        st = self.ws.load_state(n)
        if not st or st.get("unaligned"):
            raise ValueError(_("Глава {0}: нет посегментного перевода").format(n))
        return st

    def _redo_segment(self, n: int, segs: list[dict], s: dict, instruction: str, mode: str) -> str | None:
        ents = self.gl.relevant(s["src"])
        idx = segs.index(s)
        if mode == "fix" and s.get("tr"):
            new = self._fix_segment(s["src"], s["tr"], instruction or self.P.m("fix_default"), ents)
        else:
            seam = [x for x in segs[max(0, idx - self.seam):idx] if x.get("tr")]
            new = self._translate_batch(n, [s], seam, segs[idx + 1:idx + 3], ents, instruction).get(s["id"])
        return self.norm_text(new) if new else None

    def explain(self, source: str, translation: str = "", fragment: str = "", question: str = "") -> str:
        """Ответ читателю о фрагменте книги: слова, имена, термины, идиомы — с опорой на глоссарий и библию."""
        entries = self.gl.relevant(source + "\n" + translation)
        msgs = [{"role": "system", "content": self.P.m("explain_system", tgt=self.pair.tgt.name)},
                {"role": "user", "content": self.P.m(
                    "explain_user", gl=self._glossary_text(entries) if entries else "—",
                    bible=self.ws.bible()[:2500] or "—", src=source or "—", tr=translation or "—",
                    frag=fragment or source, q=question or self.P.m("explain_default_q"))}]
        res = self.llm.chat(self._translator(), msgs, temperature=0.3, max_tokens=2000, role="explain")
        return strip_think(res.content).strip()

    # ---------- редактура готового перевода ----------
    def cast_table(self) -> str:
        """Персонажи с подтверждённым полом (для редактора рода)."""
        g = {"m": self.P.m("cast_m"), "f": self.P.m("cast_f")}
        rows = [e for e in self.gl.entries if e.get("kind") == "person" and e.get("gender") in g
                and not e.get("gender_conflict")]
        return "\n".join(f"- {e['target']} — {g[e['gender']]}" + (f" ({e['note']})" if e.get("note") else "")
                         for e in rows)

    def edit_genders(self, n: int, window: int = 35) -> dict:
        """Исправить род персонажей в готовом переводе главы (без оригинала: по таблице персонажей).

        Охрана от переписывания: правка принимается, только если похожесть с исходным абзацем ≥ 0.85 и
        длина изменилась не больше чем на 10% — иначе модель «улучшала» текст, а не род.
        """
        import difflib
        st = self.ws.load_state(n)
        if not st:
            raise ValueError(_("Глава {0}: перевода нет").format(n))
        segs = [s for s in st["segments"] if s.get("tr") and not s.get("dropped")]
        cast = self.cast_table()
        if not cast:
            raise ValueError(_("Таблица персонажей пуста: задайте пол через glossary_set (kind=person, gender)"))
        sys_prompt = self.P.gender_editor(cast)
        wins = [segs[max(0, i - 2):i + window] for i in range(0, len(segs), window)]
        by_id = {s["id"]: s for s in segs}
        applied, rejected = [], []

        def run(win):
            body = "\n".join(f"[{s['id']}] {s['tr']}" for s in win)
            try:
                res = self.llm.chat(self.cfg.role("reviewer")[0],
                                    [{"role": "system", "content": sys_prompt},
                                     {"role": "user", "content": self.P.m("ge_user", n=n, body=body)}],
                                    temperature=0.1, json_mode=True, max_tokens=12000, role="editor")
                return (parse_json_lenient(res.content) or {}).get("fixes", [])
            except LLMError as e:
                self.log(_("гл.{0}: редактор пропустил окно ({1})").format(n, e))
                return []

        with ThreadPoolExecutor(max_workers=min(3, len(wins) or 1)) as ex:
            for fixes in ex.map(run, wins):
                for f in fixes:
                    try:
                        s = by_id[int(f.get("id"))]
                    except (KeyError, TypeError, ValueError):
                        continue
                    new = str(f.get("fixed") or "").strip()
                    old = s["tr"]
                    if not new or new == old:
                        continue
                    ratio = difflib.SequenceMatcher(None, old, new).ratio()
                    if ratio < 0.85 or abs(len(new) - len(old)) > 0.1 * len(old) + 3:
                        rejected.append({"id": s["id"], "ratio": round(ratio, 2)})
                        continue
                    s.setdefault("history", []).append({"by": "gender-editor", "old": old,
                                                        "why": str(f.get("what", ""))[:120]})
                    s["tr"], s["by"] = new, "gender-editor"
                    applied.append({"id": s["id"], "what": str(f.get("what", ""))[:100]})
        # Второй проход: то, что модель пропустила, но видит детектор («Ло Вэй спросил»), — адресно по абзацу.
        from .checks import gender_issues
        persons_check = self.pair.ru_target  # детектор рода — морфология русского; для остальных только редактор
        persons = [e for e in self.gl.entries if e.get("kind") == "person" and e.get("gender") in ("m", "f")]
        second = 0
        for s in segs if persons_check else []:
            iss = gender_issues(s["tr"], persons)
            if not iss:
                continue
            hint = "; ".join(i["msg"] for i in iss)
            msgs = [{"role": "system", "content": sys_prompt.split(self.P.m("ge_split"))[0]
                     + self.P.m("ge_single_tail")},
                    {"role": "user", "content": self.P.m("ge_single_user", tr=s["tr"], hint=hint)}]
            try:
                res = self.llm.chat(self.cfg.role("reviewer")[0], msgs, temperature=0.1, max_tokens=4000,
                                    role="editor")
            except LLMError:
                continue
            new = strip_think(res.content).strip().strip("`").strip()
            old = s["tr"]
            ratio = difflib.SequenceMatcher(None, old, new).ratio()
            if new and new != old and ratio >= 0.85 and abs(len(new) - len(old)) <= 0.1 * len(old) + 3 \
                    and len(gender_issues(new, persons)) < len(iss):
                s.setdefault("history", []).append({"by": "gender-editor", "old": old, "why": hint[:120]})
                s["tr"], s["by"] = new, "gender-editor"
                second += 1
        if applied or second:
            st["stage"] = "done"
            self.ws.save_state(n, st)
        return {"chapter": n, "fixed": len(applied) + second, "second_pass": second, "rejected": len(rejected),
                "examples": applied[:8]}

    def normalize_chapter(self, n: int) -> int:
        """Только детерминированные правки без LLM (пунктуация, «500 миллионов» → цифры). → сколько абзацев изменено."""
        st = self.ws.load_state(n)
        if not st or not all(s.get("tr") for s in st["segments"]):
            return 0
        before = [s["tr"] for s in st["segments"]]
        self._normalize(st["segments"])
        changed = sum(1 for a, s in zip(before, st["segments"]) if a != s["tr"])
        if changed:
            st["stage"] = "done"
            self.ws.save_state(n, st)
        return changed

    def repair_chapter(self, n: int, review: bool = False) -> dict:
        """Починить уже готовый перевод главы (свой или импортированный, например Hermes): нормализация +
        адресная починка hard-ошибок (+ опционально рецензент). Корректные абзацы не трогаются."""
        st = self.ws.load_state(n)
        if not st:
            raise ValueError(_("Глава {0}: перевода нет").format(n))
        segs = st["segments"]
        before = check_chapter(st, self.gl, pair=self.pair)["hard"]
        entries = self.gl.relevant("\n".join(s["src"] for s in segs))
        self._normalize(segs)
        self._repair(n, segs, entries)
        applied = self._review(n, segs, flagged_only=True) if review else []
        st["stage"] = "done"
        self.ws.save_state(n, st)
        after = check_chapter(st, self.gl, pair=self.pair)["hard"]
        return {"chapter": n, "hard_before": before, "hard_after": after, "review_fixes": len(applied)}

    def review_chapter(self, n: int, instruction: str = "") -> dict:
        st = self.ws.load_state(n)
        if not st or st.get("unaligned"):
            raise ValueError(_("Глава {0}: нет посегментного перевода").format(n))
        applied = self._review(n, st["segments"], instruction=instruction)
        self.ws.save_state(n, st)
        return {"chapter": n, "fixes": applied}

    def translate_text(self, text: str) -> str:
        from .workspace import split_paragraphs
        src = split_paragraphs(text)
        return "\n\n".join(t or _("[НЕ ПЕРЕВЕДЕНО] {0}").format(s) for s, t in zip(src, self.translate_lines(src)))

    def translate_lines(self, lines: list[str]) -> list[str | None]:
        """Перевести список строк (абзацы, заголовки) по глоссарию книги и с починкой; None — не удалось."""
        segs = [{"id": i + 1, "src": p, "tr": None} for i, p in enumerate(lines)]
        entries = self.gl.relevant("\n".join(lines))
        for b in self._batches(segs):
            first = segs.index(b[0])
            self._fill(0, b, [s for s in segs[max(0, first - self.seam):first] if s.get("tr")], [], entries)
        self._repair(0, segs, entries)
        return [s.get("tr") for s in segs]

    # ---------- оглавление на языке перевода ----------
    def toc_path(self) -> Path:
        return self.ws.meta_dir / f"toc_{self.pair.tgt.code}.json"

    def translated_toc(self) -> dict[int, str]:
        return {int(k): v for k, v in (read_json(self.toc_path(), {}) or {}).items()}

    def translate_toc(self, chunk: int = 60) -> int:
        """Заголовки ещё не переведённых глав — чтобы оглавление сразу было на языке читателя. Сохраняется
        после каждой пачки (прерванный перевод не теряется). → сколько заголовков переведено сейчас."""
        if self.pair.src.code == self.pair.tgt.code:
            return 0
        titles = {x["n"]: x["title"] for x in (read_json(self.ws.meta_dir / "source_map.json", []) or [])
                  if x.get("title")}
        done = self.translated_toc()
        todo = [n for n in self.ws.chapters() if n in titles and n not in done and not self.ws.is_translated(n)]
        count = 0
        for i in range(0, len(todo), chunk):
            part = todo[i:i + chunk]
            for n, tr in zip(part, self.translate_lines([titles[n] for n in part])):
                if tr:
                    done[n] = tr.strip()
                    count += 1
            atomic_write(self.toc_path(), json.dumps({str(k): v for k, v in sorted(done.items())},
                                                    ensure_ascii=False, indent=1))
            self.emit("toc_progress", done=min(i + chunk, len(todo)), total=len(todo))
        return count

    # ---------- извлечение терминов ----------
    def _scanned_path(self) -> Path:
        return self.ws.meta_dir / "scanned.json"

    def _scanned(self) -> set[int]:
        p = self._scanned_path()
        return set(read_json(p, []) or [])

    def extract_terms(self, chapters: list[int], only_new_chapters: bool = False, auto_add: bool = True) -> dict:
        scanned = self._scanned()
        todo = [n for n in chapters if not (only_new_chapters and n in scanned)]
        if not todo:
            return {"added": [], "candidates": []}
        known = [e["source"] for e in self.gl.entries]
        ref = self.cfg.role("extractor")[0]
        texts = {n: "\n".join(self.ws.source_paragraphs(n)) for n in todo}
        self.emit("extract_start", chapters=len(todo))

        def one(n):
            msgs = [{"role": "system", "content": self.P.extractor()},
                    {"role": "user", "content": self.P.m("ex_user", known=self.P.m("ex_sep").join(known[:400]) or "—",
                                                         n=n, text=texts[n])}]
            try:
                res = self.llm.chat(ref, msgs, temperature=0.1, json_mode=True, max_tokens=6000, role="extractor")
                return n, (parse_json_lenient(res.content) or {}).get("terms", [])
            except LLMError as e:
                self.log(_("гл.{0}: извлечение терминов не удалось ({1})").format(n, e))
                return n, None

        with ThreadPoolExecutor(max_workers=min(8, len(todo))) as ex:
            results = list(ex.map(one, todo))
        merged: dict[str, dict] = {}
        ok_chapters = []
        for n, terms in results:
            if terms is None:
                continue
            ok_chapters.append(n)
            for t in terms:
                if not isinstance(t, dict):
                    continue
                src = str(t.get("source", "")).strip()
                if not src or self.gl.get(src) or not self._term_like(src):
                    continue
                m = merged.setdefault(src, {**t, "source": src, "chapters": set()})
                m["chapters"].add(n)
        all_text = "\n".join(texts.values())
        candidates = []
        for src, t in merged.items():
            cand = self._candidate(t) if self.pair.name_system else self._candidate_llm(t)
            cand["count"] = all_text.count(src)
            cand["chapters"] = sorted(t["chapters"])[:10]
            candidates.append(cand)
        candidates.sort(key=lambda c: -c["count"])
        added, pending = [], []
        if auto_add:
            for c in candidates:
                if not c.get("target"):
                    continue
                # Контракт — только имена (их проверяет код). Термины ждут решения агента: status=candidate
                # (в промт переводчика не идут).
                is_name = (c["kind"] in ("person", "place", "org") and (c["kind"] == "person" or c["count"] >= 2)
                           and c.get("how") != "suspicious")
                status = "auto" if is_name else "candidate"
                self.gl.upsert(c["source"], c["target"], c["kind"], c.get("gender"), c.get("note"), status=status)
                line = f"{c['source']} → {c['target']} ({c['kind']}, ×{c['count']})"
                (added if is_name else pending).append(line)
            self.gl.save()
            atomic_write(self._scanned_path(), json.dumps(sorted(scanned | set(ok_chapters))))
        self.emit("extract_done", added=len(added), pending=len(pending))
        return {"added": added, "pending_terms": pending, "candidates": candidates}

    def _term_like(self, src: str) -> bool:
        """Кандидат в глоссарий: для китайского/японского/корейского — короткая запись в письме оригинала,
        для остальных — короткая фраза (имя, название), а не предложение."""
        if self.pair.src.cjk:
            return bool(self.pair.leftover_re.search(src) if self.pair.leftover_re else han_count(src)) \
                and len(src) <= 16
        return len(src) <= 60 and len(src.split()) <= 6

    def verify_genders(self, mark: bool = True) -> list[dict]:
        """Сверка пола персонажей глоссария с уликами оригинала (родство, обращения, титулы).

        Конфликт помечается gender_conflict: спорный пол перестаёт навязываться переводчику и проверке рода,
        пока агент не решит (glossary_set). Урок «цветочка»: агент уверенно записал младшего брата
        (许纯，许冰的弟弟) женщиной.
        """
        if self.pair.src.code != "zh":  # улики пола — китайские (他/她, родство «X，Y的弟弟»)
            return []
        from .checks import gender_conflicts
        paras = [p for n in self.ws.chapters() for p in self.ws.source_paragraphs(n)]
        conflicts = gender_conflicts(self.gl, paras)
        # Пол не указан, а улики однозначны — это не конфликт, а пробел: заполняем (братья Люмьер → m).
        filled = [c for c in conflicts if not c.get("glossary_gender")]
        for c in filled:
            e = self.gl.get(c["source"])
            if e is not None:
                e["gender"] = c["suggested"]
                e["note"] = ((e.get("note") or "") + _("; пол — по уликам оригинала")).strip("; ")
        conflicts = [c for c in conflicts if c.get("glossary_gender")]
        if filled and not mark:
            self.gl.save()
        if mark:
            bad = {c["source"]: c["suggested"] for c in conflicts}
            for e in self.gl.entries:
                if e["source"] in bad:
                    e["gender_conflict"] = bad[e["source"]]
                else:
                    e.pop("gender_conflict", None)
            self.gl.save()
        if conflicts:
            self.log(_("пол в глоссарии противоречит оригиналу: ")
                     + ", ".join(f"{c['target']} ({c['glossary_gender']}→{c['suggested']}?)" for c in conflicts))
        return conflicts

    def resolve_candidates(self, chapters: list[int] | None = None, limit: int = 80) -> dict:
        """Разбор кандидатов (status=candidate/auto) ОДНИМ вызовом модели со свежим контекстом.

        Агент делал это вручную десятками шагов (каждый шаг — вся история заново). Здесь: кандидаты +
        по 2 фрагмента оригинала → решения JSON → применяем. Имена людей не трогаем: их пишет Палладий.
        """
        cands = [e for e in self.gl.entries if e.get("status") in ("candidate", "auto")][:limit]
        if not cands:
            return {"resolved": 0}
        chs = chapters or list(self.ws.chapters())
        texts = {n: self.ws.source_paragraphs(n) for n in chs}
        lines = []
        for i, e in enumerate(cands, 1):
            ctx = []
            for n, paras in texts.items():
                for p in paras:
                    if e["source"] in p:
                        k = p.find(e["source"])
                        ctx.append(p[max(0, k - 40):k + len(e["source"]) + 40])
                        break
                if len(ctx) >= 2:
                    break
            lines.append(self.P.m("rs_line", i=i, source=e["source"], target=e["target"], kind=e.get("kind"),
                                  note=e.get("note", ""), ctx=" | ".join(ctx)))
        sys = self.P.glossary_resolver()
        bible = self.ws.bible()[:3000]
        res = self.llm.chat(self.cfg.role("reviewer")[0],
                            [{"role": "system", "content": sys},
                             {"role": "user", "content": self.P.m("rs_user", bible=bible, lines="\n".join(lines))}],
                            temperature=0.2, json_mode=True, max_tokens=12000, role="glossary")
        data = parse_json_lenient(res.content) or {}
        stats = {"keep": 0, "fix": 0, "drop": 0}
        changes = []
        for d in data.get("decisions", []):
            try:
                e = cands[int(d.get("n")) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            act = d.get("action")
            if act == "drop":
                self.gl.remove(e["source"])
                changes.append(f"✗ {e['source']}")
            elif act in ("keep", "fix"):
                target = e["target"]
                pall = {self.translit(e["source"], k) for k in ("person", "other")} - {None}
                if act == "fix" and d.get("target") and e["target"] in pall:
                    # Имя уже совпадает с Палладием — LLM его не «исправляет» (было: 瑩瑩 Инъин → «Инин»).
                    changes.append(_("⊘ {0}: LLM предлагала «{1}», оставлен Палладий «{2}»").format(e['source'], d['target'], target))
                elif act == "fix" and d.get("target"):
                    target = re.sub(r"\s*\([^)]*\)", "", str(d["target"])).strip() or target
                    if target != e["target"]:
                        changes.append(f"✎ {e['source']}: {e['target']} → {target}")
                self.gl.upsert(e["source"], target, d.get("kind") or e.get("kind", "other"),
                               d.get("gender") if d.get("gender") in ("m", "f", "n") else e.get("gender"),
                               e.get("note"), status="confirmed")
            else:
                continue
            stats[act] = stats.get(act, 0) + 1
        self.gl.save()
        left = sum(1 for e in self.gl.entries if e.get("status") in ("candidate", "auto"))
        return {**stats, "changes": changes[:40], "still_unreviewed": left}

    @staticmethod
    def _candidate_llm(t: dict) -> dict:
        """Пары без детерминированной транскрипции: вариант модели как есть."""
        kind = t.get("kind", "other")
        kind = kind if kind in ("person", "place", "org", "term", "title", "item", "skill", "other") else "other"
        gender = t.get("gender") if t.get("gender") in ("m", "f", "n") else None
        target = re.sub(r"\s*\([^)]*\)", "", str(t.get("target") or t.get("ru") or "")).strip()
        return {"source": t["source"], "target": target, "kind": kind, "gender": gender,
                "note": str(t.get("note") or "").strip()[:120], "how": "llm"}

    def _candidate(self, t: dict) -> dict:
        src = t["source"]
        kind = t.get("kind", "other")
        kind = kind if kind in ("person", "place", "org", "term", "title", "item", "skill", "other") else "other"
        gender = t.get("gender") if t.get("gender") in ("m", "f", "n") else None
        ru = re.sub(r"\s*\([^)]*\)", "", str(t.get("target") or t.get("ru") or "")).strip()
        target, how = ru, "llm"
        titled = any(src.endswith(x) for x in _TITLE_SUFFIXES) and len(src) >= 2
        affix = len(src) == 2 and src[0] in "小老阿" and src != "小白" and src[1] in _SURNAME_CHARS
        if kind == "person" and (titled or affix):
            # «余經理» — не имя «Юй Цзинли», а «управляющий Юй»; «老黃» — «старина Хуан»: берём версию LLM,
            # но фамилию проверяем Палладием.
            sur = self.translit(src[1] if affix else src[0], "other")
            title_cyr = (self.translit(src[1:], "other") or "") if titled else ""
            if ru and title_cyr and title_cyr.lower() in ru.lower():
                target, how = ru, "suspicious"  # должность транслитерирована («Юй Цзинли») — пусть решит агент
            elif ru and sur in ru:
                target, how = ru, "llm+palladius"
            else:
                target, how = ru or sur, "llm"
                note = (str(t.get("note") or "") + _("; фамилия по Палладию: {0}").format(sur)).strip("; ")
                t = {**t, "note": note}
        elif kind == "person":
            form = t.get("name_form", "full")
            if form == "full" and 2 <= len(src) <= 4:
                target, how = self.translit(src, "person"), "palladius"
            elif form == "given":
                target, how = self.translit(src, "other"), "palladius"
        elif kind == "place" and len(src) <= 3 and not (set(src) & _PLACE_SUFFIX - set(src[:1])):
            target, how = self.translit(src, "place"), "palladius"
        note = str(t.get("note") or "").strip()
        if how == "palladius" and ru and ru != target:
            note = (note + _("; LLM предлагала «{0}»").format(ru)).strip("; ")
        return {"source": src, "target": target, "kind": kind, "gender": gender, "note": note[:120], "how": how}


def _ranges(nums: list[int]) -> str:
    """[1,2,3,5,7,8] → '1-3,5,7-8' — компактно для отчётов по длинным книгам."""
    nums = sorted(set(nums))
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(f"{nums[i]}-{nums[j]}" if j > i else str(nums[i]))
        i = j + 1
    return ",".join(out)
