"""Prompts for a language pair.

Russian target → Russian prompts (prompts.py): tuned and measured on Chinese → Russian, kept word for word for
that pair; other sources get the same prompts without the Chinese-only rules. Every other target → English
prompts (prompts_en.py) with source/target-specific rules filled in here.
"""
from __future__ import annotations

from . import prompts as RU
from . import prompts_en as EN
from .lang import Lang, Pair

RU_MESSAGES = {
    "bible_header": "Библия этой книги:\n",
    "house_rules": "ОБЯЗАТЕЛЬНЫЕ правила заказчика (важнее библии; при противоречии следуй им):\n",
    "digits_zh": ("Числа — только цифрами, без слов «тысяч/миллионов»: 五百萬 → 5000000, 二十塊 → 20 юаней, "
                  "一個億 → 100000000, 三天 → 3 дня."),
    "digits": "Числа — только цифрами, без слов «тысяч/миллионов».",
    "glossary_header": "Глоссарий для этого фрагмента (обязателен):\n",
    "prev": "Предыдущие абзацы (уже переведены — только для связности, НЕ переводить):\n",
    "translate": "Переведи абзацы главы {n}:\n",
    "ahead": "Дальше в тексте (только для понимания контекста, НЕ переводить):\n",
    "special": "Особое указание: ",
    "fix_system": ("\n\nСейчас ты не переводишь заново, а ИСПРАВЛЯЕШЬ готовый перевод абзаца по указанию. "
                   "Меняй только то, что требует указание; остальное оставь как есть. "
                   "Ответ — только исправленный абзац, одной строкой."),
    "fix_user": "Оригинал:\n{src}\n\nТекущий перевод:\n{tr}\n\nУказание: {instruction}",
    "fix_default": ("Сверь перевод с оригиналом и исправь ошибки: смысл, пропуски, имена и термины по глоссарию, "
                    "род персонажей. Если ошибок нет — верни перевод как есть."),
    "explain_system": ("Ты помогаешь читателю переведённой книги. Читатель выделил фрагмент и спрашивает о нём. "
                       "Отвечай по-русски, коротко и по делу (3–8 предложений): что значат слова, имена, термины, "
                       "идиомы и культурные отсылки, как это звучит в оригинале и почему переведено так. "
                       "Опирайся на глоссарий и библию книги; не придумывай сюжет, которого нет во фрагменте "
                       "и в библии, и не раскрывай того, что будет дальше."),
    "explain_user": ("Глоссарий:\n{gl}\n\nБиблия книги:\n{bible}\n\nАбзац в оригинале:\n{src}\n\n"
                     "Абзац в переводе:\n{tr}\n\nФрагмент, о котором спрашивают: «{frag}»\n\nВопрос читателя: {q}"),
    "explain_default_q": "Объясни непонятные слова, имена, термины и выражения в этом фрагменте.",
    "anonymous_author": "Автор неизвестен",
    "meta_system": ("Ты переводишь с {src} языка на русский строку для обложки книги и карточки в библиотеке: {what} "
                    "Это не текст главы и не заголовок главы: никаких «Глава N». "
                    "Ответ — только перевод, одной строкой, в формате «[1] перевод»."),
    "meta_title": "название книги. Переведи его так, как его напечатали бы на обложке русского издания.",
    "meta_author": ("имя автора — имя или псевдоним человека. Передай его как имя, по принятой в русском "
                    "транскрипции{note}, а не переводи по смыслу. Цифры и числительные в имени — часть имени: "
                    "передай их чтение как имя (японское 五十六 читается Исороку), а не числом."),
    "meta_anonymous": "пометка «автор неизвестен» вместо имени автора. Переведи её смысл.",
    "hint_full": "Переведи абзац ПОЛНОСТЬЮ, ничего не сокращая и не пропуская.",
    "hint_script_zh": ("Убери все иероглифы и китайские скобки/кавычки: всё должно быть по-русски, "
                       "диалоги — через тире, названия — в «ёлочках»."),
    "hint_script": "Убери все остатки текста оригинала: всё должно быть по-русски.",
    "hint_gender": ("Ошибка рода: {msg}. Исправь род глаголов, прилагательных и местоимений, "
                    "относящихся к этому персонажу, по полу из глоссария; остальное не меняй."),
    "hint_digits": ("Запиши все числа ЦИФРАМИ (пять юаней → 5 юаней, двадцать два года → 22 года, "
                    "две недели → 2 недели), согласуя слова после числа; остальное не меняй."),
    "hint_unknown_name": ("В переводе {name} — такого персонажа нет в оригинале. "
                          "Назови персонажа так, как в оригинале этого абзаца (по глоссарию), или местоимением."),
    "hint_name_variant": ("Имя {source} пишется строго «{target}» — исправь написание "
                          "(склоняя по падежу; в роли подлежащего — именительный «{target}»)."),
    "hint_name_fix": "Исправь написание имени: {msg} (склоняй по падежу).",
    "rv_bible": "Библия книги:\n",
    "rv_glossary": "Глоссарий:\n",
    "rv_extra": "Дополнительное указание: ",
    "rv_user": "Глава {n}. Сверь и верни правки:\n{body}",
    "rv_orig": "ОРИГИНАЛ",
    "rv_tr": "ПЕРЕВОД",
    "bw_user_palladius": "Персонажи из глоссария (имена — уже по Палладию):\n{cast}\n\nНачало книги:\n{sample}",
    "bw_user": "Персонажи из глоссария:\n{cast}\n\nНачало книги:\n{sample}",
    "male": "муж.", "female": "жен.", "gender_unknown": "пол ?",
    "gender_disputed": " [ПОЛ СПОРНЫЙ — проверь по тексту]",
    "rb_line": "{source} → {target} (пол в глоссарии: {gender}); контекст: {ctx}",
    "rb_heading": "## Новые персонажи (гл. {rng}, авто)",
    "picked_heading": "## Персонажи этого фрагмента (из дополнений по ходу книги)",
    "ge_user": "Глава {n}. Найди и исправь ошибки рода:\n{body}",
    "ge_split": "Ответ — JSON",
    "ge_single_tail": "Ответ — только исправленный абзац целиком, без пояснений.",
    "ge_single_user": ("Абзац:\n{tr}\n\nОшибка: {hint}. Исправь род этого "
                       "персонажа во всём абзаце (глаголы, прилагательные, местоимения)."),
    "ex_user": "Уже в глоссарии: {known}\n\nГлава {n}:\n{text}",
    "ex_sep": "、",
    "rs_user": "Библия книги:\n{bible}\n\nКандидаты:\n{lines}",
    "rs_line": "{i}. {source} → «{target}» [{kind}] {note}\n   контекст: {ctx}",
    "cast_m": "мужчина", "cast_f": "женщина",
    "gl_male": "муж.", "gl_female": "жен.", "gl_disputed_zh": "пол определяй по оригиналу (他/她)",
    "gl_disputed": "пол определяй по оригиналу",
}

# Russian prompts for sources other than Chinese: the same rules without Palladius, 「」 and 第N章.
RU_TRANSLATOR_GENERIC = """Ты — профессиональный литературный переводчик художественных книг с {src} языка на русский.
Цель: перевод, который читается как книга, изначально написанная по-русски, и при этом точен.

Правила:
1. Полнота. Переводи каждый абзац целиком: ничего не пропускай, не сокращай и не добавляй от себя.
2. Только русский. В переводе не должно остаться слов и символов оригинала, кроме имён и брендов,
   которые в русских текстах принято писать как в оригинале.
3. Имена и термины — строго по глоссарию. Новые имена — по принятой в русском языке транскрипции{names_note}.
   Имена склоняй по-русски.
4. Пол — по местоимениям и указаниям оригинала. Следи за родом глаголов и прилагательных.
5. Диалоги — по русским правилам, через тире, НЕ в кавычках:
   — Пошли, — сказал он.
   Кавычки «ёлочки» — только для мыслей, цитат, названий и надписей.
6. Заголовок главы (первый абзац) → «Глава N. Название».
7. Естественность: избегай кальки, канцелярита и дословных оборотов оригинала; сохраняй ритм автора
   (короткие рубленые фразы оставляй короткими), юмор, сленг и эмоции.
8. Не объясняй и не комментируй — только перевод."""

RU_EXTRACTOR_GENERIC = """Ты извлекаешь из главы романа (язык оригинала — {src}) имена собственные и устойчивые термины для глоссария переводчика на русский.
Извлекай: имена персонажей (полные и сокращённые/ласковые формы), прозвища, топонимы, организации (компании, университеты, кланы),
титулы и обращения, специальные термины мира (система, навыки, ранги, предметы).
НЕ извлекай обычные слова и известные уже термины из списка «уже в глоссарии».
Для каждого:
- source — точная запись как в тексте;
- kind — person | place | org | term | title | item | skill | other;
- name_form (только для person) — full (фамилия+имя), given (только имя), nickname (прозвище/ласковое);
- gender — m | f | n | ? (для персонажей — по местоимениям и контексту);
- ru — предлагаемый перевод на русский (для имён — принятая русская транскрипция; для прозвищ по смыслу — перевод
  или транскрипция, как естественнее; для org/term — перевод по смыслу);
- note — 3–10 слов: кто это/что это.
Ответ — JSON: {"terms": [{"source": "...", "kind": "...", "name_form": "...", "gender": "...", "ru": "...", "note": "..."}]}"""

CHAPTER_EXAMPLES = {
    "en": "Chapter 12: The Title", "ru": "Глава 12. Название", "uk": "Розділ 12. Назва", "es": "Capítulo 12: El título",
    "pt": "Capítulo 12: O título", "fr": "Chapitre 12 : Le titre", "de": "Kapitel 12: Der Titel",
    "it": "Capitolo 12: Il titolo", "pl": "Rozdział 12: Tytuł", "cs": "Kapitola 12: Název", "tr": "Bölüm 12: Başlık",
    "vi": "Chương 12: Tiêu đề", "id": "Bab 12: Judul", "nl": "Hoofdstuk 12: De titel", "ja": "第12章 タイトル",
    "zh": "第12章 标题", "ko": "제12장 제목", "ar": "الفصل 12: العنوان",
}

_ROMANIZED = {"latin"}


def _names_rule(pair: Pair) -> str:
    s, t = pair.src, pair.tgt
    if s.code == "zh":
        if pair.name_system == "pinyin":
            rule = ("New Chinese names: Hanyu Pinyin without tone marks, family name first, the given name as one "
                    "word (Li Xiaobai, Yang Xinya; places: Qingzhou, Xi'an).")
            if t.code == "en":
                rule += (" Cultivation and xianxia terms: the established English web-novel terms (qi, dantian, "
                         "sect, Foundation Establishment, Golden Core, senior brother) unless the glossary says "
                         "otherwise.")
            return rule
        return f"New Chinese names: the standard {t.name} rendering of Chinese names."
    if s.code == "ja":
        base = ("New Japanese names: Hepburn romanization, name order as in the original."
                if t.script in _ROMANIZED else f"New Japanese names: the standard {t.name} transcription.")
        return base + (f" Honorifics (-san, -sama, senpai): render naturally in {t.name} unless the book bible "
                       "says to keep them.")
    if s.code == "ko":
        return ("New Korean names: Revised Romanization (Kim Ji-woo)." if t.script in _ROMANIZED
                else f"New Korean names: the standard {t.name} transcription.")
    if s.script != t.script:
        return f"New names: the standard {t.name} transliteration of {s.name} names."
    return f"New names: keep the spelling used in {t.name} texts (usually as in the original)."


def _names_hint(pair: Pair) -> str:
    if pair.name_system == "pinyin":
        return "Chinese names — Hanyu Pinyin without tones: Li Xiaobai, Qingzhou"
    if pair.src.code == "ja" and pair.tgt.script in _ROMANIZED:
        return "Japanese names — Hepburn romanization"
    if pair.src.code == "ko" and pair.tgt.script in _ROMANIZED:
        return "Korean names — Revised Romanization"
    return f"names — the standard {pair.tgt.name} spelling"


class Prompts:
    def __init__(self, pair: Pair):
        self.pair = pair
        self.ru = pair.prompt_lang == "ru"
        self.zh = pair.src.code == "zh"
        self.palladius = pair.name_system == "palladius"

    # ---------- short phrases ----------
    def m(self, key: str, **kw) -> str:
        table = RU_MESSAGES if self.ru else EN.MESSAGES
        if self.zh and f"{key}_zh" in table:
            key = f"{key}_zh"
        if key == "bw_user" and self.ru and self.palladius:
            key = "bw_user_palladius"
        s = table[key]
        return s.format(**kw) if kw else s

    def _ru_src(self) -> str:
        return self.pair.src.ru_gen or self.pair.src.name

    def _en(self, tpl: str, **kw) -> str:
        return tpl.format(src=self.pair.src.name, tgt=self.pair.tgt.name, **kw)

    def _ru_names_note(self) -> str:
        return {"zh": " (китайские — по Палладию)", "ja": " (японские — по Поливанову)",
                "ko": " (корейские — по Концевичу)"}.get(self.pair.src.code, "")

    # ---------- main prompts ----------
    def translator(self) -> str:
        if self.ru:
            if self.zh:
                return RU.TRANSLATOR_BASE.format(src=self._ru_src())
            return RU_TRANSLATOR_GENERIC.format(src=self._ru_src(), names_note=self._ru_names_note())
        dialogue = " Convert 「」『』 quotes into the dialogue punctuation of the target language." \
            if self.pair.src.cjk and not self.pair.tgt.cjk else ""
        example = CHAPTER_EXAMPLES.get(self.pair.tgt.code, f"Chapter 12: … (in {self.pair.tgt.name})")
        return self._en(EN.TRANSLATOR, names_rule=_names_rule(self.pair), dialogue_rule=dialogue,
                        chapter_example=example)

    def meta(self, kind: str) -> str:
        """Название или автор для обложки (kind: title | author | anonymous) — не абзац главы: без правил о
        заголовке главы и о числах цифрами."""
        if self.ru:
            return self.m("meta_system", src=self._ru_src(), what=self.m(f"meta_{kind}", note=self._ru_names_note()))
        tgt = self.pair.tgt.name
        return self.m("meta_system", src=self.pair.src.name, tgt=tgt,
                      what=self.m(f"meta_{kind}", tgt=tgt, names=_names_hint(self.pair)))

    @property
    def format_rules(self) -> str:
        return RU.FORMAT_RULES if self.ru else EN.FORMAT_RULES

    def reviewer(self, extra: str) -> str:
        if self.ru:
            return RU.REVIEWER_SYSTEM.format(src=self._ru_src(), extra=extra)
        return self._en(EN.REVIEWER, extra=extra)

    def extractor(self) -> str:
        if self.ru:
            return RU.EXTRACTOR_SYSTEM if self.zh else RU_EXTRACTOR_GENERIC.replace("{src}", self.pair.src.native)
        return self._en(EN.EXTRACTOR, names_hint=_names_hint(self.pair))

    def bible_template(self, title: str) -> str:
        return (RU.BIBLE_TEMPLATE if self.ru else EN.BIBLE_TEMPLATE).format(title=title)

    def bible_writer(self, style: str, template: str) -> str:
        if self.ru:
            text = RU.BIBLE_WRITER
            if not self.zh:
                text = (text.replace("китайского романа", f"романа с {self._ru_src()} языка")
                        .replace("(строго по 他/她 и прямым указаниям текста", "(строго по местоимениям и прямым "
                                 "указаниям текста"))
            return text.format(style=style, template=template)
        return self._en(EN.BIBLE_WRITER, style=style, template=template)

    def bible_refresher(self) -> str:
        if self.ru:
            text = RU.BIBLE_REFRESHER
            return text if self.zh else text.replace("(иероглифы)", "(как в оригинале)").replace(
                "(как в тексте: 他/她)", "(как в тексте)")
        return EN.BIBLE_REFRESHER

    def gender_editor(self, cast: str) -> str:
        if self.ru:
            text = RU.GENDER_EDITOR if self.zh else RU.GENDER_EDITOR.replace("с китайского", f"с {self._ru_src()}")
            return text.format(cast=cast)
        return self._en(EN.GENDER_EDITOR, cast=cast)

    def glossary_resolver(self) -> str:
        if self.ru:
            text = RU.GLOSSARY_RESOLVER
            if not self.zh:
                text = (text.replace("китайского романа", f"романа с {self._ru_src()} языка")
                        .replace("Имена людей транскрибированы по Палладию — их написание не меняй, только "
                                 "kind/gender при ошибке.\n", "")
                        .replace(" (余經理 → «управляющий Юй»)", ""))
            return text
        if self.pair.name_system:
            note = ("Chinese personal names were transcribed by a deterministic system — do not change their "
                    "spelling, only kind/gender if wrong.")
        else:
            note = "Keep personal names consistent with the conventional spelling in the target language."
        return self._en(EN.GLOSSARY_RESOLVER, names_note=note)

    def digits_rule(self) -> str:
        if self.ru:
            return self.m("digits")
        return EN.MESSAGES["digits"].format(examples=EN.MESSAGES["digits_examples_zh"] if self.zh else "")


def agent_system(ui_lang: str) -> str:
    """The agent talks to the user: Russian prompt for Russian-speaking users, English otherwise."""
    return RU.AGENT_SYSTEM if ui_lang == "ru" else EN.AGENT_SYSTEM


def lang_label(lg: Lang) -> str:
    return f"{lg.name} ({lg.code})"


__all__ = ["Prompts", "agent_system", "RU_MESSAGES", "CHAPTER_EXAMPLES"]
