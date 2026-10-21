"""Генерирует docs/PROTOCOL.md из реестра инструментов (hanru/tools.py)."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from hanru.config import Config  # noqa: E402
from hanru.engine import Engine  # noqa: E402
from hanru.llm import LLMClient  # noqa: E402
from hanru.tools import Toolbox  # noqa: E402
from hanru.workspace import Workspace  # noqa: E402

tb = Toolbox(Engine(Workspace(tempfile.mkdtemp()), Config.load(), LLMClient(1)))
lines = ["# Протокол инструментов Hanru", "",
         "Сгенерировано `eval/gen_protocol.py` из `hanru/tools.py`. Один и тот же реестр используется агентом",
         "(OpenAI `tools` или текстовый `<tool_call>`) и MCP-сервером (`python -m hanru.mcp_server <книга>`).", "",
         "Текстовый протокол для моделей без нативных инструментов:",
         "```", '<tool_call>{"name": "translate", "arguments": {"chapters": "1-5"}}</tool_call>', "```",
         "Результат возвращается сообщением `<tool_result name=\"translate\">…</tool_result>`.", "",
         "| инструмент | меняет данные | аргументы | описание |", "|---|---|---|---|"]
for t in tb.tools.values():
    args = ", ".join(f"`{k}`{'*' if k in t.required else ''}: {v.get('type', 'any')}"
                     for k, v in t.params.items()) or "—"
    lines.append(f"| `{t.name}` | {'да' if t.mutating else ''} | {args} | {t.description} |")
lines += ["", "`*` — обязательный аргумент. Диапазон глав: `'1-5,8'` или `'all'`."]
(ROOT / "docs/PROTOCOL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(len(tb.tools), "инструментов")
