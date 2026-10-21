"""Улики пола по русскому тексту: роли/родство рядом с именем («сестра Линь Яо», «певица Цзян Куй»).

Машинный перевод с китайского по умолчанию ставит мужской род глаголов, а существительные («сестра»,
«певица») передаёт надёжно — поэтому они сильнее голосов по глаголам.
"""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from polytome.checks import gender_votes  # noqa: E402,F401
from polytome.workspace import chapter_number, split_paragraphs  # noqa: E402

F = (r"сестр\w*|сестрёнк\w*|сестренк\w*|мам\w*|мать|матер\w*|доч\w*|жена|жены|жене|жену|девушк\w*|девочк\w*|"
     r"госпож\w*|мисс|тёт\w*|тет[яеи]\w*|бабушк\w*|певиц\w*|актрис\w*|учительниц\w*|ведущая|ведущей|хозяйк\w*|"
     r"леди|мадам|принцесс\w*|подруг\w*|невест\w*|старшая|младшая|красавиц\w*|девчонк\w*")
M = (r"брат\w*|братишк\w*|отец|отца|отцу|отцом|пап[аеуы]|папой|сын\w*|муж|мужа|мужу|парен\w*|парня|парню|"
     r"мальчик\w*|господин\w*|мистер|дяд[яеиюь]\w*|дедушк\w*|певец|певца|певцу|актёр\w*|актер\w*|ведущий|"
     r"ведущего|хозяин\w*|принц|старший|младший|друг|друга|другу|жених\w*|парнишк\w*")


def main():
    folder = Path(sys.argv[1])
    names = [l.split()[0] + " " + l.split()[1] for l in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines() if l.strip()]
    rx = {nm: (re.compile(rf"(?<![А-Яа-яЁё])({F})\s+{re.escape(nm)}(?![а-яё])", re.I),
               re.compile(rf"(?<![А-Яа-яЁё])({M})\s+{re.escape(nm)}(?![а-яё])", re.I)) for nm in names}
    cnt = {nm: collections.Counter() for nm in names}
    ex = {nm: collections.Counter() for nm in names}
    for f in folder.glob("*.txt"):
        t = f.read_text(encoding="utf-8-sig")
        for nm, (rf, rm) in rx.items():
            if nm not in t:
                continue
            for m in rf.finditer(t):
                cnt[nm]["f"] += 1
                ex[nm][m.group(1).lower()] += 1
            for m in rm.finditer(t):
                cnt[nm]["m"] += 1
                ex[nm][m.group(1).lower()] += 1
    out = {}
    for nm in names:
        c = cnt[nm]
        out[nm] = {"f": c["f"], "m": c["m"], "words": ex[nm].most_common(4)}
        print(f"{nm:18} жен.роль {c['f']:4}  муж.роль {c['m']:4}  {ex[nm].most_common(4)}")
    Path("eval/out/khudozhnik_roles.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
