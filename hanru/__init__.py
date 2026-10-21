"""Hanru — переводчик книг на основе LLM с детерминированными проверками (имена, пол, числа).

Для встраивания в приложения: `from hanru import Hanru` (см. hanru/api.py и docs/INTEGRATION.md).
"""
__version__ = "0.3.1"


def __getattr__(name):  # ленивый импорт: `import hanru` не тянет движок и httpx, пока API не нужен
    if name in ("Hanru", "HanruError", "API_VERSION"):
        from . import api
        return getattr(api, name)
    raise AttributeError(name)
