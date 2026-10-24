"""Тексты для замеров моделей (polytome bench). Написаны для Polytome и распространяются вместе с ним (GPL-3):
чужие книги в публичный набор не попадают.

Тексты короткие (две главы на пару языков), но нагружают то, что проверяет код: имена персонажей с известным
правильным написанием (глоссарий), женские персонажи с глаголами прошедшего времени (род в русском), числа
цифрами и словами, китайские кавычки 「」 и диалоги. Изменили текст — меняется версия набора (SUITE_VERSION),
и результаты старой версии не смешиваются с новыми.
"""
from __future__ import annotations

import hashlib
import json

ZH = [
    ("第一章 雨夜来客", [
        "青石镇的雨下了整整三天。林晓雨关上药铺的门，把最后一盏灯点亮。她今年二十二岁，父亲去世以后，"
        "一个人撑起了这间小小的药铺。",
        "「有人吗？」门外传来一个男人的声音。",
        "林晓雨犹豫了一下，还是打开了门。门口站着一个年轻人，浑身湿透，左手捂着右臂，指缝里渗出血来。"
        "他说自己叫陈子明，是从省城来的。",
        "「我需要包扎一下，」陈子明低声说，「我可以付钱，一百二十元够不够？」",
        "她没有接他的钱，只是让他坐在柜台旁边，转身去取纱布和药酒。伤口不深，但很长，像是被刀划开的。"
        "她一边清洗伤口，一边问他是怎么受的伤。",
        "陈子明沉默了很久，才说：「路上遇到了几个人，他们想要我身上的一封信。」他从怀里拿出一个油纸包，"
        "放在桌上。「这封信必须在5月18日之前送到白云寺。」",
        "林晓雨看了一眼窗外。白云寺在山上，离青石镇有四十里路，雨天的山路最难走。她想起父亲常说的话："
        "救人要救到底。",
        "「明天一早我带你上山，」她说，「今晚你就睡在后屋吧。」",
    ]),
    ("第二章 山路", [
        "天还没亮，林晓雨就把陈子明叫醒了。她背上竹篓，里面装着干粮、草药和一把旧伞。陈子明的手臂好了一些，"
        "但脸色依然苍白。",
        "他们走到半山腰时，遇见了砍柴的老人周大山。周大山认识林晓雨，笑着问她这么早要去哪里。",
        "「去白云寺送药，」林晓雨回答。她没有提那封信。",
        "周大山看了陈子明一眼，压低声音说：「昨天傍晚有三个外地人在镇口打听一个受伤的年轻人。你们小心一点。」",
        "陈子明的脸色更白了。林晓雨谢过老人，带着陈子明离开大路，走上了一条只有采药人才知道的小路。"
        "小路又窄又滑，她走在前面，每一步都踩得很稳。",
        "中午的时候，雨终于停了。山间的雾慢慢散开，远处露出了白云寺的屋顶。寺门口站着一位女尼，她叫赵秋月，"
        "是寺里的知客。",
        "赵秋月接过油纸包，看了看封口的红印，点了点头。「师父等这封信等了2年，」她说，「你们辛苦了。」",
        "回去的路上，陈子明对林晓雨说：「我欠你一条命。」林晓雨笑了笑，说：「那就等你下次来青石镇的时候，"
        "帮我把药铺的屋顶修好。」",
    ]),
]

EN = [
    ("Chapter 1. The Letter", [
        "Margaret Ellison had run the lighthouse on Gull Point for eleven years, and in all that time no one had "
        "ever knocked on her door after midnight.",
        "The knock came at a quarter past one. She took the lamp from the hook, wrapped her grey shawl around her "
        "shoulders and opened the door a few inches.",
        "A young man stood on the step, soaked to the skin. \"I'm sorry,\" he said. \"My boat went onto the rocks "
        "below the point. My name is Thomas Reed.\"",
        "She let him in, sat him by the stove and poured him a cup of tea from the pot she kept warm through the "
        "night. He drank it in three long swallows and asked for another.",
        "\"Where were you going in this weather?\" Margaret asked.",
        "\"To Harwick,\" Thomas said. \"I have to deliver a letter to the harbour master before the 14th of March. "
        "If it arrives late, 200 families will lose their homes.\"",
        "Margaret looked at the letter he held out. The envelope was wet, but the red seal was intact. She had seen "
        "that seal once before, on the deed to the lighthouse itself.",
    ]),
    ("Chapter 2. The Crossing", [
        "By morning the storm had blown itself out. Margaret pulled her own small boat down to the water and "
        "checked the oars twice before she let Thomas climb in.",
        "The crossing to Harwick took four hours. Thomas rowed for the first half, until his hands blistered; then "
        "Margaret took the oars and rowed the rest of the way without a word.",
        "At the harbour, an old clerk named Samuel Brooks told them the harbour master had left for the city that "
        "morning. Thomas sat down on a coil of rope and covered his face with his hands.",
        "Margaret did not sit down. She asked the clerk when the next train left, bought two tickets with the last "
        "of her savings and walked Thomas to the station herself.",
        "They reached the city at 6 o'clock in the evening, forty minutes before the office closed. The harbour "
        "master read the letter twice, signed it, and stamped it with the same red seal.",
        "On the way back, Thomas asked how he could ever repay her. Margaret thought about it for a long time. "
        "\"Paint the lighthouse,\" she said at last. \"It hasn't been painted since 1987.\"",
    ]),
]

# Глоссарий: source → {kind, gender, target по языку перевода}. Имена из иероглифов для ru/en — транскрипция
# Палладием/пиньинем (как её проверяет код); здесь записаны явно, чтобы набор не зависел от версии транскрипции.
GLOSSARY = {
    "zh": [
        {"source": "林晓雨", "kind": "person", "gender": "f", "target": {"ru": "Линь Сяоюй", "en": "Lin Xiaoyu"}},
        {"source": "陈子明", "kind": "person", "gender": "m", "target": {"ru": "Чэнь Цзымин", "en": "Chen Ziming"}},
        {"source": "赵秋月", "kind": "person", "gender": "f", "target": {"ru": "Чжао Цююэ", "en": "Zhao Qiuyue"}},
        {"source": "周大山", "kind": "person", "gender": "m", "target": {"ru": "Чжоу Дашань", "en": "Zhou Dashan"}},
        {"source": "青石镇", "kind": "place", "target": {"ru": "Цинши", "en": "Qingshi"}},
        {"source": "白云寺", "kind": "place", "target": {"ru": "Байюнь", "en": "Baiyun"}},
    ],
    "en": [
        {"source": "Margaret Ellison", "kind": "person", "gender": "f", "target": {"ru": "Маргарет Эллисон"}},
        {"source": "Thomas Reed", "kind": "person", "gender": "m", "target": {"ru": "Томас Рид"}},
        {"source": "Samuel Brooks", "kind": "person", "gender": "m", "target": {"ru": "Сэмюэл Брукс"}},
        {"source": "Harwick", "kind": "place", "target": {"ru": "Харвик"}},
    ],
}

TEXTS = {"zh": ZH, "en": EN}

# Пары языков набора: источник → перевод.
PAIRS = ("zh-ru", "zh-en", "en-ru")


def suite_version() -> str:
    """Отпечаток набора: результаты сравнимы только при одинаковой версии."""
    blob = json.dumps({"texts": TEXTS, "glossary": GLOSSARY, "pairs": PAIRS}, ensure_ascii=False, sort_keys=True)
    return "1-" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]


def chapters(src: str) -> list[list[str]]:
    """Главы на языке src: [заголовок, абзацы…] — как их пишет импорт (первый абзац главы — заголовок)."""
    return [[title, *paras] for title, paras in TEXTS[src]]


def glossary(src: str, tgt: str) -> list[dict]:
    out = []
    for e in GLOSSARY[src]:
        target = e["target"].get(tgt)
        if target:
            out.append({k: v for k, v in {"source": e["source"], "target": target, "kind": e["kind"],
                                          "gender": e.get("gender")}.items() if v})
    return out
