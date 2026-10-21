---
name: hanru-translator
description: "Перевод книг на русский через Hanru (EPUB/TXT/папка глав; китайские веб-новеллы и не только): новая книга, фоновый перевод, статус, продолжение после сбоя, проверка/починка готового перевода, сборка EPUB/FB2/TXT. Use for translating books with the Hanru CLI."
version: 0.3.0
author: animeliodas
license: GPL-3.0-or-later
metadata:
  hermes:
    tags: [translation, books, epub, fb2, chinese, russian, audiobook, novel]
    category: productivity
    config:
      - key: hanru.command
        description: "Команда запуска Hanru: путь к hanru.exe или просто hanru, если он в PATH"
        default: "hanru"
      - key: hanru.library
        description: "Папка-библиотека, где Hanru создаёт книги из EPUB/TXT"
        default: "~/Books/Hanru"
---

# Hanru — перевод книг

Hanru — консольный переводчик книг: сам готовит глоссарий и «библию» книги, переводит, проверяет имена
(Палладий), пол персонажей и числа детерминированным кодом, чинит ошибки и собирает EPUB/FB2.
Ты им **управляешь** — сам текст книги НЕ переводи и через LLM-провайдеров напрямую не гоняй.

Ниже `HANRU` = значение `hanru.command` из конфига скила (по умолчанию `hanru`), `LIB` = `hanru.library`.
В путях с пробелами и кириллицей — всегда кавычки.

## Когда использовать
- «переведи книгу/EPUB/роман», «продолжи перевод», «что с переводом?», «собери EPUB/FB2»,
  «проверь перевод на ошибки», «переведи дешевле», «поменяй модель переводчика».

## Главное правило: долгое — только в фоне
Ячейка `execute_code` живёт 300 с, а перевод книги — минуты и часы. Поэтому:
1. запуск — `HANRU run "<книга>" --background` (возвращается сразу, печатает pid и лог);
2. ожидание — `python ${HERMES_SKILL_DIR}/scripts/hanru_wait.py "<книга>" --cmd "HANRU" --max 240`
   (сам выходит до 300 с; код 0 — задача закончилась, 3 — ещё идёт: повтори вызов);
3. никогда не запускай `HANRU run` без `--background` в execute_code/terminal без фона;
4. НЕ проверяй процесс через tasklist/ps и НЕ читай лог вручную: `HANRU status "<книга>" --json` уже содержит
   `job.state` (starting → running → done/cancelled/failed/interrupted), `job.phase` (prepare/translate),
   `job.alive`, прогресс, ETA и `log_tail`. Пока `hanru_wait.py` возвращает 3 — просто вызывай его снова.

## Быстрый справочник
| задача | команда |
|---|---|
| новая книга из файла | `HANRU new "<файл.epub/.txt или папка>" --to "LIB"` |
| перевести всё (фон) | `HANRU run "<книга>" --background --export epub` |
| перевести часть | `HANRU run "<книга>" -c 1-20 --background` |
| статус (JSON, без токенов) | `HANRU status "<книга>" --json` |
| остановить | `HANRU stop "<книга>"` |
| продолжить после сбоя/остановки | `HANRU continue "<книга>" --background` |
| оценка токенов/времени | `HANRU estimate "<книга>"` |
| проверка без LLM / починка | `HANRU audit "<книга>"` / `HANRU audit "<книга>" --fix` |
| собрать книгу | `HANRU export "<книга>" --format epub` (`fb2`, `txt`, `all`) |
| профиль экономии | `HANRU profiles --set economy --book "<книга>"` |
| модели / проверка связи | `HANRU models` / `HANRU doctor` |
| точечная правка агентом Hanru | `HANRU ask "<книга>" "<задача>"` |
| убрать мусор (реклама сайта, заметки/благодарности автора) | `HANRU clean "<книга>"` → `--apply` |
| пол персонажей в готовом переводе | `HANRU cast "<книга>" --suggest` / `--set "Имя=f:кто"` → `HANRU edit "<книга>" --genders --background` |

## Процедуры

### A. Перевести книгу из файла
1. `HANRU new "<файл>" --to "LIB"` → в выводе путь книги, число глав, язык, оценка токенов. Если язык `ru` —
   переводить нечего: предложи `audit`/`export`.
2. Если книга большая (>50 глав) — покажи пользователю оценку (`HANRU estimate`) и спроси профиль, если он
   не сказал (quality / balanced / economy / local).
3. `HANRU run "<книга>" --background --export epub` (+ `--profile economy`, если просили дешевле).
4. Ожидание циклами `hanru_wait.py` (см. выше). Между циклами кратко сообщай прогресс: «готово 40/120,
   ETA ~25 мин».
5. По завершении: путь к EPUB есть в хвосте лога (`status --json` → `log_tail`) или собери: `HANRU export`.
6. Сверь: `status --json` → `chapters_translated == chapters_total`; если нет — `HANRU continue … --background`.

### B. «Продолжи перевод» / после сбоя
`HANRU status "<книга>" --json`: `job.state` = `interrupted` (процесс умер — выключение ПК), `cancelled`
(остановили) или `failed` (ошибки провайдера; см. `halt_reason`). Во всех случаях:
`HANRU continue "<книга>" --background`. Готовые главы заново не переводятся, начатые продолжаются с места.
При `failed` сначала `HANRU doctor` (ключ/провайдер), потом continue.

### C. Проверить/починить готовый перевод (свой, Hermes, сайт)
Папка книги: `Оригинал/Глава N.txt` + `Перевод/Глава N.txt`. `HANRU audit "<книга>"` (без LLM) → покажи
пользователю сводку (главы с hard-ошибками, чужие имена, конфликты пола). Починка — только с его согласия:
`HANRU audit "<книга>" --fix` (меняет только абзацы с найденными ошибками; перед массовой починкой
предложи сделать копию папки `Перевод`).

### D. Модели и провайдеры
`HANRU setup --provider <имя> --key <ключ> --strong <модель> --fast <модель> --profile <профиль>`
(провайдеры: ollama-cloud, ollama, lmstudio, openrouter, openai, anthropic, gemini, deepseek, mistral, groq,
together, aitunnel). Ключи пользователь вводит сам — не печатай их в чат. Проверка: `HANRU doctor`.

## Подводные камни
- Пути с кириллицей/пробелами — в кавычках; вывод уже в UTF-8.
- `new` предупредил «абзацы разорваны посреди фразы» (жёсткие переносы в скачанном тексте) → предложи
  `HANRU new "<папка>" --fix-breaks` (склеит с резервной копией оригинала). Из EPUB/TXT склейка делается сама.
  На разорванном тексте переводчик сдвигает абзацы; `audit` ловит это как «перевод абзацев совпадает».
- Одна книга — одна фоновая задача: повторный `run` при живой задаче вернёт ошибку «уже идёт».
- `status --json` → `job.alive=false` при `state=running` невозможен: такой статус показывается как
  `interrupted`.
- Предупреждения `GENDER_CONFLICTS` в итоге/статусе: пол персонажа в глоссарии противоречит оригиналу —
  сообщи пользователю или реши через `HANRU ask "<книга>" "проверь пол персонажа X по оригиналу и исправь"`.
- Не редактируй файлы `Перевод/` вручную во время работы задачи.

## Проверка результата
- `HANRU status "<книга>" --json` → `chapters_translated`, `job.state == "done"`, `job.failed == []`.
- `HANRU audit "<книга>"` → hard-ошибок 0 (или объясни оставшиеся).
- Файл EPUB/FB2 существует, размер > 0.

Подробные сценарии: `references/scenarios.md`.
