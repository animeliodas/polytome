"""Язык интерфейса (CLI, сообщения API, папки новых книг): русский для русскоязычной системы, иначе английский.

Переопределить: переменная окружения POLYTOME_UI_LANG=en|ru (или POLYTOME_LANG — язык системы целиком).
"""
from __future__ import annotations

import os

from .lang import normalize, system_lang

UI_LANGS = ("en", "ru")


def ui_lang() -> str:
    v = normalize(os.environ.get("POLYTOME_UI_LANG", "")) or system_lang()
    return v if v in UI_LANGS else "en"


def folder_names() -> tuple[str, str]:
    """Папки оригинала и перевода для новых книг."""
    return ("Оригинал", "Перевод") if ui_lang() == "ru" else ("Original", "Translation")


def chapter_file() -> str:
    """Шаблон имени файла главы для новых книг."""
    return "Глава {n}.txt" if ui_lang() == "ru" else "Chapter {n}.txt"


_CATALOG: dict[str, str] | None = None


def _(msg: str) -> str:
    """Строка интерфейса на языке пользователя. Ключ — русский текст (исходный язык проекта); английский —
    polytome/locales/en.json. Нет перевода — русский текст (tools/i18n_wrap.py check находит такие места)."""
    if ui_lang() == "ru":
        return msg
    global _CATALOG
    if _CATALOG is None:
        import json
        from pathlib import Path
        p = Path(__file__).with_name("locales") / "en.json"
        _CATALOG = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    return _CATALOG.get(msg) or msg
