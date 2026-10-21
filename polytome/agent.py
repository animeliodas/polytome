"""Цикл агента: модель ↔ инструменты. Нативный tool-calling или текстовый протокол <tool_call>."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from .i18n import _
from .config import Config
from .engine import parse_json_lenient, strip_think
from .llm import LLMClient, LLMError, ToolCall
from .i18n import ui_lang
from .prompting import agent_system
from .tools import Toolbox
from .workspace import atomic_write

_TEXT_CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
_FOREIGN_CALL = re.compile(r"<[\w:]*invoke\s+name=\"([\w.]+)\"[^>]*>(.*?)</[\w:]*invoke>", re.S)
_FOREIGN_PARAM = re.compile(r"<[\w:]*parameter\s+name=\"(\w+)\"[^>]*>(.*?)</[\w:]*parameter>", re.S)


class NullUI:
    def assistant(self, text: str): pass
    def tool_start(self, name: str, args: dict): pass
    def tool_end(self, name: str, result: str, seconds: float): pass
    def status(self, text: str): pass


def text_protocol_help(toolbox: Toolbox) -> str:
    lines = ["Инструменты вызываются ТЕКСТОМ (нативного вызова нет). Формат, можно несколько подряд:",  # i18n: skip
             '<tool_call>{"name": "имя_инструмента", "arguments": {...}}</tool_call>',  # i18n: skip
             "После вызовов остановись: результаты придут следующим сообщением в <tool_result>.",  # i18n: skip
             "Когда инструменты не нужны — отвечай обычным текстом без <tool_call>.", "", "Инструменты:"]  # i18n: skip
    for t in toolbox.tools.values():
        props = {k: v.get("type", "any") for k, v in t.params.items()}
        lines.append(f"- {t.name}({json.dumps(props, ensure_ascii=False)}) req={list(t.required)} — {t.description}")
    return "\n".join(lines)


def parse_text_calls(content: str) -> list[ToolCall]:
    calls = []
    for i, m in enumerate(_TEXT_CALL.finditer(content or "")):
        data = parse_json_lenient(m.group(1)) or {}
        if data.get("name"):
            args = data.get("arguments") or data.get("args") or {}
            calls.append(ToolCall(f"txt_{i}", data["name"], args if isinstance(args, dict) else {}))
    if not calls:  # «чужие» XML-форматы (например, <atem:invoke name="...">)
        for i, m in enumerate(_FOREIGN_CALL.finditer(content or "")):
            name = m.group(1).split(".")[0]
            args = {}
            for pm in _FOREIGN_PARAM.finditer(m.group(2)):
                v = pm.group(2).strip()
                try:
                    args[pm.group(1)] = json.loads(v)
                except json.JSONDecodeError:
                    args[pm.group(1)] = v
            calls.append(ToolCall(f"xml_{i}", name, args))
    return calls


class Agent:
    def __init__(self, toolbox: Toolbox, cfg: Config, llm: LLMClient, ui=None, protocol: str | None = None):
        self.tb = toolbox
        self.cfg = toolbox.eng.cfg
        self.llm = llm
        self.ui = ui or NullUI()
        self.protocol = protocol or cfg.tool_protocol  # native | text | auto
        self.ref = self.cfg.role("agent")[0]
        self.max_steps = 80
        self.context_chars = 240_000  # ≈ 80k токенов
        self.cancel = threading.Event()
        self.session_path = toolbox.ws.meta_dir / "sessions" / time.strftime("%Y%m%d-%H%M%S.json")
        self.messages: list[dict] = [{"role": "system", "content": self._system()}]

    # ---------- промт ----------
    def _system(self) -> str:
        st = self.tb.ws.status()
        g = self.tb.gl.stats()
        parts = [agent_system(ui_lang()),
                 f"Текущая книга: «{st['title']}» ({st['root']}). Глав: {st['chapters_total']}, переведено: "  # i18n: skip
                 f"{st['chapters_translated']}. Глоссарий: {g['total']} записей (auto без проверки: {g['auto_unreviewed']}). "
                 f"Библия: {'заполнена' if len(self.tb.ws.bible()) > 400 and '(жанр' not in self.tb.ws.bible() else 'пустая/шаблон'}.",  # i18n: skip
                 f"Модели: переводчик {self.cfg.role_spec('translator')}, рецензент {self.cfg.role_spec('reviewer')}."]  # i18n: skip
        style = self.cfg.style_text(self.tb.ws.pair).strip()
        if style:
            parts.append("Обязательные правила заказчика (STYLE.md) — библия не должна им противоречить:\n" + style)  # i18n: skip
        if st.get("interrupted_job"):
            j = st["interrupted_job"]
            parts.append(f"ВНИМАНИЕ: есть незавершённая задача перевода ({j['status']}): главы {j['chapters']}, "  # i18n: skip
                         f"осталось {j['remaining']}. Если пользователь просит продолжить — вызови translate с этими "
                         "главами: готовое не переводится заново, начатые главы продолжатся с места остановки.")
        if self.protocol == "text":
            parts.append(text_protocol_help(self.tb))
        return "\n\n".join(parts)

    def switch_protocol(self, proto: str):
        self.protocol = proto
        self.messages[0] = {"role": "system", "content": self._system()}

    def set_model(self, spec: str):
        self.ref = self.cfg.resolve(spec)

    # ---------- контекст ----------
    def _size(self) -> int:
        return sum(len(m.get("content") or "") + len(json.dumps(m.get("tool_calls", ""), ensure_ascii=False))
                   for m in self.messages)

    def fold_old_results(self, keep_last: int = 6, limit: int = 1500):
        """Сворачивает старые объёмные результаты инструментов (прочитанные главы и т.п.).

        Каждый шаг агента заново отправляет всю историю: 5 прочитанных глав × 25 шагов = 80% токенов
        запуска (см. JOURNAL.md). Через keep_last сообщений крупный результат заменяется заглушкой.
        """
        for m in self.messages[1:-keep_last]:
            c = m.get("content") or ""
            is_result = m["role"] == "tool" or c.startswith("<tool_result")
            if is_result and len(c) > limit and not c.endswith("[свёрнуто]"):
                m["content"] = (c[:400] + f"\n…[результат {len(c)} симв. свёрнут для экономии контекста; "  # i18n: skip
                                "вызови инструмент снова, если нужно] [свёрнуто]")

    def compact(self, force: bool = False):
        self.fold_old_results()
        if not force and self._size() < self.context_chars:
            return
        # 1) обрезаем старые результаты инструментов
        keep_tail = 8
        for m in self.messages[1:-keep_tail]:
            c = m.get("content") or ""
            if (m["role"] == "tool" or c.startswith("<tool_result")) and len(c) > 600:
                m["content"] = c[:600] + " …[обрезано при сжатии контекста]"  # i18n: skip
        if not force and self._size() < self.context_chars * 0.8:
            return
        # 2) сводка середины диалога
        middle = self.messages[1:-keep_tail]
        if len(middle) < 4:
            return
        transcript = "\n".join(f"{m['role']}: {(m.get('content') or '')[:1500]}"
                               + (f" [вызовы: {', '.join(tc['function']['name'] for tc in m.get('tool_calls', []))}]"  # i18n: skip
                                  if m.get("tool_calls") else "") for m in middle)
        try:
            res = self.llm.chat(self.ref, [{"role": "system", "content": "Сожми историю работы агента-переводчика в "  # i18n: skip
                                            "сводку (до 25 строк): что сделано, решения, договорённости с "
                                            "пользователем, открытые задачи."},
                                           {"role": "user", "content": transcript[-60000:]}],
                                temperature=0.2, role="agent")
            summary = strip_think(res.content)
        except LLMError:
            summary = "(сводка недоступна)"  # i18n: skip
        tail = self.messages[-keep_tail:]
        while tail and tail[0]["role"] == "tool":  # не начинаем хвост с осиротевшего результата
            tail = tail[1:]
        self.messages = [self.messages[0], {"role": "user", "content": "[Сводка предыдущей работы]\n" + summary},  # i18n: skip
                         {"role": "assistant", "content": "Понял, продолжаю с учётом сводки."}] + tail  # i18n: skip
        self.ui.status(_("контекст сжат"))

    def save(self):
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.session_path, json.dumps(self.messages, ensure_ascii=False, indent=1))

    def load(self, path: Path):
        self.messages = json.loads(path.read_text(encoding="utf-8"))
        self.messages[0] = {"role": "system", "content": self._system()}
        self.session_path = path

    # ---------- цикл ----------
    def _call_model(self):
        tools = self.tb.schemas() if self.protocol in ("native", "auto") else None
        try:
            return self.llm.chat(self.ref, self.messages, tools=tools, temperature=0.3, role="agent")
        except LLMError as e:
            if tools and self.protocol == "auto" and e.status == 400:
                self.ui.status(_("модель не принимает tools — переключаюсь на текстовый протокол"))
                self.switch_protocol("text")
                return self.llm.chat(self.ref, self.messages, temperature=0.3, role="agent")
            raise

    def run(self, user_text: str) -> str:
        self.cancel.clear()
        self.messages[0] = {"role": "system", "content": self._system()}
        self.messages.append({"role": "user", "content": user_text})
        final = ""
        for _step in range(self.max_steps):
            if self.cancel.is_set():
                final = _("(остановлено пользователем)")
                break
            self.compact()
            res = self._call_model()
            content = strip_think(res.content)
            calls = res.tool_calls
            native = bool(calls)
            if not calls and self.protocol in ("text", "auto"):
                calls = parse_text_calls(content)
                if calls and self.protocol == "auto":
                    self.ui.status(_("модель вызывает инструменты текстом — включаю текстовый протокол"))
                    self.switch_protocol("text")
            visible = _TEXT_CALL.sub("", content).strip() if not native else content
            if visible:
                self.ui.assistant(visible)
            if not calls:
                self.messages.append({"role": "assistant", "content": content})
                final = content
                break
            if native:
                self.messages.append({"role": "assistant", "content": content, "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": c.raw_arguments or json.dumps(c.arguments, ensure_ascii=False)}}
                    for c in calls]})
            else:
                self.messages.append({"role": "assistant", "content": content})
            results = []
            for c in calls:
                if self.cancel.is_set():
                    out = json.dumps({"error": "остановлено пользователем"}, ensure_ascii=False)  # i18n: skip
                else:
                    self.ui.tool_start(c.name, c.arguments)
                    t0 = time.time()
                    out = self.tb.call(c.name, c.arguments)
                    self.ui.tool_end(c.name, out, time.time() - t0)
                if native:
                    self.messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name, "content": out})
                else:
                    results.append(f'<tool_result name="{c.name}">\n{out}\n</tool_result>')
            if results:
                self.messages.append({"role": "user", "content": "\n".join(results)})
            self.save()
        else:
            final = _("(достигнут лимит шагов)")
        self.save()
        return final
