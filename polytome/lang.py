"""Languages and language pairs.

Polytome translates from any language to any language. What changes with the pair is which deterministic
checks apply: Chinese names → Palladius (Cyrillic) for Russian, Hanyu Pinyin for Latin-script targets; Russian
morphology for grammatical gender; leftover-source-script detection when scripts differ, and so on.
Everything that is not language-specific (omissions, numbers, duplicates, glossary contract) works for any pair.
"""
from __future__ import annotations

import locale
import os
import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Lang:
    code: str
    name: str            # English name (used in English prompts)
    native: str          # self-name (shown in UI)
    script: str          # han | kana | hangul | cyrillic | latin | arabic | hebrew | thai | devanagari | greek | ...
    ru_gen: str = ""     # Russian genitive for Russian prompts: «с китайского языка»

    @property
    def cjk(self) -> bool:
        return self.script in ("han", "kana", "hangul")


_L = [
    ("zh", "Chinese", "中文", "han", "китайского"),
    ("ja", "Japanese", "日本語", "kana", "японского"),
    ("ko", "Korean", "한국어", "hangul", "корейского"),
    ("en", "English", "English", "latin", "английского"),
    ("ru", "Russian", "Русский", "cyrillic", "русского"),
    ("uk", "Ukrainian", "Українська", "cyrillic", "украинского"),
    ("be", "Belarusian", "Беларуская", "cyrillic", "белорусского"),
    ("bg", "Bulgarian", "Български", "cyrillic", "болгарского"),
    ("sr", "Serbian", "Српски", "cyrillic", "сербского"),
    ("kk", "Kazakh", "Қазақша", "cyrillic", "казахского"),
    ("es", "Spanish", "Español", "latin", "испанского"),
    ("pt", "Portuguese", "Português", "latin", "португальского"),
    ("fr", "French", "Français", "latin", "французского"),
    ("de", "German", "Deutsch", "latin", "немецкого"),
    ("it", "Italian", "Italiano", "latin", "итальянского"),
    ("nl", "Dutch", "Nederlands", "latin", "нидерландского"),
    ("pl", "Polish", "Polski", "latin", "польского"),
    ("cs", "Czech", "Čeština", "latin", "чешского"),
    ("sk", "Slovak", "Slovenčina", "latin", "словацкого"),
    ("hr", "Croatian", "Hrvatski", "latin", "хорватского"),
    ("sl", "Slovenian", "Slovenščina", "latin", "словенского"),
    ("ro", "Romanian", "Română", "latin", "румынского"),
    ("hu", "Hungarian", "Magyar", "latin", "венгерского"),
    ("fi", "Finnish", "Suomi", "latin", "финского"),
    ("sv", "Swedish", "Svenska", "latin", "шведского"),
    ("no", "Norwegian", "Norsk", "latin", "норвежского"),
    ("da", "Danish", "Dansk", "latin", "датского"),
    ("et", "Estonian", "Eesti", "latin", "эстонского"),
    ("lv", "Latvian", "Latviešu", "latin", "латышского"),
    ("lt", "Lithuanian", "Lietuvių", "latin", "литовского"),
    ("tr", "Turkish", "Türkçe", "latin", "турецкого"),
    ("az", "Azerbaijani", "Azərbaycanca", "latin", "азербайджанского"),
    ("uz", "Uzbek", "Oʻzbekcha", "latin", "узбекского"),
    ("vi", "Vietnamese", "Tiếng Việt", "latin", "вьетнамского"),
    ("id", "Indonesian", "Bahasa Indonesia", "latin", "индонезийского"),
    ("ms", "Malay", "Bahasa Melayu", "latin", "малайского"),
    ("tl", "Filipino", "Filipino", "latin", "филиппинского"),
    ("eo", "Esperanto", "Esperanto", "latin", "эсперанто"),
    ("el", "Greek", "Ελληνικά", "greek", "греческого"),
    ("ar", "Arabic", "العربية", "arabic", "арабского"),
    ("fa", "Persian", "فارسی", "arabic", "персидского"),
    ("ur", "Urdu", "اردو", "arabic", "урду"),
    ("he", "Hebrew", "עברית", "hebrew", "иврита"),
    ("hi", "Hindi", "हिन्दी", "devanagari", "хинди"),
    ("bn", "Bengali", "বাংলা", "bengali", "бенгальского"),
    ("th", "Thai", "ไทย", "thai", "тайского"),
    ("ka", "Georgian", "ქართული", "georgian", "грузинского"),
    ("hy", "Armenian", "Հայերեն", "armenian", "армянского"),
]
LANGS: dict[str, Lang] = {c: Lang(c, n, nat, s, g) for c, n, nat, s, g in _L}

SCRIPT_RE = {
    "han": r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", "kana": r"[\u3040-\u30ff]",
    "hangul": r"[\uac00-\ud7af\u1100-\u11ff]", "cyrillic": r"[\u0400-\u04ff]", "latin": r"[A-Za-z\u00c0-\u024f]",
    "arabic": r"[\u0600-\u06ff]", "hebrew": r"[\u0590-\u05ff]", "thai": r"[\u0e00-\u0e7f]",
    "devanagari": r"[\u0900-\u097f]", "bengali": r"[\u0980-\u09ff]", "greek": r"[\u0370-\u03ff]",
    "georgian": r"[\u10a0-\u10ff]", "armenian": r"[\u0530-\u058f]",
}
_ALIASES = {"chinese": "zh", "zh-cn": "zh", "zh-tw": "zh", "zh-hans": "zh", "zh-hant": "zh", "cn": "zh",
            "japanese": "ja", "jp": "ja", "korean": "ko", "kr": "ko", "english": "en", "russian": "ru",
            "ukrainian": "uk", "ua": "uk", "spanish": "es", "portuguese": "pt", "pt-br": "pt", "french": "fr",
            "german": "de", "italian": "it", "polish": "pl", "turkish": "tr", "vietnamese": "vi", "arabic": "ar",
            "hebrew": "he", "iw": "he", "hindi": "hi", "thai": "th", "indonesian": "id", "in": "id", "nb": "no",
            "nn": "no", "fil": "tl", "greek": "el", "persian": "fa", "dutch": "nl", "czech": "cs",
            "китайский": "zh", "японский": "ja", "корейский": "ko", "английский": "en", "русский": "ru",
            "украинский": "uk", "испанский": "es", "французский": "fr", "немецкий": "de"}


def get(code: str | None) -> Lang:
    """Language by code; unknown codes still work (the model gets the code as the language name)."""
    c = normalize(code) if code else "en"
    return LANGS.get(c) or Lang(c, c, c, "latin", c)


def normalize(value: str | None) -> str:
    """'ru-RU', 'Russian', 'русский', 'zh_Hans' → ISO 639-1 code."""
    if not value:
        return ""
    v = value.strip().lower().replace("_", "-")
    if v in _ALIASES:
        return _ALIASES[v]
    if v in LANGS:
        return v
    for name in (v, v.split("-")[0]):
        if name in _ALIASES:
            return _ALIASES[name]
        if name in LANGS:
            return name
    hit = next((c for c, lg in LANGS.items() if v in (lg.name.lower(), lg.native.lower())), None)
    return hit or v.split("-")[0]


@lru_cache(maxsize=1)
def system_lang() -> str:
    """Language of the user's system (UI locale) → ISO code; English if unknown."""
    for var in ("POLYTOME_LANG", "LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        v = os.environ.get(var, "")
        if v and v.split(".")[0] not in ("C", "POSIX"):
            return normalize(v.split(":")[0].split(".")[0]) or "en"
    if os.name == "nt":
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            name = locale.windows_locale.get(lcid, "")
            if name:
                return normalize(name) or "en"
        except (AttributeError, OSError):
            pass
    try:
        loc = locale.getlocale()[0] or ""
    except ValueError:
        loc = ""
    return normalize(loc.split(".")[0]) or "en"


@dataclass(frozen=True)
class Pair:
    """Source → target language pair: decides prompts and which deterministic checks apply."""
    src: Lang
    tgt: Lang

    @classmethod
    def of(cls, src: str | None, tgt: str | None) -> "Pair":
        return cls(get(src or "zh"), get(tgt or "ru"))

    @property
    def prompt_lang(self) -> str:
        """Language of instructions to the model: Russian prompts are tuned and measured for Russian output
        (JOURNAL.md); everything else uses English prompts."""
        return "ru" if self.tgt.code == "ru" else "en"

    @property
    def name_system(self) -> str | None:
        """Deterministic transliteration of Chinese names: palladius (→ Cyrillic), pinyin (→ Latin script)."""
        if self.src.code != "zh":
            return None
        if self.tgt.code == "ru":  # Палладий — русская система; украинская, болгарская и др. — свои
            return "palladius"
        if self.tgt.script == "latin":
            return "pinyin"
        return None

    @property
    def leftover_re(self) -> re.Pattern | None:
        """Characters of the source script that must not survive in the translation (scripts differ)."""
        if self.src.script == self.tgt.script or self.src.script == "latin":
            return None  # Latin names/brands legitimately stay in many targets
        scripts = [self.src.script] + (["han"] if self.src.script == "kana" else [])
        if self.tgt.script in ("han", "kana") and "han" in scripts:
            scripts = [s for s in scripts if s != "han"]
        pat = "|".join(SCRIPT_RE[s] for s in scripts if s in SCRIPT_RE)
        return re.compile(pat) if pat else None

    @property
    def cjk_to_alphabetic(self) -> bool:
        return self.src.cjk and not self.tgt.cjk

    @property
    def ru_target(self) -> bool:
        return self.tgt.code == "ru"

    @property
    def target_word_re(self) -> re.Pattern:
        """Capitalised words of the target (candidates for names in the translation)."""
        if self.tgt.script == "cyrillic":
            return re.compile(r"[А-ЯЁІЇЄҐЎ][а-яёіїєґў]+(?:-[А-ЯЁа-яё][а-яё]+)?")
        if self.tgt.script == "latin":
            return re.compile(r"[A-ZÀ-ÞĀ-Ž][a-zß-ÿā-ž']+(?:-[A-ZÀ-Þa-zß-ÿ]+)?")
        return re.compile(r"\w+")


def describe(code: str) -> str:
    lg = get(code)
    return f"{lg.native} ({lg.code})" if lg.native != lg.code else lg.code


__all__ = ["Lang", "LANGS", "Pair", "get", "normalize", "system_lang", "describe", "SCRIPT_RE"]
