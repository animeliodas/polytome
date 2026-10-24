"""Polytome — переводчик книг на основе LLM с детерминированными проверками (имена, пол, числа).

Для встраивания в приложения: `from polytome import Polytome` (см. polytome/api.py и docs/INTEGRATION.md).
"""
__version__ = "0.5.0"


def __getattr__(name):  # ленивый импорт: `import polytome` не тянет движок и httpx, пока API не нужен
    if name in ("Polytome", "PolytomeError", "API_VERSION"):
        from . import api
        return getattr(api, name)
    raise AttributeError(name)
