"""Deterministic transcription of Chinese names.

- palladius: Cyrillic (Russian standard): hanzi --pypinyin--> pinyin syllables --table--> Cyrillic.
- pinyin: Hanyu Pinyin without tone marks for Latin-script targets: Li Xiaobai, Qingzhou, Xi'an.
LLMs mix up «Синья/Синя/Синъя» or "Xinya/Xin'ya" — code does not.
"""
from __future__ import annotations

import re

from pypinyin import Style, lazy_pinyin

# Двусложные фамилии: первые два иероглифа — фамилия.
COMPOUND_SURNAMES = {
    "欧阳", "歐陽", "司马", "司馬", "上官", "诸葛", "諸葛", "慕容", "东方", "東方", "南宫", "南宮",
    "西门", "西門", "令狐", "皇甫", "公孙", "公孫", "端木", "独孤", "獨孤", "尉迟", "尉遲",
    "长孙", "長孫", "宇文", "夏侯", "轩辕", "軒轅", "司徒", "司空", "百里", "呼延", "澹台",
    "公冶", "太史", "申屠", "闻人", "聞人", "钟离", "鍾離", "万俟", "萬俟", "拓跋", "赫连", "赫連",
}

# Фамильные чтения многозначных иероглифов (в начале имени).
SURNAME_READINGS = {
    "曾": "zeng", "单": "shan", "單": "shan", "解": "xie", "仇": "qiu", "区": "ou", "區": "ou",
    "朴": "piao", "查": "zha", "乐": "yue", "樂": "yue", "盖": "ge", "蓋": "ge", "缪": "miao",
    "繆": "miao", "翟": "zhai", "折": "she", "沈": "shen", "瀋": "shen", "重": "chong",
    "种": "chong", "種": "chong", "覃": "qin", "召": "shao", "尉": "wei", "员": "yun", "員": "yun",
    "黑": "he", "秘": "bi", "能": "nai", "华": "hua", "華": "hua", "任": "ren", "传": "chuan",
}

_INITIALS = {
    "b": "б", "p": "п", "m": "м", "f": "ф", "d": "д", "t": "т", "n": "н", "l": "л",
    "g": "г", "k": "к", "h": "х", "j": "цз", "q": "ц", "x": "с",
    "zh": "чж", "ch": "ч", "sh": "ш", "r": "ж", "z": "цз", "c": "ц", "s": "с",
}

# Финали после согласной (общий случай).
_FINALS = {
    "a": "а", "o": "о", "e": "э", "ai": "ай", "ei": "эй", "ao": "ао", "ou": "оу",
    "an": "ань", "en": "энь", "ang": "ан", "eng": "эн", "ong": "ун", "er": "эр",
    "i": "и", "ia": "я", "ie": "е", "iao": "яо", "iu": "ю", "ian": "янь", "in": "инь",
    "iang": "ян", "ing": "ин", "iong": "юн",
    "u": "у", "ua": "уа", "uo": "о", "uai": "уай", "ui": "уй", "uan": "уань", "un": "унь",
    "uang": "уан", "ueng": "ун",
    "v": "юй", "ve": "юэ", "van": "юань", "vn": "юнь", "ue": "юэ",
}

# Слоги без согласной/с y-, w-.
_STANDALONE = {
    "a": "а", "ai": "ай", "an": "ань", "ang": "ан", "ao": "ао", "e": "э", "ei": "эй",
    "en": "энь", "eng": "эн", "er": "эр", "o": "о", "ou": "оу",
    "ya": "я", "yan": "янь", "yang": "ян", "yao": "яо", "ye": "е", "yi": "и", "yin": "инь",
    "ying": "ин", "yo": "ё", "yong": "юн", "you": "ю", "yu": "юй", "yue": "юэ", "yuan": "юань",
    "yun": "юнь", "wa": "ва", "wai": "вай", "wan": "вань", "wang": "ван", "wei": "вэй",
    "wen": "вэнь", "weng": "вэн", "wo": "во", "wu": "у",
    "m": "м", "n": "нь", "ng": "н", "hm": "хм", "hng": "хн",
}

# Особые слоги.
_SPECIAL = {
    "zhi": "чжи", "chi": "чи", "shi": "ши", "ri": "жи", "zi": "цзы", "ci": "цы", "si": "сы",
    "ju": "цзюй", "qu": "цюй", "xu": "сюй", "jue": "цзюэ", "que": "цюэ", "xue": "сюэ",
    "juan": "цзюань", "quan": "цюань", "xuan": "сюань", "jun": "цзюнь", "qun": "цюнь", "xun": "сюнь",
    "lv": "люй", "nv": "нюй", "lve": "люэ", "nve": "нюэ", "lue": "люэ", "nue": "нюэ",
    "jiong": "цзюн", "qiong": "цюн", "xiong": "сюн",
}

_VOWELS = set("аеёиоуыэюя")

# Устоявшиеся русские названия (экзонимы) — важнее Палладия.
EXONYMS = {
    "北京": "Пекин", "上海": "Шанхай", "香港": "Гонконг", "南京": "Нанкин", "台湾": "Тайвань",
    "臺灣": "Тайвань", "澳门": "Макао", "澳門": "Макао", "西藏": "Тибет", "中国": "Китай",
    "中國": "Китай", "黄河": "Хуанхэ", "黃河": "Хуанхэ", "长江": "Янцзы", "長江": "Янцзы",
    "东京": "Токио", "東京": "Токио", "韩国": "Корея", "韓國": "Корея", "日本": "Япония",
    "美国": "США", "美國": "США", "俄罗斯": "Россия", "俄羅斯": "Россия", "欧洲": "Европа",
    "歐洲": "Европа", "哈尔滨": "Харбин", "哈爾濱": "Харбин", "喜马拉雅": "Гималаи",
}


def syllable_to_cyr(syl: str) -> str:
    s = syl.lower().replace("ü", "v").replace("u:", "v")
    s = re.sub(r"[1-5]$", "", s)
    if s in _SPECIAL:
        return _SPECIAL[s]
    if s in _STANDALONE:
        return _STANDALONE[s]
    for ini in ("zh", "ch", "sh"):
        if s.startswith(ini):
            fin = s[2:]
            if fin in _FINALS:
                return _INITIALS[ini] + _FINALS[fin]
            return s
    ini, fin = s[:1], s[1:]
    if ini in _INITIALS and fin in _FINALS:
        cyr_fin = _FINALS[fin]
        # Смягчённые финали после л/н/д/т/м/б/п: lia → ля, nie → не и т.п. — уже корректно,
        # т.к. «я/е/ю» в кириллице и так смягчают согласную.
        return _INITIALS[ini] + cyr_fin
    return s  # неизвестный слог — оставляем латиницей, агент увидит


def join_syllables(cyr: list[str], ng_flags: list[bool]) -> str:
    """Склейка слогов с разделительным ъ: слог на -ng (кир. «н») перед гласной → «нъ»."""
    out = ""
    for i, part in enumerate(cyr):
        if i > 0 and out and part and part[0] in _VOWELS:
            if ng_flags[i - 1] and out.endswith("н"):
                out += "ъ"
        out += part
    return out


def _pinyin_list(hanzi: str, surname_first: bool) -> list[str]:
    syls = lazy_pinyin(hanzi, style=Style.NORMAL, errors="ignore", v_to_u=False)
    if surname_first and hanzi and hanzi[0] in SURNAME_READINGS and len(syls) == len(hanzi):
        syls[0] = SURNAME_READINGS[hanzi[0]]
    return syls


def transcribe_word(hanzi: str, surname_first: bool = False) -> tuple[str, list[str]]:
    syls = _pinyin_list(hanzi, surname_first)
    cyr = [syllable_to_cyr(s) for s in syls]
    ng = [s.lower().endswith("ng") for s in syls]
    word = join_syllables(cyr, ng)
    return (word[:1].upper() + word[1:]) if word else word, syls


EXONYMS_LATIN = {"香港": "Hong Kong", "澳门": "Macau", "澳門": "Macau", "西藏": "Tibet", "台湾": "Taiwan",
                 "臺灣": "Taiwan", "中国": "China", "中國": "China", "长江": "Yangtze", "長江": "Yangtze",
                 "黄河": "Yellow River", "黃河": "Yellow River", "内蒙古": "Inner Mongolia", "內蒙古": "Inner Mongolia"}


def pinyin_word(hanzi: str, surname_first: bool = False) -> tuple[str, list[str]]:
    """One word in pinyin without tones; apostrophe before a/o/e-initial syllables (Xi'an, Tian'anmen)."""
    syls = lazy_pinyin(hanzi, style=Style.NORMAL, errors="ignore", v_to_u=True)
    if surname_first and hanzi and hanzi[0] in SURNAME_READINGS and len(syls) == len(hanzi):
        syls[0] = SURNAME_READINGS[hanzi[0]]
    out = ""
    for i, syl in enumerate(syls):
        if i and syl[:1] in "aoe":
            out += "'"
        out += syl
    return (out[:1].upper() + out[1:]) if out else out, syls


def transcribe(text: str, kind: str = "auto", system: str = "palladius") -> dict:
    """Транскрипция имени/топонима.

    kind: person — «Фамилия Имя» (с учётом двусложных фамилий);
          place/other — одним словом; auto — person, если 2–4 иероглифа.
    """
    hanzi = "".join(ch for ch in text if "㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿")
    if not hanzi:
        return {"source": text, "error": "нет иероглифов"}
    if system == "pinyin":
        return _transcribe_pinyin(text, hanzi, kind)
    if hanzi in EXONYMS and kind != "person":
        return {"source": text, "pinyin": " ".join(_pinyin_list(hanzi, False)),
                "target": EXONYMS[hanzi], "kind": "place", "note": "устоявшееся русское название"}
    if kind == "auto":
        word = transcribe(text, "other")
        if 2 <= len(hanzi) <= 4:
            person = transcribe(text, "person")
            return {"source": text, "pinyin": word["pinyin"], "as_person": person["target"],
                    "as_word": word["target"], "kind": "auto"}
        return word
    if kind == "person" and len(hanzi) >= 2:
        sur_len = 2 if hanzi[:2] in COMPOUND_SURNAMES and len(hanzi) >= 3 else 1
        sur, sur_py = transcribe_word(hanzi[:sur_len], surname_first=True)
        given, given_py = transcribe_word(hanzi[sur_len:])
        return {"source": text, "pinyin": " ".join(sur_py + given_py), "target": f"{sur} {given}",
                "surname": sur, "given": given, "kind": "person"}
    word, py = transcribe_word(hanzi)
    return {"source": text, "pinyin": " ".join(py), "target": word, "kind": kind}


def _transcribe_pinyin(text: str, hanzi: str, kind: str) -> dict:
    if hanzi in EXONYMS_LATIN and kind != "person":
        return {"source": text, "pinyin": " ".join(_pinyin_list(hanzi, False)), "target": EXONYMS_LATIN[hanzi],
                "kind": "place", "note": "established English name"}
    if kind == "auto":
        word = _transcribe_pinyin(text, hanzi, "other")
        if 2 <= len(hanzi) <= 4:
            person = _transcribe_pinyin(text, hanzi, "person")
            return {"source": text, "pinyin": word["pinyin"], "as_person": person["target"],
                    "as_word": word["target"], "kind": "auto"}
        return word
    if kind == "person" and len(hanzi) >= 2:
        sur_len = 2 if hanzi[:2] in COMPOUND_SURNAMES and len(hanzi) >= 3 else 1
        sur, sur_py = pinyin_word(hanzi[:sur_len], surname_first=True)
        given, given_py = pinyin_word(hanzi[sur_len:])
        return {"source": text, "pinyin": " ".join(sur_py + given_py), "target": f"{sur} {given}",
                "surname": sur, "given": given, "kind": "person"}
    word, py = pinyin_word(hanzi)
    return {"source": text, "pinyin": " ".join(py), "target": word, "kind": kind}


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:] or ["黎小白", "楊心雅", "青州", "欧阳锋", "王浩", "平安", "天安门", "嫦娥", "延安", "西安"]:
        print(arg, transcribe(arg))
