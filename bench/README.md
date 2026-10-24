# Model bench / Замеры моделей

[Русский — ниже](#по-русски)

Which model translates books well, and what does it cost? `polytome bench` runs a model on the same test texts
through the real pipeline — batches, then the code-guided repair — and scores the result with code, not with
another model's opinion.

**The texts.** One short story (an apothecary, a wounded visitor with a letter, a mountain path), written for
Polytome anew in seven languages — Chinese, Japanese, Korean, English, French, German, Spanish — with local names
and the same layout: two chapters of a title and 8 paragraphs. 13 pairs: each of them → Russian and → English
(`zh-ru`, `ja-ru`, `ko-ru`, `en-ru`, `fr-ru`, `de-ru`, `es-ru`, `zh-en`, `ja-en`, `ko-en`, `fr-en`, `de-en`, `es-en`).

**The score** is the average of three shares, 0–100:

- **first pass** — paragraphs with no serious issue right after translation: no leftovers of the original (for
  French, German and Spanish — their function words), the original's names and numbers kept, names spelled as in
  the glossary, correct gender of characters in Russian, numbers as digits, a plausible length, nothing skipped or
  repeated;
- **after repair** — the same after Polytome fixes what its checks found (what a reader gets);
- **traps** — 8 meaning traps per pair with a known right answer, at the same spots in every language: a dropped
  subject (she, not he — the verbs in Russian), a name that is an ordinary word (白云 "white cloud", 森 "forest",
  하늘 "sky", Lapin, Wolf, Lobo, Fox), an idiom whose literal translation is wrong (吃醋 — to be jealous, not "to eat
  vinegar"; 배가 아프다, avoir le cafard, Tomaten auf den Augen, pan comido…), "spring" as a season and not a name,
  a number and a date in words, an organization name from the glossary.

The traps check meaning, not wording: "the eighteenth of May" is as right as "May 18" in English, "закрывала" as
"закрыла" in Russian, and an idiom may keep its image as long as the meaning is there. Plus speed, tokens and price
(if the provider reports prices) per 1000 characters of the original.

The table of all submitted results: **[LEADERBOARD.md](LEADERBOARD.md)**. The first, simpler suite (short texts
without traps, where strong models all reached 100) is kept in [archive/](archive/).

**Limits.** Style is not measured, and 8 traps per pair catch typical mistakes of mid-level models but do not
separate the strongest ones, which pass them all. Each result file contains the translation itself, so you can
read and compare. New traps are welcome — they live in `polytome/bench_suite.py`.

## Run

```
polytome bench -m openrouter:deepseek/deepseek-chat -p zh-ru -p ja-ru
polytome bench -m ollama-cloud:glm-5.2 -p all
```

`-m` and `-p` can be repeated (`-p all` — every pair); the default is your translator model on `zh-ru`. A run of
one pair usually costs 4–20 thousand tokens (slow reasoning models — up to 100 thousand). A run stops at 100,000 tokens (`--budget`): a model that needs more
is too wasteful for books and is listed as "did not fit the token budget" (small reasoning models can burn hundreds
of thousands). Results are saved to `bench/results/<pair>/<date>_<model>.json`. In the Bayun app: Settings →
Translation → "Check model", then "Share result".

## Add your result

1. Fork this repository, run the bench (or take the file shared from Bayun) and put the JSON into
   `bench/results/<pair>/`.
2. Optionally rebuild the table: `polytome bench --leaderboard`.
3. Open a pull request. One file per run, so pull requests do not conflict.

A result contains no keys and no server addresses — only the provider id, the model, the numbers and the
translation of the test texts. Results of another suite version (`suite`) are not mixed into the table.

---

## По-русски

Какая модель хорошо переводит книги и сколько это стоит? `polytome bench` прогоняет модель на одинаковых тестовых
текстах через настоящий конвейер — батчи, затем починка по замечаниям — и оценивает результат кодом, а не мнением
другой модели.

**Тексты.** Одна короткая история (аптекарша, раненый гость с письмом, горная тропа), написанная для Polytome заново
на семи языках — китайском, японском, корейском, английском, французском, немецком, испанском — с местными именами и
одинаковой разметкой: две главы, в каждой заголовок и 8 абзацев. 13 пар: каждый из них → на русский и → на
английский.

**Score** — среднее трёх долей, 0–100:

- **сразу** — абзацы без серьёзных замечаний сразу после перевода: нет остатков оригинала (для французского,
  немецкого и испанского — их служебных слов), имена и числа оригинала на месте и написаны как в глоссарии, верный
  род персонажей в русском, числа цифрами, правдоподобная длина, ничего не пропущено и не повторено;
- **после починки** — то же после того, как Polytome исправил найденное проверками (что получит читатель);
- **ловушки** — 8 мест со смыслом и заранее известным правильным ответом на пару, на одних и тех же местах во всех
  языках: опущенное подлежащее (она, а не он — глаголы в русском), имя-слово (白云 «белое облако», 森 «лес»,
  하늘 «небо», Lapin, Wolf, Lobo, Fox), идиома, у которой дословный перевод неверен (吃醋 — ревновать, а не «есть
  уксус»; 배가 아프다, avoir le cafard, Tomaten auf den Augen, pan comido…), «весна» — время года, а не имя, число
  и дата прописью, название организации из глоссария.

Ловушки проверяют смысл, а не формулировку: «the eighteenth of May» в английском так же верно, как «May 18»,
«закрывала» — как «закрыла», а идиома может сохранить образ, если смысл передан. Плюс скорость, токены и цена (если
провайдер сообщает цены) на 1000 знаков оригинала.

Таблица всех присланных результатов: **[LEADERBOARD.md](LEADERBOARD.md)**. Первый, более простой набор (короткие
тексты без ловушек — сильные модели все получали 100) сохранён в [archive/](archive/).

**Ограничения.** Стиль не оценивается, а 8 ловушек на пару ловят типичные ошибки моделей среднего уровня, но не
различают самых сильных — те проходят все. В каждом файле результата есть сам перевод — его можно прочитать и
сравнить. Новые ловушки приветствуются — они в `polytome/bench_suite.py`.

## Запуск

```
polytome bench -m openrouter:deepseek/deepseek-chat -p zh-ru -p ja-ru
polytome bench -m ollama-cloud:glm-5.2 -p all
```

`-m` и `-p` можно повторять (`-p all` — все пары); по умолчанию — ваша модель-переводчик на `zh-ru`. Прогон одной
пары обычно стоит 4–20 тысяч токенов (медленные рассуждающие модели — до 100 тысяч). На 100 000 токенов прогон останавливается (`--budget`): модель, которой нужно
больше, для книг слишком расточительна и попадает в список «не уложились в лимит» (маленькие рассуждающие модели
могут сжечь сотни тысяч). Результаты сохраняются в `bench/results/<пара>/<дата>_<модель>.json`. В приложении Баюн:
Настройки → Перевод → «Проверить модель», затем «Поделиться результатом».

## Как добавить свой результат

1. Сделайте форк репозитория, запустите замер (или возьмите файл, которым поделился Баюн) и положите JSON в
   `bench/results/<пара>/`.
2. По желанию пересоберите таблицу: `polytome bench --leaderboard`.
3. Откройте pull request. Один прогон — один файл, поэтому запросы не конфликтуют.

В результате нет ключей и адресов серверов — только провайдер, модель, цифры и перевод тестовых текстов. Результаты
другой версии набора (`suite`) в таблицу не смешиваются.
