"""Детерминированный QA перевода (без LLM).

hard — точно ошибка, движок чинит сам; soft — подозрение, решает агент/рецензент.
"""
from __future__ import annotations

import re

from .i18n import _
from .glossary import NAME_KINDS, Glossary, word_stem

_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# Правило numbers=digits (ставит Engine из конфига): числа словами у единиц измерения — hard.
NUMBERS_DIGITS = True
_HAN_ONLY = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# 【】 не считаем ошибкой: в русских переводах LitRPG их часто оставляют (заголовки, сообщения Системы) —
# это решение библии книги, а не дефект.
_CJK_PUNCT = re.compile(r"[「」『』《》〈〉]")
_CYR_WORD = re.compile(r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]+)?")
_LAT_WORD = re.compile(r"\b[A-Za-z]{3,}\b")
_DIGITS = re.compile(r"\d+(?:[.,]\d+)?")


def _palladius_syllables() -> set[str]:
    from .palladius import _FINALS, _INITIALS, _SPECIAL, _STANDALONE
    out = set(_SPECIAL.values()) | set(_STANDALONE.values())
    for i in _INITIALS.values():
        for f in _FINALS.values():
            out.add(i + f)
    return {s for s in out if s}


def _pinyin_syllables() -> set[str]:
    from .palladius import _FINALS, _INITIALS, _SPECIAL, _STANDALONE
    out = set(_SPECIAL) | set(_STANDALONE) | {i + f for i in _INITIALS for f in _FINALS}
    return {x.replace("v", "ü") for x in out} | {x.replace("v", "u") for x in out} | out


_PSYL = _palladius_syllables()
_PINYIN = _pinyin_syllables()
_PINYIN_MAX = max(len(x) for x in _PINYIN)
_PSYL_MAX = max(len(s) for s in _PSYL)
SURNAMES_RU = set("""Ли Ван Чжан Лю Чэнь Ян Чжао Хуан Чжоу У Сюй Сунь Ху Чжу Гао Линь Хэ Го Ма Ло Лян Сун Чжэн Се Хань  # i18n: skip
Тан Фэн Юй Дун Сяо Чэн Цао Юань Дэн Фу Шэнь Цзэн Пэн Люй Су Лу Цзян Цай Цзя Дин Вэй Сюэ Е Янь Пань Ду Дай Ся Чжун
Тянь Жэнь Фань Фан Ши Яо Тань Ляо Цзоу Сюн Цзинь Хао Кун Бай Цуй Кан Мао Цю Цинь Гу Хоу Шао Мэн Лун Вань Дуань
Цянь Инь И Чан Цяо Лай Гун Вэнь Мо Нин Юнь Мэй Шэн Чу Ци Лэй Оуян Шангуань Мужун Симэнь Наньгун Дунфан""".split())


def is_palladius_word(w: str) -> bool:
    """Слово целиком раскладывается на слоги Палладия (с возможным ъ между слогами)."""
    s = w.lower().replace("ъ", "")
    if len(s) < 1:
        return False
    ok = [False] * (len(s) + 1)
    ok[0] = True
    for i in range(len(s)):
        if not ok[i]:
            continue
        for j in range(i + 1, min(len(s), i + _PSYL_MAX) + 1):
            if s[i:j] in _PSYL:
                ok[j] = True
    return ok[len(s)]


_FULLNAME = re.compile(r"\b([А-ЯЁ][а-яё]{0,7})\s([А-ЯЁ][а-яёъ]{1,12})\b")


def plausible_variant(t: str, w: str) -> bool:
    """t похоже на искажённое имя w (а не на обычное слово, другую фамилию или законное сокращение)?

    Ложные срабатывания на романе в 100 глав: «Лицо» ~ «Линь», «Чжао» ~ «Чжоу» (две фамилии), «Фэй»/«Фэя»
    ~ «Фэйян» (сокращение), «Банк»/«Союз» в названиях организаций. Настоящие ошибки остаются: «Синя»/«Сине» ~
    «Синья», «Сябай» ~ «Сяобай», «Имунь» ~ «Имэн». Словарь pymorphy3 тут не помогает: «синя» — краткая форма
    прилагательного «синий», и фильтр «известное слово» прятал бы настоящие ошибки.
    """
    # короткие имена — только 1 буква разницы («Лицо» ~ «Линь» — 2, не вариант; «Синя» ~ «Синья» — 1)
    if len(w) < 4 or len(t) < 3 or t[:2] != w[:2] or _levenshtein(t, w) > (1 if len(w) <= 4 else 2):
        return False
    if t.lower().startswith(word_stem(w).lower()):
        return False  # склонение
    if not is_palladius_word(w):
        return False  # слово названия организации/прозвища по-русски («Банк», «Союз»)
    if t in SURNAMES_RU and w in SURNAMES_RU:
        return False  # две разные фамилии
    if w.startswith(t) or (w.startswith(t[:-1]) and len(t) <= len(w) - 2):
        return False  # сокращение имени: Фэй, Фэя ← Фэйян
    return True


def is_pinyin_word(w: str) -> bool:
    """Слово целиком раскладывается на слоги пиньиня (Xiaobai, Xi'an, Lü)."""
    s = w.lower().replace("'", "").replace("’", "")
    if not s:
        return False
    ok = [False] * (len(s) + 1)
    ok[0] = True
    for i in range(len(s)):
        if not ok[i]:
            continue
        for j in range(i + 1, min(len(s), i + _PINYIN_MAX) + 1):
            if s[i:j] in _PINYIN:
                ok[j] = True
    return ok[len(s)]


def plausible_variant_pinyin(t: str, w: str) -> bool:
    """t похоже на искажённое пиньинь-имя w: Xinyi ~ Xinya, Xiaobei ~ Xiaobai (не сокращение и не склонение)."""
    if len(w) < 4 or len(t) < 3 or t[:2].lower() != w[:2].lower():
        return False
    if _levenshtein(t.lower(), w.lower()) > (1 if len(w) <= 5 else 2):
        return False
    tl, wl = t.lower(), w.lower()
    if tl.startswith(wl) or wl.startswith(tl):
        return False  # окончание (Xiaobais) или сокращение
    return is_pinyin_word(w) and is_pinyin_word(t)


def _plain_stem(w: str) -> str:
    """Основа имени для языков без падежей кириллицы: только притяжательное 's (Li Xiaobai's)."""
    return re.sub(r"['’]s$", "", w.strip())


def find_chinese_names(tr: str) -> set[str]:
    """«Фамилия Имя» в кириллице, похожие на китайские (фамилия из списка, имя из слогов Палладия)."""
    out = set()
    for m in _FULLNAME.finditer(tr):
        sur, given = m.group(1), m.group(2)
        if sur in SURNAMES_RU:
            for cut in (0, 1, 2):  # склонение: Сяобая, Синьей
                g = given[:-cut] if cut else given
                if len(g) >= 1 and is_palladius_word(g):
                    out.add(f"{sur} {given}")
                    break
    return out


_SP = "\x01"  # «пробел, если дальше слово»
_PUNCT_MAP = [("……", "…" + _SP), ("，", "," + _SP), ("。", "." + _SP), ("！", "!" + _SP), ("？", "?" + _SP),
              ("：", ":" + _SP), ("；", ";" + _SP), ("、", "," + _SP), ("（", " ("), ("）", ")" + _SP),
              ("～", "~"), ("　", " ")]


def normalize_punct(tr: str) -> str:
    """Детерминированная замена китайской/полноширинной пунктуации на русскую (без LLM)."""
    if not tr:
        return tr
    out = tr
    for a, b in _PUNCT_MAP:
        out = out.replace(a, b)
    out = re.sub(_SP + r"(?=[\wА-Яа-яЁё—«(])", " ", out).replace(_SP, "")  # i18n: skip
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" +([,.!?:;)…])", r"\1", out)
    out = re.sub(r"\( +", "(", out)
    out = re.sub(r"([»!?.,…])—(?=\s)", r"\1 —", out)  # «Да!»— ответил → «Да!» — ответил
    return out.strip()


_MULT = {"тыс": 10 ** 3, "тысяч": 10 ** 3, "млн": 10 ** 6, "миллион": 10 ** 6, "млрд": 10 ** 9,
         "миллиард": 10 ** 9, "трлн": 10 ** 12, "триллион": 10 ** 12}
_NUM_MULT = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(тыс\.?|тысяч[аиуеюй]*|млн\.?|миллион[а-я]*|"
                       r"млрд\.?|миллиард[а-я]*|трлн\.?|триллион[а-я]*)(?![а-яё])")
_NUM_WORDS = (r"(?:дв[аеу]х?|две|двум[ая]?|тр[иеё]х?|трём|четыр[её]х?|четыре|пят[иь]ю?|шест[иь]|сем[иь]|восьм[иь]|"  # i18n: skip
              r"восем[ьи]|девят[иь]|десят[иь]|одиннадцат[иь]|двенадцат[иь]|тринадцат[иь]|четырнадцат[иь]|"
              r"пятнадцат[иь]|шестнадцат[иь]|семнадцат[иь]|восемнадцат[иь]|девятнадцат[иь]|двадцат[иь]|"
              r"тридцат[иь]|сорок[а]?|пятьдесят|шестьдесят|семьдесят|восемьдесят|девяност[оа]|ст[оа]|двест[иь]|"
              r"триста|четыреста|пятьсот|шестьсот|семьсот|восемьсот|девятьсот|сотн[июя]|тысяч[аиуе]?|"
              r"миллион[а-я]*|миллиард[а-я]*)")
_UNITS = (r"(?:юан[а-яё]*|долл[а-яё]*|рубл[а-яё]*|минут[а-яё]*|секунд[а-яё]*|час[а-яё]*|дн[еяю][а-яё]*|сут[ок]*|"  # i18n: skip
          r"недел[а-яё]*|месяц[а-яё]*|лет|год[а-яё]*|процент[а-яё]*|метр[а-яё]*|километр[а-яё]*|килограмм[а-яё]*|"
          r"грамм[а-яё]*|этаж[а-яё]*|балл[а-яё]*|очк[а-яё]*|ракет[а-яё]*|штук[а-яё]*|человек[а-яё]*|"
          r"квадратн[а-яё]*|кв\.|вилл[а-яё]*)")
_WORD_NUM = re.compile(r"(?<![а-яё])((?:" + _NUM_WORDS + r"[\s-]+){1,4})" + _UNITS, re.I)  # i18n: skip


def normalize_numbers(tr: str) -> str:
    """«500 миллионов юаней» → «500000000 юаней», «1,5 тыс.» → «1500» (правило numbers=digits)."""
    def rep(m):
        num = float(m.group(1).replace(",", "."))
        key = next(k for k in _MULT if m.group(2).lower().startswith(k))
        return str(int(round(num * _MULT[key])))
    return _NUM_MULT.sub(rep, tr) if tr else tr


_MULT_ONLY = re.compile(r"^(?:(?:сотн|тысяч|миллион|миллиард)[а-яё]*[\s-]+)+", re.I)
_VAGUE = re.compile(r"(?:десятк|сотн|несколько|много|тысячи|пар[уы])[а-яё]*\s+$", re.I)


def number_word_issues(tr: str) -> list[dict]:
    found = []
    for m in _WORD_NUM.finditer(tr or ""):
        if _MULT_ONLY.match(m.group(1)):
            continue  # «миллионов юаней», «тысячи человек» — без числа: неточное количество
        if _VAGUE.search(tr[max(0, m.start() - 15):m.start()]):
            continue  # «десятки тысяч юаней», «несколько сотен»
        found.append(m.group(0).strip())
    if not found:
        return []
    return [{"type": "number_words", "level": "hard",
             "msg": _("числа словами вместо цифр: {0}").format(', '.join(found[:4]))}]


_MORPH = None
_PREPS = {"у", "с", "со", "к", "ко", "от", "для", "о", "об", "обо", "на", "за", "перед", "при", "без", "про",  # i18n: skip
          "через", "над", "под", "из", "до", "по", "между", "около", "возле", "мимо", "после", "среди", "вокруг"}  # i18n: skip
_TOK = re.compile(r"[А-ЯЁа-яё]+(?:-[А-ЯЁа-яё]+)?|[^\sА-ЯЁа-яё]")
_STOP = set(",.!?;:()«»\"…")


def _morph():
    global _MORPH
    if _MORPH is None:
        try:
            import pymorphy3
            _MORPH = pymorphy3.MorphAnalyzer()
        except ImportError:  # морфология необязательна: без неё проверка рода просто не работает
            _MORPH = False
    return _MORPH


def _verb_gender(word: str) -> str | None:
    """Род глагола прошедшего времени / краткого прилагательного (ед.ч.): masc | femn | None."""
    m = _morph()
    if not m:
        return None
    parses = m.parse(word.lower())[:3]
    # «тронут» = «(они) тронут» или «тронут (он)» — неоднозначно, не судим
    if any(p.tag.number == "plur" or (p.tag.POS == "VERB" and p.tag.tense in ("pres", "futr")) for p in parses):
        return None
    for p in parses:
        t = p.tag
        if ((t.POS == "VERB" and t.tense == "past") or t.POS == "ADJS") and t.number == "sing" \
                and t.gender in ("masc", "femn"):
            return str(t.gender)  # у pymorphy3 это свой тип-граммема — не сериализуется между процессами
    return None


_PRON = {"я", "ты", "он", "она", "оно", "мы", "вы", "они"}  # i18n: skip


def _clause_has_subject_before(toks: list[str], i: int) -> bool:
    """До имени в той же части предложения уже есть глагол или личное местоимение → имя, скорее, дополнение:
    «он ещё и Ли Сяовань спас», «посадил Ли Сяовань и Ся Имэн и поехал»."""
    m = _morph()
    for k in range(i - 1, max(-1, i - 8), -1):
        t = toks[k]
        if t in _STOP or t == "—":
            return False
        if t.lower() in _PRON:
            return True
        if m and re.match(r"[А-ЯЁа-яё]", t):
            if any(p.tag.POS == "VERB" for p in m.parse(t.lower())[:2]):
                return True
    return False


def _subject_slot(prev: str) -> bool:
    if not re.match(r"[А-ЯЁа-яё]", prev):
        return True  # знак препинания / тире
    if prev.lower() in _PREPS:
        return False
    m = _morph()
    pos = {p.tag.POS for p in m.parse(prev.lower())[:2]}
    return bool(pos & {"CONJ", "ADVB", "PRCL", "INTJ", "PRED"}) and not (pos & {"NOUN", "ADJF", "PRTF", "NPRO"})


def gender_votes(tr: str, names: list[str]) -> list[tuple[str, str, str]]:
    """Голоса за пол по русскому тексту (когда оригинала нет): [(имя, masc|femn, глагол)].

    Те же высокоточные шаблоны, что и в gender_issues: имя-подлежащее + глагол прош. вр./краткое прил.,
    и инверсия в диалоге «— …, — сказала Имя».
    """
    if not _morph():
        return []
    toks = _TOK.findall(tr)
    out = []
    for name in names:
        words = name.split()
        L = len(words)
        for i in range(len(toks) - L + 1):
            if toks[i:i + L] != words:
                continue
            found = None
            if (i == 0 or _subject_slot(toks[i - 1])) and not _clause_has_subject_before(toks, i):
                for k in range(i + L, min(len(toks), i + L + 4)):
                    if toks[k] in _STOP or toks[k] == "—" or (toks[k][:1].isupper() and k > i + L) \
                            or toks[k].lower() in _PRON:
                        break
                    vg = _verb_gender(toks[k])
                    if vg:
                        found = (toks[k], vg)
                        break
            if not found and i >= 2 and toks[i - 2] == "—":
                vg = _verb_gender(toks[i - 1])
                if vg:
                    found = (toks[i - 1], vg)
            if found:
                out.append((name, found[1], found[0]))
    return out


def gender_issues(tr: str, entries: list[dict]) -> list[dict]:
    """«Шэнь Син улыбнулся» при Шэнь Син = женщина; «— …, — сказала Су Янь» при Су Янь = мужчина.

    Только высокоточные шаблоны: имя в исходной форме + глагол/краткое прил. рядом, без предлога перед
    именем и без знаков препинания между ними.
    """
    persons = [(e["target"].split(), e["gender"]) for e in entries
               if e.get("kind") == "person" and e.get("gender") in ("m", "f") and e.get("target")
               and not e.get("gender_conflict")]
    if not persons or not _morph():
        return []
    toks = _TOK.findall(tr)
    issues = []
    for words, g in persons:
        want = "masc" if g == "m" else "femn"
        L = len(words)
        for i in range(len(toks) - L + 1):
            if toks[i:i + L] != words:
                continue
            found = None
            # Подлежащее — только после начала фразы, знака, союза, наречия. «у Шэнь Син был…» (предлог),
            # «в глазах Сюй Бин блеснул огонёк», «взгляд Шэнь Син стал…» (несклоняемое женское имя
            # в родительном после существительного) — не подлежащее.
            if (i == 0 or _subject_slot(toks[i - 1])) and not _clause_has_subject_before(toks, i):
                for k in range(i + L, min(len(toks), i + L + 4)):
                    if toks[k] in _STOP or toks[k] == "—" or (toks[k][:1].isupper() and k > i + L) \
                            or toks[k].lower() in _PRON:  # «Ли Сяовань первым я нашёл» — глагол при «я»
                        break
                    vg = _verb_gender(toks[k])
                    if vg:
                        found = (toks[k], vg)
                        break
            # инверсия в диалоге: «…, — улыбнулся Шэнь Син»
            if not found and i >= 2 and toks[i - 2] == "—":
                vg = _verb_gender(toks[i - 1])
                if vg:
                    found = (toks[i - 1], vg)
            if found and found[1] != want:
                name = " ".join(words)
                issues.append({"type": "gender", "level": "hard", "term": name,
                               "msg": _("«{0} … {1}»: {2} — {3}, а глагол в {4} роде").format(name, found[0], name, _("мужчина") if g == 'm' else _("женщина"), _("женском") if found[1] == 'femn' else _("мужском"))})
    return issues


_KIN_M = "弟弟|哥哥|兄长|儿子|父亲|爸爸|丈夫|老公|少年|男孩|男人|男子|先生|公子|少爷|弟|兄|叔叔|爷爷|舅舅|夫"
_KIN_F = "妹妹|姐姐|女儿|母亲|妈妈|妻子|老婆|少女|女孩|女人|女子|女士|小姐|姑娘|妹|姐|阿姨|奶奶|婆婆|妻"


def gender_evidence(name: str, paragraphs: list[str], other_names: list[str]) -> dict:
    """Улики пола персонажа из ОРИГИНАЛА — без LLM.

    - родство/роль рядом с именем: «许纯，许冰的弟弟», «许纯这个少年» → m;
    - местоимения 他/她 в предложениях, где из персонажей упомянут только он.
    → {"m": n, "f": n, "examples": [...], "verdict": "m"|"f"|None}
    """
    m = f = 0
    ex: list[str] = []
    # Обращение само несёт пол: 沈星姐姐, 林叔.
    if re.search("(?:" + _KIN_M + ")$", name) and len(name) > 1:
        return {"m": 9, "f": 0, "examples": [_("обращение «{0}»").format(name)], "verdict": "m"}
    if re.search("(?:" + _KIN_F + ")$", name) and len(name) > 1:
        return {"m": 0, "f": 9, "examples": [_("обращение «{0}»").format(name)], "verdict": "f"}
    # Только приложение: «许纯，许冰的弟弟», «许纯是许冰的弟弟», «许纯这个少年».
    # «许冰的弟弟» без запятой — это брат Сюй Бин, а не она сама.
    sep = r"(?:[，,]\s*|是|这个|这位|那个|那位)"
    n = re.escape(name)
    kin_m = re.compile(n + sep + r"(?:[^，。！？“”]{0,6}的)?(?:" + _KIN_M + ")|叫(?:做)?" + n + r"的(?:" + _KIN_M + ")"
                       + "|" + n + r"(?:先生|少爷|公子|哥哥|弟弟)")
    kin_f = re.compile(n + sep + r"(?:[^，。！？“”]{0,6}的)?(?:" + _KIN_F + ")|叫(?:做)?" + n + r"的(?:" + _KIN_F + ")"
                       + "|" + n + r"(?:小姐|女士|姐姐|妹妹)")
    pm = pf = 0
    others = [o for o in other_names if o != name and name not in o and o not in name]
    for p in paragraphs:
        if name not in p:
            continue
        for sent in re.split(r"(?<=[。！？!?])", p):
            if name not in sent:
                continue
            km, kf = len(kin_m.findall(sent)), len(kin_f.findall(sent))
            if km or kf:
                m += 3 * km
                f += 3 * kf
                if len(ex) < 4:
                    ex.append(sent.strip()[:80])
                continue
            if any(o in sent for o in others):
                continue  # в предложении есть другой персонаж — местоимение может быть о нём
            pm += sent.count("他")
            pf += sent.count("她")
    # Сильные улики (родство, обращение, титул) решают; местоимения — только при подавляющем перевесе:
    # в «женском мире» 他 почти всегда — главный герой, даже в предложениях о героине.
    verdict = None
    if m + f >= 3:
        if m >= 3 * max(1, f):
            verdict = "m"
        elif f >= 3 * max(1, m):
            verdict = "f"
    elif pm + pf >= 10:
        if pm >= 5 * max(1, pf):
            verdict = "m"
        elif pf >= 5 * max(1, pm):
            verdict = "f"
    return {"m": m, "f": f, "pron_m": pm, "pron_f": pf, "examples": ex, "verdict": verdict}


def gender_conflicts(gl: Glossary, paragraphs: list[str]) -> list[dict]:
    """Персонажи, у которых пол в глоссарии противоречит уликам оригинала."""
    persons = [e for e in gl.entries if e.get("kind") == "person" and len(e["source"]) >= 2]
    names = [e["source"] for e in persons]
    if not names:
        return []
    # Индекс «имя → абзацы» одним проходом (было: каждый персонаж × все абзацы — 4.5 с на романе в 913 глав).
    rx = re.compile("|".join(map(re.escape, sorted(set(names), key=len, reverse=True))))
    index: dict[str, list[str]] = {}
    near: dict[str, set[str]] = {}
    for p in paragraphs:
        found = set(rx.findall(p))
        for nm in found:
            index.setdefault(nm, []).append(p)
            near.setdefault(nm, set()).update(found)
    out = []
    for e in persons:
        if e.get("gender_confirmed"):
            continue  # агент сознательно подтвердил пол вопреки уликам
        own = index.get(e["source"])
        if not own:
            continue
        ev = gender_evidence(e["source"], own, sorted(near.get(e["source"], ())))
        if ev["verdict"] and e.get("gender") in ("m", "f") and ev["verdict"] != e["gender"]:
            out.append({"source": e["source"], "target": e["target"], "glossary_gender": e["gender"],
                        "evidence": f"他×{ev['m']} / 她×{ev['f']}" if not ev["examples"] else ev["examples"],
                        "suggested": ev["verdict"]})
        elif ev["verdict"] and e.get("gender") not in ("m", "f"):
            out.append({"source": e["source"], "target": e["target"], "glossary_gender": None,
                        "evidence": f"他×{ev['m']} / 她×{ev['f']}", "suggested": ev["verdict"]})
    return out


def han_count(s: str) -> int:
    return len(_HAN_ONLY.findall(s))


def _levenshtein(a: str, b: str, limit: int = 3) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _name_words(target: str) -> list[str]:
    return [w for w in re.split(r"[\s\-]+", target) if len(w) >= 2 and w[0].isupper()]


_GROUND_CACHE: dict[str, list[tuple[str, str, str]]] = {}


def source_name_readings(src: str) -> list[tuple[str, str, str]]:
    """Все «Фамилия Имя», которые можно прочитать из подряд идущих иероглифов абзаца (Палладий).

    → [(hanzi, фамилия, имя)]. Позволяет проверить, что имя в переводе действительно есть в оригинале.
    """
    if src in _GROUND_CACHE:
        return _GROUND_CACHE[src]
    from .palladius import COMPOUND_SURNAMES, SURNAME_READINGS, join_syllables, syllable_to_cyr
    from pypinyin import Style, lazy_pinyin
    out = []
    for run in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]{2,}", src):
        py = lazy_pinyin(run, style=Style.NORMAL, errors="ignore")
        if len(py) != len(run):
            continue
        for i in range(len(run) - 1):
            sur_variants = [(1, syllable_to_cyr(SURNAME_READINGS.get(run[i], py[i])).capitalize())]
            if run[i:i + 2] in COMPOUND_SURNAMES:  # 東方暮雪 → Дунфан Мусюэ
                s2 = py[i:i + 2]
                sur_variants.append((2, join_syllables([syllable_to_cyr(s) for s in s2],
                                                       [s.endswith("ng") for s in s2]).capitalize()))
            for sl, sur in sur_variants:
                for L in (1, 2, 3):
                    if i + sl + L > len(run):
                        break
                    g = py[i + sl:i + sl + L]
                    given = join_syllables([syllable_to_cyr(s) for s in g], [s.endswith("ng") for s in g]).capitalize()
                    out.append((run[i:i + sl + L], sur, given))
    if len(_GROUND_CACHE) > 20000:
        _GROUND_CACHE.clear()
    _GROUND_CACHE[src] = out
    return out


def foreign_name_issues(tr: str, entries: list[dict], gl: Glossary | None, src: str = "") -> list[dict]:
    """Имя в переводе, которого нет в оригинале абзаца: «Су Чэнь» там, где в оригинале 黎小白."""
    if gl is None:
        return []
    issues = []
    rel_stems = {word_stem(w) for e in entries for w in _name_words(e.get("target", ""))}
    readings = source_name_readings(src) if src else []
    for name in find_chinese_names(tr):
        sur, given = name.split(" ", 1)
        if any(given.startswith(st) for st in rel_stems if len(st) >= 2):
            continue  # имя из глоссария, встречающееся в этом абзаце оригинала
        grounded = next((r for r in readings if r[1] == sur and given.startswith(word_stem(r[2]))
                         and len(given) - len(r[2]) <= 2), None)
        if grounded:
            if not gl.get(grounded[0]):
                issues.append({"type": "new_name", "level": "soft", "term": grounded[0],
                               "target": f"{grounded[1]} {grounded[2]}",
                               "msg": _("имя «{0} {1}» ({2}) есть в оригинале, но не в глоссарии").format(grounded[1], grounded[2], grounded[0])})
            continue
        near = min(((r, _levenshtein(f"{r[1]} {r[2]}", name, 4)) for r in readings if r[1] == sur),
                   key=lambda x: x[1], default=None)
        if near and near[1] <= 2:
            issues.append({"type": "name_variant", "level": "hard", "term": near[0][0],
                           "msg": _("«{0}»: по Палладию из оригинала ({1}) — «{2} {3}»").format(name, near[0][0], near[0][1], near[0][2])})
            continue
        known = next((e for e in gl.entries if e.get("kind") == "person"
                      and any(given.startswith(word_stem(w)) for w in _name_words(e.get("target", ""))[1:2])
                      and _name_words(e.get("target", ""))[:1] == [sur]), None)
        if known:
            issues.append({"type": "name_not_in_source", "level": "soft", "term": known["source"],
                           "msg": _("«{0}» ({1}) упомянут в переводе, но не в оригинале абзаца").format(name, known['source'])})
        else:
            persons = sum(1 for e in gl.entries if e.get("kind") == "person")
            issues.append({"type": "unknown_name", "level": "hard" if persons >= 3 else "soft",
                           "msg": _("имя «{0}» отсутствует в глоссарии и в оригинале — галлюцинация или чужое имя?").format(name)})
    return issues


def _default_pair():
    from .lang import Pair
    return Pair.of("zh", "ru")


def _source_units(src: str, pair) -> int:
    """Объём оригинала в «знаках письма» для китайского/японского/корейского (иероглифы, кана, хангыль)."""
    rx = pair.leftover_re if pair.src.cjk else None
    return len(rx.findall(src)) if rx else han_count(src)


def check_segment(src: str, tr: str | None, entries: list[dict], all_stems: set[str] | None = None,
                  gl: Glossary | None = None, pair=None) -> list[dict]:
    """entries — термины глоссария, встречающиеся в src. pair — языковая пара (по умолчанию китайский → русский):
    специальные проверки (Палладий, морфология рода, числа словами) включаются только там, где применимы."""
    pair = pair or _default_pair()
    issues: list[dict] = []
    if not tr or not tr.strip():
        return [{"type": "missing", "level": "hard", "msg": _("нет перевода")}]
    left_re = pair.leftover_re
    left = left_re.findall(tr) if left_re else []
    if left:
        issues.append({"type": "untranslated", "level": "hard",
                       "msg": _("в переводе остались символы оригинала: {0}").format(''.join(left[:10]))})
    if pair.cjk_to_alphabetic and _CJK_PUNCT.search(tr):
        issues.append({"type": "punct", "level": "hard", "msg": _("остались китайские кавычки 「」『』《》")})
    # Длина: алфавитный перевод ~2.5–3.5 символа на иероглиф (для остальных пар — по главе, chapter_level_issues).
    hc = _source_units(src, pair) if pair.cjk_to_alphabetic else 0
    if hc >= 15:
        ratio = len(re.sub(r"\s", "", tr)) / hc
        if ratio < 1.2:
            issues.append({"type": "short", "level": "hard", "msg": _("перевод подозрительно короткий (×{0:.1f})").format(ratio)})
        elif ratio < 1.6:
            issues.append({"type": "short", "level": "soft", "msg": _("перевод короче обычного (×{0:.1f})").format(ratio)})
        elif ratio > 7.5:
            issues.append({"type": "long", "level": "soft", "msg": _("перевод подозрительно длинный (×{0:.1f})").format(ratio)})
    # Числа арабскими цифрами в оригинале должны сохраниться.
    # Числа с китайским множителем (500萬 → 5000000) пропускаем: их форма законно меняется.
    src_nums = {m.group(0).replace(",", "") for m in _DIGITS.finditer(src)
                if not pair.src.cjk or not re.match(r"\s*[十百千萬万億亿兆]", src[m.end():m.end() + 2])}
    tr_nums = {n.replace(" ", "").replace(",", "") for n in _DIGITS.findall(tr.replace(" ", ""))}
    lost = [n for n in src_nums if n not in tr_nums and len(n) >= 2]
    if lost:
        issues.append({"type": "number", "level": "soft", "msg": _("не найдены числа оригинала: {0}").format(', '.join(lost[:5]))})
    # Глоссарий.
    tr_words = pair.target_word_re.findall(tr)
    stem = word_stem if pair.tgt.script == "cyrillic" else _plain_stem
    variant = plausible_variant if pair.name_system == "palladius" else \
        plausible_variant_pinyin if pair.name_system == "pinyin" else None
    for e in entries:
        target = e.get("target", "")
        words = _name_words(target)
        if e.get("kind") in NAME_KINDS and words:
            stems = [stem(w) for w in words]
            present = any(any(t.startswith(st) for t in tr_words) for st in stems)
            # Варианты: похожие на имя слова, которые НЕ являются формой целевого имени.
            variants = set()
            for t in tr_words:
                if any(t.startswith(st) for st in stems):
                    continue
                if all_stems and any(t.startswith(s) for s in all_stems if len(s) >= 3):
                    continue  # это другое имя из глоссария
                for w in words:
                    if variant and variant(t, w):
                        variants.add(t)
            if variants:
                issues.append({"type": "name_variant", "level": "hard", "term": e["source"],
                               "msg": _("{0} должно быть «{1}», найдено: {2}").format(e['source'], target, ', '.join(sorted(variants)))})
            elif not present:
                issues.append({"type": "name_missing", "level": "soft", "term": e["source"],
                               "msg": _("нет имени «{0}» ({1}) — заменено местоимением или искажено?").format(target, e['source'])})
        elif target and e.get("kind") not in NAME_KINDS:
            stems = [stem(w.lower()) for w in re.split(r"\s+", target) if len(w) >= 3]
            low = tr.lower()
            if stems and not all(st in low for st in stems):
                issues.append({"type": "term", "level": "soft", "term": e["source"],
                               "msg": _("термин {0} обычно «{1}» — здесь не найден").format(e['source'], target)})
    if pair.name_system == "palladius":
        issues.extend(foreign_name_issues(tr, entries, gl, src))
    if pair.ru_target:
        if NUMBERS_DIGITS:
            issues.extend(number_word_issues(tr))
        issues.extend(gender_issues(tr, entries))
    return issues


def glossary_stems(gl: Glossary, pair=None) -> set[str]:
    pair = pair or _default_pair()
    stem = word_stem if pair.tgt.script == "cyrillic" else _plain_stem
    out = set()
    for e in gl.entries:
        if e.get("kind") in NAME_KINDS:
            for w in _name_words(e.get("target", "")):
                out.add(stem(w))
    return out


# Шумные типы: в прогоне 3 агент разобрал ~50 таких замечаний вручную, все ложные (имя заменено
# местоимением — нормальный русский текст). new_name движок обрабатывает сам (автодобавление в глоссарий).
NOISY = {"name_missing", "new_name"}


def check_chapter(state: dict, gl: Glossary, include_noisy: bool = False, pair=None) -> dict:
    """Отчёт по главе: {seg_id: [issues]} + сводка."""
    stems = glossary_stems(gl, pair)
    per: dict[int, list[dict]] = {}
    segs = _bead_view(state["segments"])
    for s in segs:
        iss = check_segment(s["src"], s.get("tr"), gl.relevant(s["src"]), stems, gl, pair)
        if not include_noisy:
            iss = [i for i in iss if i["type"] not in NOISY]
        if iss:
            per[s["id"]] = iss
    for sid, iss in chapter_level_issues(segs).items():
        per.setdefault(sid, []).extend(iss)
    hard = sum(1 for v in per.values() for i in v if i["level"] == "hard")
    soft = sum(1 for v in per.values() for i in v if i["level"] == "soft")
    return {"issues": per, "hard": hard, "soft": soft, "segments": len(state["segments"])}


def _bead_view(segments: list[dict]) -> list[dict]:
    """Сегменты для проверок: продолжения (cont — абзац оригинала разбит в переводе на несколько) склеиваются с
    головой, убранные очисткой (dropped) пропускаются. Для своего перевода (1:1) ничего не меняется."""
    out: list[dict] = []
    for s in segments:
        if s.get("dropped"):
            continue
        if s.get("cont") and out:
            out[-1] = {**out[-1], "tr": ((out[-1].get("tr") or "") + " " + (s.get("tr") or "")).strip()}
        else:
            out.append(s)
    return out


def chapter_level_issues(segs: list[dict]) -> dict[int, list[dict]]:
    """Ошибки, видимые только на всей главе.

    - duplicate (hard): два абзаца с одинаковым переводом — признак сдвига (на романе в 100 глав починка
      разорванного абзаца «перевела» соседний, и одна реплика оказалась дважды);
    - length_shift (soft): несколько абзацев подряд с переводом в 2+ раза длиннее/короче обычного для главы.
    """
    import math
    import statistics
    out: dict[int, list[dict]] = {}
    seen: dict[str, int] = {}
    for s in segs:
        t = re.sub(r"\W+", "", (s.get("tr") or "").lower())
        if len(t) < 30:
            continue
        key = t[:60]
        if key in seen:
            for sid in (seen[key], s["id"]):
                out.setdefault(sid, []).append({"type": "duplicate", "level": "hard",
                                                "msg": _("перевод абзацев [{0}] и [{1}] совпадает — сдвиг?").format(seen[key], s['id'])})
        else:
            seen[key] = s["id"]
    lens = [(max(han_count(s["src"]), len(s["src"]) // 2), len(s.get("tr") or "")) for s in segs]
    ratios = [t / h for h, t in lens if h >= 10]
    g = statistics.median(ratios) if len(ratios) >= 8 else 0
    if g > 0:  # большинство абзацев без перевода — сравнивать длины не с чем (и делить на 0 нельзя)
        bad = [abs(math.log((t + 12) / ((h + 4) * g))) > math.log(2.0) for h, t in lens]
        for k, s in enumerate(segs):
            if bad[k] and sum(bad[max(0, k - 1):k + 2]) >= 2:
                out.setdefault(s["id"], []).append({"type": "length_shift", "level": "soft",
                                                    "msg": _("длина перевода не соответствует оригиналу у соседних абзацев — возможен сдвиг")})
    return out


def name_variants_report(texts: dict[int, str], gl: Glossary) -> list[dict]:
    """Межглавная согласованность: какие написания имён встречаются в переводе (по всем главам)."""
    report = []
    all_words: dict[str, set[int]] = {}
    # Только слова с заглавной в середине предложения («…сказал Ян Синя») — иначе «Лицо», «Лично» в начале
    # фразы дают ложные срабатывания.
    mid = re.compile(r"(?<=[а-яё,;:] )([А-ЯЁ][а-яёъ]+)")
    other_stems = glossary_stems(gl)
    for ch, t in texts.items():
        for w in mid.findall(t):
            if is_palladius_word(w) or is_palladius_word(w[:-1]) or is_palladius_word(w[:-2]):
                all_words.setdefault(w, set()).add(ch)
    for e in gl.entries:
        if e.get("kind") not in NAME_KINDS:
            continue
        for w in _name_words(e.get("target", "")):
            if len(w) < 4:
                continue
            st = word_stem(w)
            bad = {t: sorted(chs)[:8] for t, chs in all_words.items()
                   if abs(len(t) - len(w)) <= 1 and plausible_variant(t, w)
                   and not any(t.startswith(s) for s in other_stems if len(s) >= 3 and s != st)}
            if bad:
                report.append({"term": e["source"], "target": e["target"], "suspicious": bad})
    return report
