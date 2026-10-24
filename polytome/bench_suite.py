"""Тексты для замеров моделей (polytome bench), версия 2. Написаны для Polytome и распространяются вместе с ним
(GPL-3): чужие книги в публичный набор не попадают.

Одна и та же история (аптекарша, раненый гость с письмом, горная тропа) написана заново на каждом исходном языке —
с местными именами и одинаковой разметкой: две главы, в каждой заголовок и 8 абзацев. Поэтому одни и те же
ловушки стоят на одних и тех же местах во всех языках, и результаты разных пар сравнимы.

Кроме проверок движка (остатки оригинала, имена по глоссарию, род, числа, пропуски) в тексты встроены ловушки со
смыслом — их проверяет код по заранее известному правильному ответу, а не другая модель:
- подлежащее опущено (в китайском, японском, корейском это естественно) — род глаголов в русском, she/he в английском;
- имя персонажа — обычное слово (白云 «белое облако», 森 «лес», 하늘 «небо», Lapin, Wolf, Lobo, Fox): переводить нельзя;
- идиома, у которой дословный перевод неверен (吃醋 — ревновать, а не «есть уксус»);
- число и дата прописью, «старшая сестра», название организации из глоссария.
Изменили текст или ловушку — меняется версия набора (suite_version), старые результаты не смешиваются с новыми.
"""
from __future__ import annotations

import hashlib
import json

# ------------------------------------------------------------------ тексты: (заголовок, [8 абзацев]) × 2 главы
TEXTS: dict[str, list[tuple[str, list[str]]]] = {
    "zh": [
        ("第一章 雨夜来客", [
            "青石镇的雨已经下了整整三天。林晓雨的姐姐春天去了京城，从那以后，镇上这间小药铺就只剩她一个人照看。",
            "天黑以后，关好门窗，数了数当天的收入，吹灭油灯，上了楼。",
            "刚躺下，楼下就传来急促的敲门声，接着是一个男人的声音：「有人吗？」",
            "门外站着一个浑身湿透的年轻人，左臂缠着渗血的布条。他说自己叫白云，是青龙会的人。",
            "「我需要包扎伤口，」白云低声说，「我可以付钱，三千五百文够不够？」",
            "他从怀里取出一封用油纸包好的信：「这封信必须在五月十八日之前送到清风寺。」",
            "包扎的时候，他苦笑着说：「我未婚妻要是知道我半夜敲一个姑娘的门，一定会吃醋。」",
            "林晓雨没有接他的钱，只说：「明天一早，我带你上山。」",
        ]),
        ("第二章 山路", [
            "天刚亮，两人就出发了。林晓雨背着竹篓，里面装着干粮、草药和一把旧伞。",
            "半山腰上，他们遇见了砍柴的老人周大山。老人放下斧头，压低声音说，昨天傍晚有三个外地人在镇口打听一个受伤的年轻人。",
            "「晓雨，你们走小路吧，」周大山说，「大路上不安全。」林晓雨向他道了谢。",
            "小路又窄又滑，林晓雨走在前面，每一步都踩得很稳。白云跟在后面，一句话也没说。",
            "中午雨停了，山间的雾慢慢散开，远处露出了清风寺的屋顶。寺门口站着一位尼姑，她叫赵秋月，是寺里负责接待客人的知客。",
            "赵秋月接过信，看了看封口的红印，点了点头：「住持等这封信等了2年。」",
            "白云对林晓雨说：「我欠你一条命。」林晓雨笑了：「那你下次来，帮我把药铺的屋顶修好。」白云留在了寺里。",
            "傍晚，林晓雨独自下山。到家时天已经黑了，点上灯，在柜台后面坐了很久。",
        ]),
    ],
    "ja": [
        ("第一章 雨の夜の客", [
            "石川町の雨は、もう三日も降り続いていた。佐藤春香の姉は春に都へ出ていき、それ以来、町の小さな薬屋は春香ひとりで切り盛りしている。",
            "日が暮れると、雨戸を閉め、その日の売り上げを数え、灯りを吹き消して二階へ上がった。",
            "横になったとたん、下で激しく戸を叩く音がして、男の声がした。「誰かいませんか」",
            "戸口には、ずぶ濡れの若い男が立っていた。左腕に巻いた布には血がにじんでいる。男は森健一と名乗り、青龍会の者だと言った。",
            "「傷の手当てをお願いしたい」と森は小声で言った。「お代は払います。三千五百円で足りますか」",
            "森は懐から油紙に包んだ手紙を取り出した。「この手紙を五月十八日までに清風寺へ届けなければならないんです」",
            "春香は心の中でため息をついた。薬草の仕入れの季節で、猫の手も借りたいほど忙しいというのに。",
            "それでも春香は金を受け取らず、「明日の朝早く、山へ案内します」とだけ言った。",
        ]),
        ("第二章 山道", [
            "夜が明けるとすぐ、二人は出発した。春香は籠を背負い、中には握り飯と薬草と古い傘が入っていた。",
            "山の中腹で、薪を割っていた老人の田中茂に会った。老人は斧を置き、声をひそめて言った。昨日の夕方、よそ者が三人、町の入り口で怪我をした若い男のことを尋ね回っていたという。",
            "「春香ちゃん、裏道を行きなさい」と田中は言った。「表の道は危ない」春香は礼を言った。",
            "裏道は細くて滑りやすかった。春香が先に立ち、一歩一歩しっかりと踏みしめて進んだ。森は黙って後に続いた。",
            "昼になると雨はやみ、山の霧がゆっくりと晴れて、遠くに清風寺の屋根が見えてきた。門の前には尼僧が立っていた。寺で客の応対をしている山本静という人だった。",
            "山本は手紙を受け取り、封の赤い印を確かめてうなずいた。「住職はこの手紙を2年も待っていました」",
            "森は春香に「命の恩人です」と言った。春香は笑って、「じゃあ今度来たら、薬屋の屋根を直してください」と答えた。森は寺に残った。",
            "夕方、春香はひとりで山を下りた。家に着くともう暗く、灯りをつけて、帳場の奥に長いあいだ座っていた。",
        ]),
    ],
    "ko": [
        ("제1장 비 오는 밤의 손님", [
            "돌다리 마을에는 사흘째 비가 내리고 있었다. 김하늘의 언니는 봄에 서울로 떠났고, 그 뒤로 마을의 작은 약방은 하늘 혼자 꾸려 가고 있었다.",
            "날이 저물자 덧문을 닫고, 그날 번 돈을 세어 보고, 등잔불을 끈 뒤 위층으로 올라갔다.",
            "막 자리에 누웠을 때, 아래층에서 다급하게 문을 두드리는 소리가 나더니 남자 목소리가 들렸다. \"누구 계세요?\"",
            "문 앞에는 흠뻑 젖은 젊은 남자가 서 있었다. 왼팔에 감은 천에는 피가 배어 있었다. 남자는 자신을 박준호라고 소개하며 청룡회 사람이라고 했다.",
            "\"상처를 좀 치료해 주세요.\" 박준호가 낮은 목소리로 말했다. \"돈은 드리겠습니다. 삼천오백 냥이면 되겠습니까?\"",
            "그는 품에서 기름종이로 싼 편지를 꺼냈다. \"이 편지를 오월 십팔일까지 청풍사에 전해야 합니다.\"",
            "하늘은 속으로 생각했다. 약방이 잘된다고 이웃들이 배가 아파하던 참인데, 한밤중에 낯선 남자까지 찾아오다니.",
            "그래도 하늘은 돈을 받지 않고 \"내일 아침 일찍 산으로 안내해 드릴게요\"라고만 말했다.",
        ]),
        ("제2장 산길", [
            "날이 밝자마자 두 사람은 길을 나섰다. 하늘은 바구니를 등에 메었는데, 그 안에는 주먹밥과 약초와 낡은 우산이 들어 있었다.",
            "산 중턱에서 장작을 패던 노인 이영수를 만났다. 노인은 도끼를 내려놓고 목소리를 낮춰 말했다. 어제 저녁 낯선 사람 세 명이 마을 어귀에서 다친 젊은 남자를 찾고 있었다는 것이다.",
            "\"하늘아, 샛길로 가거라.\" 이영수가 말했다. \"큰길은 위험하다.\" 하늘은 노인에게 고맙다고 인사했다.",
            "샛길은 좁고 미끄러웠다. 하늘이 앞장서서 한 걸음 한 걸음 단단히 디디며 나아갔다. 박준호는 말없이 뒤를 따랐다.",
            "정오가 되자 비가 그치고 산안개가 천천히 걷히면서, 멀리 청풍사의 지붕이 보이기 시작했다. 절 문 앞에는 비구니 한 명이 서 있었다. 절에서 손님을 맞는 일을 하는 정혜 스님이었다.",
            "정혜 스님은 편지를 받아 봉인의 붉은 도장을 확인하고 고개를 끄덕였다. \"주지 스님께서 이 편지를 2년이나 기다리셨습니다.\"",
            "박준호가 하늘에게 말했다. \"생명의 은인입니다.\" 하늘은 웃으며 대답했다. \"그럼 다음에 오시면 약방 지붕이나 고쳐 주세요.\" 박준호는 절에 남았다.",
            "저녁 무렵 하늘은 혼자 산을 내려왔다. 집에 도착하니 벌써 어두웠다. 등잔불을 켜고 계산대 뒤에 한참을 앉아 있었다.",
        ]),
    ],
    "fr": [
        ("Chapitre 1. Un visiteur sous la pluie", [
            "Il pleuvait sur Pierrefeu depuis trois jours. Au printemps, la sœur aînée de Claire Martin était partie pour Paris, et depuis, Claire tenait seule la petite pharmacie du village.",
            "À la nuit tombée, elle ferma les volets, compta la recette de la journée, souffla la lampe et monta à l'étage.",
            "À peine s'était-elle allongée qu'on frappa violemment à la porte, en bas. Une voix d'homme demanda : « Il y a quelqu'un ? »",
            "Sur le seuil se tenait un jeune homme trempé jusqu'aux os, le bras gauche serré dans un linge taché de sang. Il se présenta : il s'appelait Pierre Lapin et appartenait à la Confrérie du Dragon d'azur.",
            "« Il faut me soigner, dit Lapin à voix basse. Je peux payer. Trois mille cinq cents francs, cela suffira ? »",
            "Il sortit de sa veste une lettre enveloppée de papier huilé. « Cette lettre doit arriver à l'abbaye de Saint-Vent avant le dix-huit mai. »",
            "Depuis le départ de sa sœur, Claire avait souvent le cafard le soir, mais cette nuit-là, elle n'eut pas le temps d'y penser.",
            "Elle refusa pourtant son argent et dit seulement : « Demain à l'aube, je vous conduirai dans la montagne. »",
        ]),
        ("Chapitre 2. Le chemin de la montagne", [
            "Au lever du jour, ils se mirent en route. Claire portait sur le dos un panier contenant du pain, des herbes médicinales et un vieux parapluie.",
            "À mi-pente, ils rencontrèrent le vieux Gaston Roux, qui fendait du bois. Il posa sa hache et leur dit à voix basse que, la veille au soir, trois étrangers avaient interrogé les gens à l'entrée du village au sujet d'un jeune homme blessé.",
            "« Prenez le sentier, ma petite Claire, dit Gaston Roux. La grand-route n'est pas sûre. » Claire le remercia.",
            "Le sentier était étroit et glissant. Claire marchait devant, posant chaque pas avec assurance. Lapin la suivait sans un mot.",
            "À midi, la pluie cessa ; la brume se dissipa lentement et le toit de l'abbaye de Saint-Vent apparut au loin. Devant la porte se tenait une religieuse, sœur Agnès, chargée d'accueillir les visiteurs.",
            "Sœur Agnès prit la lettre, examina le sceau rouge et hocha la tête. « La mère abbesse attend cette lettre depuis 2 ans. »",
            "Lapin dit à Claire : « Je vous dois la vie. » Claire sourit : « Alors, la prochaine fois, réparez le toit de ma pharmacie. » Lapin resta à l'abbaye.",
            "Le soir, Claire redescendit seule de la montagne. Quand elle arriva chez elle, il faisait déjà nuit ; elle alluma la lampe et resta longtemps assise derrière le comptoir.",
        ]),
    ],
    "de": [
        ("Kapitel 1. Ein Gast in der Regennacht", [
            "In Felsheim regnete es seit drei Tagen. Im Frühling war Anna Webers ältere Schwester nach Berlin gegangen, und seitdem führte Anna die kleine Apotheke des Dorfes allein.",
            "Bei Einbruch der Dunkelheit schloss sie die Fensterläden, zählte die Einnahmen des Tages, blies die Lampe aus und ging nach oben.",
            "Kaum hatte sie sich hingelegt, als unten heftig an die Tür geklopft wurde. Eine Männerstimme rief: „Ist da jemand?“",
            "Vor der Tür stand ein junger Mann, nass bis auf die Haut, den linken Arm in ein blutiges Tuch gewickelt. Er stellte sich als Konrad Wolf vor und sagte, er gehöre zur Bruderschaft des Azurdrachen.",
            "„Ich brauche einen Verband“, sagte Wolf leise. „Ich kann bezahlen. Reichen dreitausendfünfhundert Mark?“",
            "Er zog einen in Ölpapier gewickelten Brief aus seiner Jacke. „Dieser Brief muss vor dem achtzehnten Mai im Kloster Steinbach sein.“",
            "Anna hatte wohl Tomaten auf den Augen gehabt: Erst jetzt bemerkte sie, wie blass er war.",
            "Trotzdem nahm sie sein Geld nicht an und sagte nur: „Morgen früh bringe ich Sie in die Berge.“",
        ]),
        ("Kapitel 2. Der Bergpfad", [
            "Bei Tagesanbruch brachen sie auf. Anna trug einen Korb auf dem Rücken, darin Brot, Heilkräuter und ein alter Regenschirm.",
            "Auf halber Höhe trafen sie den alten Hans Becker, der Holz hackte. Er legte die Axt beiseite und sagte leise, gestern Abend hätten sich drei Fremde am Dorfeingang nach einem verletzten jungen Mann erkundigt.",
            "„Nehmt den Pfad, Anna“, sagte Hans Becker. „Die Landstraße ist nicht sicher.“ Anna dankte ihm.",
            "Der Pfad war schmal und rutschig. Anna ging voran und setzte jeden Schritt mit Bedacht. Wolf folgte ihr schweigend.",
            "Gegen Mittag hörte der Regen auf, der Nebel lichtete sich langsam, und in der Ferne tauchte das Dach von Kloster Steinbach auf. Am Tor stand eine Nonne, Schwester Hildegard, die im Kloster die Gäste empfing.",
            "Schwester Hildegard nahm den Brief, prüfte das rote Siegel und nickte. „Die Äbtissin wartet seit 2 Jahren auf diesen Brief.“",
            "Wolf sagte zu Anna: „Ich verdanke Ihnen mein Leben.“ Anna lächelte: „Dann reparieren Sie beim nächsten Mal das Dach meiner Apotheke.“ Wolf blieb im Kloster.",
            "Am Abend stieg Anna allein vom Berg hinab. Als sie zu Hause ankam, war es schon dunkel; sie zündete die Lampe an und saß lange hinter dem Ladentisch.",
        ]),
    ],
    "es": [
        ("Capítulo 1. Un visitante en la noche de lluvia", [
            "En Peñaseca llovía desde hacía tres días. En primavera, la hermana mayor de Lucía Romero se había marchado a Madrid, y desde entonces Lucía atendía sola la pequeña botica del pueblo.",
            "Al caer la noche, cerró los postigos, contó lo que había ganado ese día, apagó la lámpara de un soplo y subió al piso de arriba.",
            "Apenas se había acostado cuando llamaron con fuerza a la puerta de abajo. Una voz de hombre preguntó: —¿Hay alguien?",
            "En el umbral había un joven calado hasta los huesos, con el brazo izquierdo envuelto en un paño manchado de sangre. Dijo que se llamaba Diego Lobo y que pertenecía a la Hermandad del Dragón Azul.",
            "—Necesito que me curen —dijo Lobo en voz baja—. Puedo pagar. ¿Bastará con tres mil quinientos reales?",
            "Sacó de la chaqueta una carta envuelta en papel encerado. —Esta carta tiene que llegar al monasterio de San Blas antes del dieciocho de mayo.",
            "Para Lucía, curar aquella herida era pan comido, pero la carta la inquietaba.",
            "Aun así, no aceptó su dinero y solo dijo: —Mañana al amanecer lo llevaré a la montaña.",
        ]),
        ("Capítulo 2. El sendero de la montaña", [
            "Al despuntar el día se pusieron en camino. Lucía llevaba a la espalda una cesta con pan, hierbas medicinales y un paraguas viejo.",
            "A media ladera se encontraron con el viejo Tomás Gil, que estaba partiendo leña. Dejó el hacha y les dijo en voz baja que la tarde anterior tres forasteros habían preguntado en la entrada del pueblo por un joven herido.",
            "—Id por el sendero, Lucía —dijo Tomás Gil—. El camino real no es seguro. Lucía le dio las gracias.",
            "El sendero era estrecho y resbaladizo. Lucía iba delante, pisando con firmeza a cada paso. Lobo la seguía en silencio.",
            "Al mediodía dejó de llover, la niebla se disipó despacio y a lo lejos apareció el tejado del monasterio de San Blas. Ante la puerta esperaba una monja, sor Inés, encargada de recibir a los visitantes.",
            "Sor Inés tomó la carta, examinó el sello rojo y asintió. —La abadesa lleva 2 años esperando esta carta.",
            "Lobo le dijo a Lucía: —Le debo la vida. Lucía sonrió: —Entonces, la próxima vez que venga, arrégleme el tejado de la botica. Lobo se quedó en el monasterio.",
            "Al atardecer, Lucía bajó sola de la montaña. Cuando llegó a casa ya era de noche; encendió la lámpara y se quedó mucho rato sentada detrás del mostrador.",
        ]),
    ],
    "en": [
        ("Chapter 1. A Visitor in the Rain", [
            "In Harwick it had been raining cats and dogs for three days. In the spring Margaret Hale's older sister had left for London, and since then Margaret had run the village apothecary on her own.",
            "When night fell, she closed the shutters, counted the day's takings, blew out the lamp and went upstairs.",
            "She had barely lain down when someone hammered on the door below. A man's voice called, \"Is anyone there?\"",
            "On the step stood a young man soaked to the skin, his left arm wrapped in a bloodstained cloth. He said his name was Thomas Fox and that he belonged to the Azure Dragon Society.",
            "\"I need my arm seen to,\" Fox said quietly. \"I can pay. Will three thousand five hundred shillings do?\"",
            "He took a letter wrapped in oilpaper from his coat. \"This letter must reach St Bride's Abbey before the eighteenth of May.\"",
            "As she cleaned the wound, he smiled weakly. \"My fiancée would never forgive me for knocking on a young woman's door at midnight.\"",
            "Even so, Margaret would not take his money. \"At first light,\" she said, \"I'll take you up the mountain.\"",
        ]),
        ("Chapter 2. The Mountain Path", [
            "They set out at dawn. Margaret carried a basket on her back with bread, medicinal herbs and an old umbrella.",
            "Halfway up the slope they met old Samuel Brooks, who was splitting firewood. He put down his axe and told them quietly that the evening before, three strangers had been asking at the edge of the village about a wounded young man.",
            "\"Take the footpath, Maggie,\" said Samuel Brooks. \"The high road isn't safe.\" Margaret thanked him.",
            "The footpath was narrow and slippery. Margaret went first, placing every step with care. Fox followed her without a word.",
            "At noon the rain stopped, the mist slowly lifted, and in the distance the roof of St Bride's Abbey came into view. At the gate stood a nun, Sister Agnes, whose task was to receive visitors.",
            "Sister Agnes took the letter, examined the red seal and nodded. \"The abbess has been waiting for this letter for 2 years.\"",
            "Fox said to Margaret, \"I owe you my life.\" Margaret smiled. \"Then next time you come, mend the roof of my shop.\" Fox stayed at the abbey.",
            "In the evening Margaret walked down the mountain alone. When she reached home it was already dark; she lit the lamp and sat for a long time behind the counter.",
        ]),
    ],
}

# ------------------------------------------------------------------ глоссарий (его получает модель)
ORG = {"ru": "Общество Лазурного Дракона", "en": "Azure Dragon Society"}


def _p(source, ru, en, gender=None, short=None):
    """Персонаж; short — как его зовут коротко в тексте: (оригинал, ru, en) — отдельная запись глоссария."""
    out = [{"source": source, "kind": "person", "gender": gender, "target": {"ru": ru, "en": en}}]
    if short:
        out.append({"source": short[0], "kind": "person", "gender": gender, "target": {"ru": short[1], "en": short[2]}})
    return out


def _pl(source, ru, en, kind="place"):
    return [{"source": source, "kind": kind, "target": {"ru": ru, "en": en}}]


def _flat(*groups):
    return [e for g in groups for e in g]


GLOSSARY: dict[str, list[dict]] = {
    "zh": _flat(_p("林晓雨", "Линь Сяоюй", "Lin Xiaoyu", "f", ("晓雨", "Сяоюй", "Xiaoyu")),
                _p("白云", "Бай Юнь", "Bai Yun", "m"), _p("周大山", "Чжоу Дашань", "Zhou Dashan", "m"),
                _p("赵秋月", "Чжао Цююэ", "Zhao Qiuyue", "f"), _pl("青龙会", ORG["ru"], ORG["en"], "org"),
                _pl("清风寺", "Цинфэн", "Qingfeng"), _pl("青石镇", "Цинши", "Qingshi")),
    "ja": _flat(_p("佐藤春香", "Сато Харука", "Haruka Sato", "f", ("春香", "Харука", "Haruka")),
                _p("森健一", "Мори Кэнъити", "Ken'ichi Mori", "m", ("森", "Мори", "Mori")),
                _p("田中茂", "Танака Сигэру", "Shigeru Tanaka", "m", ("田中", "Танака", "Tanaka")),
                _p("山本静", "Ямамото Сидзука", "Shizuka Yamamoto", "f", ("山本", "Ямамото", "Yamamoto")),
                _pl("青龍会", ORG["ru"], ORG["en"], "org"), _pl("清風寺", "Сэйфу", "Seifu"),
                _pl("石川町", "Исикава", "Ishikawa")),
    "ko": _flat(_p("김하늘", "Ким Ханыль", "Kim Haneul", "f", ("하늘", "Ханыль", "Haneul")),
                _p("박준호", "Пак Джунхо", "Park Junho", "m"), _p("이영수", "Ли Ёнсу", "Lee Youngsoo", "m"),
                _p("정혜", "Чонхе", "Jeonghye", "f"), _pl("청룡회", ORG["ru"], ORG["en"], "org"),
                _pl("청풍사", "Чхонпхунса", "Cheongpungsa"), _pl("돌다리", "Тольдари", "Doldari")),
    "fr": _flat(_p("Claire Martin", "Клер Мартен", "Claire Martin", "f", ("Claire", "Клер", "Claire")),
                _p("Pierre Lapin", "Пьер Лапен", "Pierre Lapin", "m", ("Lapin", "Лапен", "Lapin")),
                _p("Gaston Roux", "Гастон Ру", "Gaston Roux", "m"), _p("Agnès", "Агнес", "Agnès", "f"),
                _pl("Confrérie du Dragon d'azur", ORG["ru"], ORG["en"], "org"),
                _pl("Saint-Vent", "Сен-Ван", "Saint-Vent"), _pl("Pierrefeu", "Пьерфё", "Pierrefeu")),
    "de": _flat(_p("Anna Weber", "Анна Вебер", "Anna Weber", "f", ("Anna", "Анна", "Anna")),
                _p("Konrad Wolf", "Конрад Вольф", "Konrad Wolf", "m", ("Wolf", "Вольф", "Wolf")),
                _p("Hans Becker", "Ганс Беккер", "Hans Becker", "m"), _p("Hildegard", "Хильдегард", "Hildegard", "f"),
                _pl("Bruderschaft des Azurdrachen", ORG["ru"], ORG["en"], "org"),
                _pl("Steinbach", "Штайнбах", "Steinbach"), _pl("Felsheim", "Фельсхайм", "Felsheim")),
    "es": _flat(_p("Lucía Romero", "Лусия Ромеро", "Lucía Romero", "f", ("Lucía", "Лусия", "Lucía")),
                _p("Diego Lobo", "Диего Лобо", "Diego Lobo", "m", ("Lobo", "Лобо", "Lobo")),
                _p("Tomás Gil", "Томас Хиль", "Tomás Gil", "m"), _p("Inés", "Инес", "Inés", "f"),
                _pl("Hermandad del Dragón Azul", ORG["ru"], ORG["en"], "org"),
                _pl("San Blas", "Сан-Блас", "San Blas"), _pl("Peñaseca", "Пеньясека", "Peñaseca")),
    "en": _flat(_p("Margaret Hale", "Маргарет Хейл", "Margaret Hale", "f", ("Margaret", "Маргарет", "Margaret")),
                _p("Thomas Fox", "Томас Фокс", "Thomas Fox", "m", ("Fox", "Фокс", "Fox")),
                _p("Samuel Brooks", "Сэмюэл Брукс", "Samuel Brooks", "m"), _p("Agnes", "Агнес", "Agnes", "f"),
                _pl("Azure Dragon Society", ORG["ru"], ORG["en"], "org"),
                _pl("St Bride's Abbey", "Сент-Брайдс", "St Bride's Abbey"), _pl("Harwick", "Харвик", "Harwick")),
}

# ------------------------------------------------------------------ ловушки: что обязано быть и чего быть не должно
# ch, p — глава и абзац (0 — заголовок); p=None — весь перевод. require — каждое выражение должно найтись,
# forbid — ни одно не должно. Регистр не важен, если в выражении нет явного (?-i).
# Род глаголов героини — любые вид и приставка (закрыла / закрывала, погасила / гасила); мужской род тех же глаголов — ошибка.
_FEM_1 = r"\b\w*(крыла|крывала|творила|хлопнула|считала|считывала|гасила|дула|дувала|тушила|нялась|нималась|шла|правилась)\b"
_MASC_1 = r"\b\w*(крыл|крывал|творил|хлопнул|считал|считывал|гасил|дул|дувал|тушил|нялся|нимался|шёл|шел|правился)\b"
_FEM_2 = r"\b\w*(спустилась|спускалась|сошла|вернулась|возвращалась|дошла|пришла|бралась|биралась|жгла|жигала|светила|села|садилась|сидела)\b"
_MASC_2 = r"\b\w*(спустился|спускался|сошёл|сошел|вернулся|возвращался|дошёл|дошел|пришёл|пришел|брался|бирался|жёг|жег|жигал|светил|сел|садился|сидел)\b"
_SHE = r"\b(she|her)\b"
_HE = r"\b(he|him|his|himself)\b"

SHARED_TRAPS = [
    {"id": "spring_and_sister", "ch": 1, "p": 1, "why": "сестра уехала весной: «весна» — время года, а не имя",
     "ru": {"require": [r"сестр", r"весн"], "forbid": [r"чу[нь]+т[яь]а?н"]}, "en": {"require": [r"sister", r"spring"]}},
    {"id": "dropped_subject_1", "ch": 1, "p": 2, "why": "подлежащее опущено — действует героиня",
     "ru": {"require": [_FEM_1], "forbid": [_MASC_1]}, "en": {"require": [_SHE], "forbid": [_HE]}},
    {"id": "organization", "ch": 1, "p": 4, "why": "название организации — по глоссарию",
     "ru": {"require": [r"лазурн", r"дракон"]}, "en": {"require": [r"azure", r"dragon"]}},
    # в русском промпт требует цифр; в английском прописью — нормальный литературный стиль: проверяется значение
    {"id": "number_words", "ch": 1, "p": 5, "why": "3500 прописью — в русском цифрами",
     "ru": {"require": [r"3[\s.,]?500"]},
     "en": {"require": [r"3[\s.,]?500|three thousand,? five hundred|thirty-five hundred"]}},
    # 五月 / 오월 в старинной обстановке можно понять и как «пятый месяц» (лунный календарь) — тоже верно
    {"id": "date_words", "ch": 1, "p": 6, "why": "18 мая прописью — верное число и месяц",
     "ru": {"require": [r"(18|восемнадцат\w*)(-?(го|е))?\s+(числа\s+|дня\s+)?(ма[яй]|(пятого|5-?го)\s+(лунного\s+)?месяц)"]},
     "en": {"require": [r"may\s+(the\s+)?(18|eighteen)|(18(th)?|eighteenth)\s+(of\s+)?may|"
                        r"(18(th)?|eighteenth)\s+(day\s+)?of\s+the\s+fifth\s+(lunar\s+)?month"]}},
    {"id": "dropped_subject_2", "ch": 2, "p": 8, "why": "героиня одна; подлежащее опущено или «она»",
     "ru": {"require": [_FEM_2], "forbid": [_MASC_2]}, "en": {"require": [_SHE], "forbid": [_HE]}},
]

SOURCE_TRAPS: dict[str, list[dict]] = {
    "zh": [
        {"id": "name_is_word", "ch": None, "p": None, "why": "白云 — имя, а не «белое облако»",
         "ru": {"forbid": [r"облак"]}, "en": {"forbid": [r"white\s+cloud", r"\bclouds?\b"]}},
        {"id": "idiom", "ch": 1, "p": 7, "why": "吃醋 — ревновать, а не «есть уксус»",
         "ru": {"require": [r"ревн"], "forbid": [r"уксус"]}, "en": {"require": [r"jealous"], "forbid": [r"vinegar"]}},
    ],
    "ja": [
        {"id": "name_is_word", "ch": None, "p": None, "why": "森 — фамилия Мори, а не «лес»",
         "ru": {"forbid": [r"\bлес(а|у|ом|е)?\b"]}, "en": {"forbid": [r"\bforests?\b"]}},
        {"id": "idiom", "ch": 1, "p": 7, "why": "猫の手も借りたい — очень много дел (образ кошки можно сохранить, смысл — нет)",
         "ru": {"require": [r"занят|дел[ао]?\b|дел\w* по горло|работ|хлопот|завал|некогда|запар|суматох|горяч|"
                            r"рук\w*(\s+\w+)?\s+не\s+хвата|не\s+хвата\w*(\s+\w+)?\s+рук|невпроворот|с ног сбива|ни минуты"]},
         "en": {"require": [r"bus(y|ier|iest)|swamped|work|run off|rushed|hectic|short.handed|no time|hands full|pair of hands"]}},
    ],
    "ko": [
        {"id": "name_is_word", "ch": None, "p": None, "why": "하늘 — имя героини, а не «небо»",
         "ru": {"forbid": [r"\bнеб(о|а|у|ом|е)\b|\bнебес"]}, "en": {"forbid": [r"\bsky\b|\bskies\b|\bheaven"]}},
        {"id": "idiom", "ch": 1, "p": 7, "why": "배가 아프다 — завидовать (образ живота можно сохранить, смысл — нет)",
         "ru": {"require": [r"завид|завист|ревн"]},
         "en": {"require": [r"env(y|ious)|jealous|resent|begrudg"]}},
    ],
    "fr": [
        {"id": "name_is_word", "ch": None, "p": None, "why": "Lapin — фамилия, а не «кролик»",
         "ru": {"forbid": [r"\bкролик"]}, "en": {"forbid": [r"(?-i:\brabbits?\b)"]}},
        {"id": "idiom", "ch": 1, "p": 7, "why": "avoir le cafard — тосковать, таракан ни при чём",
         "ru": {"require": [r"тоск|грус|хандр|печал|уныл|уныни|одинок|тяжело на душе|не по себе|тошно|кошки скребли"],
                "forbid": [r"таракан"]},
         "en": {"require": [r"blue|down|sad|gloom|low|lonely|melanchol|depress|dejected|miserable"], "forbid": [r"cockroach"]}},
    ],
    "de": [
        {"id": "name_is_word", "ch": None, "p": None, "why": "Wolf — фамилия, а не «волк»",
         "ru": {"forbid": [r"\bволк"]}, "en": {"forbid": [r"(?-i:\bwol(f|ves)\b)"]}},
        {"id": "idiom", "ch": 1, "p": 7, "why": "Tomaten auf den Augen — не замечать, помидоры ни при чём",
         "ru": {"require": [r"не замеч|не замет|не увидел|не видел|только теперь|только сейчас|лишь теперь|лишь сейчас|слеп"],
                "forbid": [r"помидор|томат"]},
         "en": {"require": [r"not notic|n't notic|only now|blind|oblivious|failed to see|hadn't seen|didn't see|not seen"],
                "forbid": [r"tomato"]}},
    ],
    "es": [
        {"id": "name_is_word", "ch": None, "p": None, "why": "Lobo — фамилия, а не «волк»",
         "ru": {"forbid": [r"\bволк"]}, "en": {"forbid": [r"(?-i:\bwol(f|ves)\b)"]}},
        {"id": "idiom", "ch": 1, "p": 7, "why": "ser pan comido — проще простого, хлеб ни при чём",
         "ru": {"require": [r"пустяк|прост|легк|раз плюнуть|ерунд|несложн|плёв|плев|труда не"], "forbid": [r"хлеб"]},
         "en": {"require": [r"easy|piece of cake|simple|breeze|child.s play|nothing|cinch"], "forbid": [r"bread"]}},
    ],
    "en": [
        {"id": "name_is_word", "ch": None, "p": None, "why": "Fox — фамилия, а не «лиса»",
         "ru": {"forbid": [r"\bлис(а|ы|у|ой|е|ица|ицы|ицу|ий|ья|ью)\b"]}},
        {"id": "idiom", "ch": 1, "p": 1, "why": "raining cats and dogs — ливень, кошки и собаки ни при чём",
         "ru": {"forbid": [r"\bкош|\bкот(ы|ов|ами)?\b|собак"]}},
    ],
}

SOURCES = ("zh", "ja", "ko", "en", "fr", "de", "es")
TARGETS = ("ru", "en")
# пары: сначала на русский (там проверок больше всего), затем на английский
PAIRS = tuple(f"{s}-{t}" for t in TARGETS for s in SOURCES if s != t)


def suite_version() -> str:
    """Отпечаток набора: результаты сравнимы только при одинаковой версии."""
    blob = json.dumps({"texts": TEXTS, "glossary": GLOSSARY, "shared": SHARED_TRAPS, "traps": SOURCE_TRAPS,
                       "pairs": PAIRS}, ensure_ascii=False, sort_keys=True)
    return "2-" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]


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


def traps(src: str, tgt: str) -> list[dict]:
    """Ловушки пары: [{"id", "ch", "p", "why", "require": [...], "forbid": [...]}]."""
    out = []
    for t in SHARED_TRAPS + SOURCE_TRAPS.get(src, []):
        rule = t.get(tgt)
        if rule:
            out.append({"id": t["id"], "ch": t["ch"], "p": t["p"], "why": t["why"],
                        "require": rule.get("require", []), "forbid": rule.get("forbid", [])})
    return out
