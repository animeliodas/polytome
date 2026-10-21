"""Провайдеры, слоты моделей, профили.

Для пользователя три понятия:
- ПРОВАЙДЕР — откуда модели (каталог готовых ниже; любой OpenAI-совместимый сервер добавляется в "providers").
- СЛОТЫ — какие модели у вас есть: strong (основной переводчик), fast (дешёвая быстрая), agent (ведёт диалог),
  reviewer, extractor, local. Заполняются один раз: `polytome setup`.
- ПРОФИЛЬ — как тщательно работать: quality | balanced | economy | local (`polytome profiles`).
Продвинутым: явные "roles" в конфиге перекрывают профиль.

Где лежит конфиг: $POLYTOME_HOME, иначе папка программы (если там есть polytome.json), иначе ~/.polytome.
Ключи — в secrets.json рядом с конфигом (или переменные окружения), в polytome.json их нет.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import _

PKG_DIR = Path(__file__).resolve().parent
APP_DIR = PKG_DIR.parent


def polytome_home() -> Path:
    if os.environ.get("POLYTOME_HOME"):
        return Path(os.environ["POLYTOME_HOME"])
    if (APP_DIR / "polytome.json").is_file():
        return APP_DIR
    return Path.home() / ".polytome"


# Готовые провайдеры. Все — OpenAI-совместимый /chat/completions.
PROVIDER_CATALOG: dict[str, dict] = {
    "ollama-cloud": {"title": "Ollama Cloud", "base_url": "https://ollama.com/v1", "api_key_env": "OLLAMA_API_KEY",
                     "keys_url": "https://ollama.com/settings/keys",
                     "suggest": {"strong": "kimi-k3", "fast": "deepseek-v4.1-flash", "agent": "glm-5.3",
                                 "reviewer": "glm-5.3", "extractor": "deepseek-v4.1-flash"}},
    "ollama": {"title": _("Ollama на этом ПК (бесплатно)"), "base_url": "http://localhost:11434/v1", "local": True,
               "suggest": {"local": "qwen3:14b"}},
    "lmstudio": {"title": _("LM Studio на этом ПК"), "base_url": "http://localhost:1234/v1", "local": True},
    "llamacpp": {"title": "llama.cpp server", "base_url": "http://localhost:8080/v1", "local": True},
    "vllm": {"title": "vLLM", "base_url": "http://localhost:8000/v1", "local": True},
    "openrouter": {"title": _("OpenRouter (сотни моделей по одному ключу)"), "base_url": "https://openrouter.ai/api/v1",
                   "api_key_env": "OPENROUTER_API_KEY", "keys_url": "https://openrouter.ai/keys"},
    "openai": {"title": "OpenAI", "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY",
               "keys_url": "https://platform.openai.com/api-keys"},
    "anthropic": {"title": "Anthropic (Claude)", "base_url": "https://api.anthropic.com/v1",
                  "api_key_env": "ANTHROPIC_API_KEY", "keys_url": "https://console.anthropic.com/settings/keys",
                  "suggest": {"strong": "claude-sonnet-5-5", "fast": "claude-haiku-5-5", "agent": "claude-sonnet-5-5",
                              "reviewer": "claude-haiku-5-5", "extractor": "claude-haiku-5-5"}},
    "gemini": {"title": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "api_key_env": "GEMINI_API_KEY", "keys_url": "https://aistudio.google.com/apikey"},
    "deepseek": {"title": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY",
                 "keys_url": "https://platform.deepseek.com/api_keys"},
    "mistral": {"title": "Mistral", "base_url": "https://api.mistral.ai/v1", "api_key_env": "MISTRAL_API_KEY"},
    "groq": {"title": "Groq", "base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"},
    "together": {"title": "Together AI", "base_url": "https://api.together.xyz/v1", "api_key_env": "TOGETHER_API_KEY"},
    "aitunnel": {"title": "AITunnel (RU)", "base_url": "https://api.aitunnel.ru/v1", "api_key_env": "AITUNNEL_API_KEY"},
}

ROLES = ("agent", "translator", "reviewer", "extractor")
SLOTS = ("strong", "fast", "agent", "reviewer", "extractor", "local")

# Профиль: роль → (слот, reasoning_effort или None = как у модели по умолчанию) + параметры движка.
PROFILES: dict[str, dict] = {
    "quality": {
        "desc": _("максимум качества: переводчик с полным рассуждением, рецензент на всю главу (медленно, "
                  "много токенов)"),
        "roles": {"translator": ("strong", None), "reviewer": ("reviewer", "low"), "extractor": ("extractor", "low"),
                  "agent": ("agent", None)},
        "engine": {"review": "full", "batch_chars": 1500, "repair_attempts": 2}},
    "balanced": {
        "desc": _("по умолчанию: сильный переводчик без долгих рассуждений + рецензент на всю главу"),
        "roles": {"translator": ("strong", "low"), "reviewer": ("reviewer", "low"), "extractor": ("extractor", "low"),
                  "agent": ("agent", None)},
        "engine": {"review": "full", "batch_chars": 1500, "repair_attempts": 2}},
    "economy": {
        "desc": _("экономия токенов в 2–3 раза: быстрая модель, крупные батчи, рецензент только по местам с "
                  "замечаниями"),
        "roles": {"translator": ("fast", "low"), "reviewer": ("fast", "low"), "extractor": ("fast", "low"),
                  "agent": ("fast", "low")},
        "engine": {"review": "flagged", "batch_chars": 2600, "repair_attempts": 1, "parallel": 3}},
    "local": {
        "desc": _("всё на вашем ПК (Ollama/LM Studio): бесплатно и приватно, но медленно; нужна модель от ~14B"),
        "roles": {"translator": ("local", None), "reviewer": ("local", None), "extractor": ("local", None),
                  "agent": ("local", None)},
        "engine": {"review": "flagged", "batch_chars": 900, "repair_attempts": 1, "parallel": 1},
        "tool_protocol": "auto"},
}

# Известные особенности моделей (замеры в JOURNAL.md): модельные параметры важнее профильных.
KNOWN_MODEL_OPTIONS = {
    "deepseek-v4.1-flash": {"reasoning_effort": "none"},  # с low всё равно думает ~12k символов
}

DEFAULT_CONFIG = {
    "profile": "balanced",
    "slots": {"strong": "ollama-cloud:kimi-k3", "fast": "ollama-cloud:deepseek-v4.1-flash",
              "agent": "ollama-cloud:glm-5.3", "reviewer": "ollama-cloud:glm-5.3",
              "extractor": "ollama-cloud:deepseek-v4.1-flash", "local": "ollama:qwen3:14b"},
    "providers": {},          # свои/переопределённые провайдеры; каталог встроен
    "models": {},             # опции модели: {"<провайдер:модель>": {"temperature":..., "extra": {...}}}
    "engine": {},             # переопределения параметров движка поверх профиля
    "style": {"numbers": "auto"},    # digits | natural | auto (русский — цифрами, остальные — как принято)
    "target_lang": "",               # язык перевода по умолчанию для новых книг; пусто — язык системы
    "tool_protocol": "auto",  # native | text | auto
    "parallel": 8,            # одновременных запросов к LLM (общий пул); профиль может уменьшить
}


@dataclass
class ModelRef:
    provider: str
    model: str
    base_url: str
    api_key: str | None
    temperature: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def spec(self) -> str:
        return f"{self.provider}:{self.model}"


def _merged(user: dict) -> dict:
    data = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in (user or {}).items():
        if isinstance(v, dict) and isinstance(data.get(k), dict):
            data[k].update(v)
        else:
            data[k] = v
    return data


def _read_key_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8-sig").strip()
    return text.split()[-1] if text else None  # «ollama key - XXXX» и просто «XXXX»


class Config:
    def __init__(self, data: dict, base_dir: Path, path: Path | None = None):
        self.data = data
        self.base_dir = base_dir
        self.path = path or base_dir / "polytome.json"
        self.forced_profile: str | None = None  # профиль, явно заданный на запуск: важнее профиля книги

    # ---------- загрузка / сохранение ----------
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        p = Path(path or os.environ.get("POLYTOME_CONFIG") or polytome_home() / "polytome.json")
        user = json.loads(p.read_text(encoding="utf-8-sig")) if p.is_file() else {}
        return cls(_merged(user), p.parent, p)

    @classmethod
    def from_dict(cls, user: dict, home: str | Path | None = None) -> "Config":
        """Конфиг целиком в памяти (для приложений): ключи — в providers.<id>.api_key, на диск ничего не пишется."""
        base = Path(home) if home else polytome_home()
        return cls(_merged(user), base, base / "polytome.json")

    def save(self) -> Path:
        from .workspace import atomic_write
        defaults = DEFAULT_CONFIG
        # Сохраняем только отличия от значений по умолчанию — файл остаётся коротким и читаемым.
        out = {k: v for k, v in self.data.items() if v != defaults.get(k)}
        atomic_write(self.path, json.dumps(out, ensure_ascii=False, indent=2))
        return self.path

    @property
    def target_lang(self) -> str:
        from .lang import normalize, system_lang
        return normalize(self.data.get("target_lang") or "") or system_lang()

    def numbers_style(self, pair) -> str:
        v = (self.data.get("style") or {}).get("numbers", "auto")
        if v == "auto":
            return "digits" if pair.ru_target else "natural"
        return v

    def style_text(self, pair=None) -> str:
        """Общие правила перевода: style_text из конфига → STYLE.<язык>.md / STYLE.md рядом с конфигом →
        встроенные styles/<язык>.md → styles/default.md."""
        if self.data.get("style_text"):
            return self.data["style_text"]
        tgt = pair.tgt.code if pair else "ru"
        for p in (self.base_dir / f"STYLE.{tgt}.md", self.base_dir / "STYLE.md", PKG_DIR / "styles" / f"{tgt}.md",
                  PKG_DIR / "styles" / "default.md"):
            if p.is_file():
                return p.read_text(encoding="utf-8")
        return ""

    @property
    def secrets_path(self) -> Path:
        return self.base_dir / "secrets.json"

    def secrets(self) -> dict:
        from .workspace import read_json
        return read_json(self.secrets_path, {}) or {}

    def set_secret(self, provider: str, key: str):
        from .workspace import atomic_write
        s = self.secrets()
        s[provider] = key
        atomic_write(self.secrets_path, json.dumps(s, indent=1))
        try:
            os.chmod(self.secrets_path, 0o600)
        except OSError:
            pass

    def with_overrides(self, roles: dict | None = None, profile: str | None = None) -> "Config":
        if not roles and not profile:
            return self
        data = json.loads(json.dumps(self.data))
        if roles:
            data.setdefault("roles", {}).update(roles)
        if profile:
            data["profile"] = profile
        out = Config(data, self.base_dir, self.path)
        out.forced_profile = profile or self.forced_profile
        return out

    # ---------- профиль ----------
    @property
    def profile_name(self) -> str:
        p = self.data.get("profile", "balanced")
        return p if p in PROFILES else "balanced"

    @property
    def profile(self) -> dict:
        return PROFILES[self.profile_name]

    def engine_options(self) -> dict:
        opts = {"review": "full", "batch_chars": 1500, "repair_attempts": 2}
        opts.update(self.profile.get("engine", {}))
        opts.update(self.data.get("engine") or {})
        return opts

    @property
    def parallel(self) -> int:
        p = int(self.data.get("parallel", 8))
        prof = self.engine_options().get("parallel")
        return min(p, int(prof)) if prof else p

    @property
    def tool_protocol(self) -> str:
        return self.data.get("tool_protocol") or self.profile.get("tool_protocol", "auto")

    # ---------- провайдеры и модели ----------
    def providers(self) -> dict[str, dict]:
        merged = {k: dict(v) for k, v in PROVIDER_CATALOG.items()}
        for k, v in (self.data.get("providers") or {}).items():
            merged.setdefault(k, {}).update(v)
        return merged

    def provider_key(self, prov: str) -> str | None:
        pconf = self.providers().get(prov, {})
        key = pconf.get("api_key")
        if not key and pconf.get("api_key_env"):
            key = os.environ.get(pconf["api_key_env"])
        if not key:
            key = self.secrets().get(prov)
        if not key and pconf.get("api_key_file"):
            key = _read_key_file((self.base_dir / pconf["api_key_file"]).resolve())
        return key

    def resolve(self, spec: str, effort: str | None = None) -> ModelRef:
        if ":" not in spec:
            raise ValueError(_("Модель задаётся как 'провайдер:модель', получено: {0!r}").format(spec))
        prov, model = spec.split(":", 1)
        pconf = self.providers().get(prov)
        if pconf is None:
            raise ValueError(_("Неизвестный провайдер {0!r}. Есть: {1}").format(prov, ', '.join(self.providers())))
        mopts = (self.data.get("models") or {}).get(spec, {})
        extra = {}
        if effort:
            extra["reasoning_effort"] = effort
        extra.update(KNOWN_MODEL_OPTIONS.get(model, {}) if effort else {})
        extra.update(mopts.get("extra", {}))
        return ModelRef(prov, model, pconf["base_url"].rstrip("/"), self.provider_key(prov),
                        mopts.get("temperature"), extra)

    def role_specs(self, name: str) -> list[tuple[str, str | None]]:
        """[(провайдер:модель, effort)] для роли: явные roles → иначе профиль + слоты."""
        explicit = (self.data.get("roles") or {}).get(name)
        if explicit:
            specs = explicit if isinstance(explicit, list) else [explicit]
            effort = (self.data.get("role_options") or {}).get(name, {}).get("reasoning_effort")
            if effort is None and name in ("extractor", "reviewer"):
                effort = "low"
            return [(s, effort) for s in specs]
        slot, effort = self.profile["roles"].get(name, (name, None))
        spec = (self.data.get("slots") or {}).get(slot) or DEFAULT_CONFIG["slots"].get(slot)
        specs = spec if isinstance(spec, list) else [spec]
        return [(s, effort) for s in specs]

    def role(self, name: str) -> list[ModelRef]:
        return [self.resolve(s, e) for s, e in self.role_specs(name)]

    def role_spec(self, name: str) -> str:
        return ", ".join(s + (f"@{e}" if e else "") for s, e in self.role_specs(name))

    def summary(self) -> dict:
        return {"profile": self.profile_name, "profile_desc": self.profile["desc"],
                "roles": {r: self.role_spec(r) for r in ROLES}, "engine": self.engine_options(),
                "config_file": str(self.path)}
