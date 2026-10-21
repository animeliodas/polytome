# Протокол инструментов Polytome

Сгенерировано `eval/gen_protocol.py` из `polytome/tools.py`. Один и тот же реестр используется агентом
(OpenAI `tools` или текстовый `<tool_call>`) и MCP-сервером (`python -m polytome.mcp_server <книга>`).

Текстовый протокол для моделей без нативных инструментов:
```
<tool_call>{"name": "translate", "arguments": {"chapters": "1-5"}}</tool_call>
```
Результат возвращается сообщением `<tool_result name="translate">…</tool_result>`.

| инструмент | меняет данные | аргументы | описание |
|---|---|---|---|
| `workspace_status` |  | — | Состояние книги: главы, сколько переведено, глоссарий, библия. |
| `list_chapters` |  | `chapters`: string, `only`: string | Список глав с размером и статусом перевода. |
| `read_source` |  | `chapter`*: integer, `from_id`: integer, `to_id`: integer | Прочитать оригинал главы (абзацы с номерами). Диапазон абзацев необязателен. |
| `read_translation` |  | `chapter`*: integer, `from_id`: integer, `to_id`: integer, `with_source`: boolean | Прочитать перевод главы рядом с оригиналом (по абзацам). |
| `search` |  | `query`*: string, `scope`: string, `chapters`: string, `regex`: boolean, `limit`: integer | Поиск по книге (подстрока или regex) в оригинале или переводе. Для проверки согласованности. |
| `transcribe` |  | `names`*: array, `kind`: string | Детерминированная транскрипция китайских имён/топонимов по Палладию (pypinyin). Используй вместо собственной памяти. |
| `glossary_list` |  | `filter`: string, `kind`: string, `status`: string, `limit`: integer | Показать глоссарий (фильтр по подстроке/типу/статусу). |
| `glossary_set` | да | `entries`*: array | Добавить/обновить термины глоссария (пакетом). Имена — kind=person/place/org: их написание потом проверяется кодом во всех главах. status: confirmed (по умолчанию) | candidate. |
| `verify_genders` |  | — | Сверить пол всех персонажей глоссария с уликами ОРИГИНАЛА (родство: «X，Y的弟弟», обращения 姐姐/哥哥, титулы 先生/小姐, 名叫X的女子). Делай после записи персонажей. |
| `glossary_remove` | да | `sources`*: array | Удалить термины из глоссария. |
| `extract_terms` | да | `chapters`*: string | Найти в главах новые имена и термины (модель-извлекатель). Имена добавляются автоматически со status=auto (транскрипция по Палладию), прочие термины — status=candidate (не используются, пока не подтвердишь через glossary_set). |
| `resolve_candidates` | да | `chapters`: string | Разобрать все неподтверждённые записи глоссария (status=candidate/auto) ОДНИМ вызовом модели-редактора с контекстом из оригинала: keep / fix / drop. Гораздо дешевле, чем разбирать вручную. Возвращает список изменений — просмотри его. |
| `bible_read` |  | — | Прочитать библию книги (правила перевода этой книги). |
| `bible_write` | да | `content`*: string | Записать библию книги целиком (markdown). Коротко и конкретно: о книге, персонажи (пол, отношения, обращения), стиль, особые правила. |
| `bible_edit` | да | `old`*: string, `new`*: string | Точечная правка библии: заменить фрагмент old на new. |
| `translate` | да | `chapters`*: string, `retranslate`: boolean, `review`: boolean, `parallel_chapters`: integer | Перевести главы движком (батчи, шов контекста, авто-починка, рецензент). Возвращает отчёт по главам. Уже переведённые пропускаются, если не retranslate=true. |
| `check` |  | `chapters`*: string, `include_soft`: boolean | Детерминированная проверка перевода глав: пропуски, иероглифы, обрезания, имена не по глоссарию, числа. hard — точно ошибки, soft — подозрения. |
| `name_consistency` |  | `chapters`: string | Межглавная проверка написания имён: ищет в переводе формы, похожие на имена из глоссария, но не являющиеся их правильными формами. |
| `review` | да | `chapter`*: integer, `instruction`: string | Прогнать рецензента по уже переведённой главе (с доп. указанием, например «проверь пол персонажа X»). Правки применяются, если проходят проверки. |
| `edit_segment` | да | `chapter`*: integer, `id`*: integer, `old`: string, `new`*: string | Точечная правка перевода абзаца: заменить фрагмент old на new (или весь текст, если old не задан). |
| `retranslate` | да | `chapter`*: integer, `ids`*: array, `instruction`: string, `mode`: string | Перевести заново указанные абзацы главы с инструкцией (mode=retranslate) или исправить их по инструкции, сохранив остальное (mode=fix). |
| `replace_in_translation` | да | `old`*: string, `new`*: string, `chapters`: string, `regex`: boolean, `dry_run`: boolean | Массовая замена в переводе (например, переименование персонажа). Сначала dry_run=true — покажет, что изменится. |
| `translate_text` |  | `text`*: string | Перевести произвольный текст (не главу книги) с учётом глоссария и библии. |
| `import_text` | да | `path`*: string, `pattern`: string | Импортировать оригинал из одного большого txt-файла: разрезать на главы по заголовкам (第N章 / Глава N / Chapter N) и сохранить в папку оригинала. |
| `export_book` |  | `format`: string, `chapters`: string, `path`: string, `title`: string, `author`: string | Собрать книгу из переведённых глав: EPUB (с обложкой и оглавлением), FB2 или TXT. |
| `prepare_book` | да | `sample_chapters`: integer | Подготовить новую книгу одной операцией (без пошаговой работы агента): имена и термины из первых глав → разбор кандидатов → сверка пола → черновик библии. |
| `estimate` |  | `chapters`: string | Оценить токены и время перевода глав в текущем профиле (без LLM). |
| `set_profile` | да | `profile`*: string | Профиль работы этой книги: quality | balanced | economy | local. |
| `repair_chapters` | да | `chapters`*: string, `review`: boolean | Починить готовый перевод глав (свой или импортированный чужой): нормализация чисел и пунктуации + адресная починка hard-ошибок. Корректные абзацы не трогаются. |
| `clean_book` | да | `target`: string, `apply`: boolean, `drop_lines`: array, `drop_regex`: array | Найти и (apply=true) убрать мусор: служебные строки сайтов, заметки/благодарности автора в конце глав. target: translation | source. Без apply — только отчёт. |
| `edit_genders` | да | `chapters`*: string | Исправить род персонажей в ГОТОВОМ переводе глав (когда оригинала нет или он не нужен): по таблице персонажей (glossary kind=person с gender). Правит только род. |
| `job_start` | да | `chapters`: string, `profile`: string, `export`: string | Запустить перевод ФОНОВЫМ процессом и сразу вернуться (для долгих задач и клиентов с таймаутом вызова, например MCP/Hermes). Дальше — job_status. |
| `job_status` |  | — | Статус фонового перевода: готово/осталось, ETA, токены, хвост лога (без LLM). |
| `job_stop` | да | — | Мягко остановить фоновый перевод (прогресс сохранится; продолжить — job_start/continue). |

`*` — обязательный аргумент. Диапазон глав: `'1-5,8'` или `'all'`.
