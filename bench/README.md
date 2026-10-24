# Model bench / Замеры моделей

[Русский — ниже](#по-русски)

Which model translates books well, and what does it cost? `polytome bench` runs a model on the same short test
texts (written for Polytome, two chapters per language pair: zh→ru, zh→en, en→ru) through the real pipeline —
batches, then the code-guided repair — and scores the result with Polytome's deterministic checks, not with
another model's opinion:

- **first pass** — share of paragraphs with no serious issue right after translation (does the model follow the
  task: no leftovers of the original, the original's names and numbers kept, names spelled as in the glossary,
  correct gender of characters in Russian, numbers as digits, a plausible length, nothing skipped or repeated);
- **after repair** — the same after Polytome fixes what its checks found (what a reader gets);
- **score** — the average of the two, 0–100;
- speed, tokens and price (if the provider reports prices) per 1000 characters of the original.

The table of all submitted results: **[LEADERBOARD.md](LEADERBOARD.md)**.

## Run

```
polytome bench -m openrouter:deepseek/deepseek-chat -p zh-ru -p en-ru
```

`-m` can be repeated to compare several models; the default is your translator model. A run of one pair costs
roughly 20–40 thousand tokens. Results are saved to `bench/results/<pair>/<date>_<model>.json`. In the Bayun app:
Settings → Translation → "Check model", then "Share result".

## Add your result

1. Fork this repository, run the bench (or take the file shared from Bayun) and put the JSON into
   `bench/results/<pair>/`.
2. Optionally rebuild the table: `polytome bench --leaderboard`.
3. Open a pull request. One file per run, so pull requests do not conflict.

A result contains no keys and no server addresses — only the provider id, the model, the numbers and the
translation of the test texts (so anyone can look at the quality with their own eyes). Results of an older suite
version (`suite`) are kept but not mixed into the table.

---

## По-русски

Какая модель хорошо переводит книги и сколько это стоит? `polytome bench` прогоняет модель на одинаковых коротких
текстах (написаны для Polytome, по две главы на пару языков: zh→ru, zh→en, en→ru) через настоящий конвейер —
батчи, затем починка по замечаниям — и оценивает результат детерминированными проверками Polytome, а не мнением
другой модели:

- **сразу** — доля абзацев без серьёзных замечаний сразу после перевода (понимает ли модель задачу: нет остатков
  оригинала, имена и числа оригинала на месте и написаны как в глоссарии, правильный род персонажей в русском,
  числа цифрами, правдоподобная длина, ничего не пропущено и не повторено);
- **после починки** — то же после того, как Polytome исправил найденное проверками (что получит читатель);
- **score** — среднее этих двух, 0–100;
- скорость, токены и цена (если провайдер сообщает цены) на 1000 знаков оригинала.

Таблица всех присланных результатов: **[LEADERBOARD.md](LEADERBOARD.md)**.

## Запуск

```
polytome bench -m openrouter:deepseek/deepseek-chat -p zh-ru -p en-ru
```

`-m` можно повторить, чтобы сравнить несколько моделей; по умолчанию — ваша модель-переводчик. Прогон одной пары
стоит примерно 20–40 тысяч токенов. Результаты сохраняются в `bench/results/<пара>/<дата>_<модель>.json`.
В приложении Баюн: Настройки → Перевод → «Проверить модель», затем «Поделиться результатом».

## Как добавить свой результат

1. Сделайте форк репозитория, запустите замер (или возьмите файл, которым поделился Баюн) и положите JSON в
   `bench/results/<пара>/`.
2. По желанию пересоберите таблицу: `polytome bench --leaderboard`.
3. Откройте pull request. Один прогон — один файл, поэтому запросы не конфликтуют.

В результате нет ключей и адресов серверов — только провайдер, модель, цифры и перевод тестовых текстов (чтобы
качество можно было оценить и глазами). Результаты старой версии набора (`suite`) хранятся, но в таблицу не
смешиваются.
