# Встраивание Polytome в приложения

[English](INTEGRATION.md) · **Русский**

Polytome можно вызывать четырьмя способами. Все работают поверх одного API (`polytome/api.py`), поэтому методы,
параметры и события везде одинаковые.

| способ | для кого | как |
|---|---|---|
| Python | скрипты, боты, GUI на Python | `from polytome import Polytome` |
| Android (Kotlin/Java) | читалки и другие мобильные приложения | Chaquopy: Python внутри APK, `call(method, json)` |
| JSON-RPC по stdin/stdout | Electron, C#, Go, Rust, Kotlin desktop | `polytome serve` |
| MCP | ИИ-агенты (Hermes, Claude Code) | `polytome mcp <книга>`, скил `polytome integrate hermes` |

Входы и выходы — JSON-совместимые словари. Книга задаётся путём к её папке (её возвращает `import_book`).

## Python

```python
from polytome import Polytome

h = Polytome({                                   # конфиг целиком в памяти: на диск ничего не пишется
    "providers": {"openrouter": {"api_key": "sk-..."}},
    "slots": {"strong": "openrouter:<модель>", "fast": "openrouter:<модель>"},
    "profile": "economy",
    "target_lang": "ru",                         # язык чтения по умолчанию (иначе — язык системы)
}, home="/путь/к/рабочей/папке")              # там будут books/ и черновики
# Polytome() без аргументов берёт конфиг CLI (polytome.json + secrets.json).

book = h.import_book("D:/Загрузки/книга.epub", target_lang="en")["path"]   # язык оригинала определится сам
print(h.check())                               # {"ready": true, ...} — ключи и модели на месте
h.translate(book, chapters="1-5", on_event=print)   # блокирует до конца; wait=False — в фоновом потоке
page = h.chapter(book, 1)                      # {"source": [...], "translation": [...], "aligned": true}
h.export(book, "epub")                         # {"path": ".../Название.epub", ...}
```

## Android (Kotlin) через Chaquopy

[Chaquopy](https://chaquo.com/chaquopy/) (MIT) встраивает Python в APK. Все зависимости Polytome написаны на
чистом Python (httpx, pypinyin, pymorphy3), поэтому собираются без нативного кода.

`app/build.gradle.kts`:
```kotlin
plugins {
    id("com.android.application")
    id("com.chaquo.python")
}
chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            install("polytome")                              // с PyPI, после публикации
            // install("../polytome/dist/polytome-0.5.0-py3-none-any.whl")  // или локальная сборка: python -m build
        }
    }
}
```

Обёртка: весь обмен идёт JSON-строками, без возни с типами Python.
```kotlin
interface PolytomeListener { fun onEvent(json: String) }       // Python вызовет listener.onEvent(...)

class PolytomeBridge(filesDir: File, configJson: String, listener: PolytomeListener) {
    private val api: PyObject = Python.getInstance().getModule("polytome.api").get("Polytome")!!
        .callAttr("from_json", configJson, filesDir.resolve("polytome").absolutePath, listener)

    /** Любой метод API: call("chapter", """{"book": "...", "n": 1}""") → {"ok": true, "result": ...} */
    fun call(method: String, paramsJson: String = "{}"): JSONObject =
        JSONObject(api.callAttr("call", method, paramsJson).toString())
}
```

Правила для Android:
- **Вызовы — не из UI-потока** (`Dispatchers.IO`). `call("translate")` и `call("resume")` сами уходят в фон
  и сразу возвращают `{"started": true}`; прогресс приходит в `onEvent`, текущее состояние — `call("status")`.
- **Долгий перевод держите в foreground-сервисе** (`foregroundServiceType="dataSync"`) с уведомлением о
  прогрессе. Если система всё же убьёт процесс, ничего не теряется: прогресс сохраняется после каждого
  батча, при следующем запуске `call("status")` покажет `"state": "interrupted"`, а `call("resume")` продолжит
  с места остановки.
- **Ключи** храните в Android Keystore / EncryptedSharedPreferences и передавайте в `configJson`
  (`providers.<id>.api_key`). Polytome держит их только в памяти.
- `cancel` — мягкая остановка: текущие батчи допишутся (секунды, до ~1 мин), событие `job_done` придёт с
  полем `cancelled`.

## JSON-RPC по stdin/stdout (`polytome serve`)

```
polytome serve --home D:/PolytomeData
```
Одна строка — одно сообщение JSON-RPC 2.0:
```
→ {"jsonrpc":"2.0","id":1,"method":"configure","params":{"config":{"providers":{"openrouter":{"api_key":"sk-..."}}},"home":"D:/PolytomeData"}}
← {"jsonrpc":"2.0","id":1,"result":{"profile":"balanced",...}}
→ {"jsonrpc":"2.0","id":2,"method":"import_book","params":{"source":"D:/книга.epub"}}
← {"jsonrpc":"2.0","id":2,"result":{"path":"D:/PolytomeData/books/Книга","chapters_total":120,...}}
→ {"jsonrpc":"2.0","id":3,"method":"translate","params":{"book":"D:/PolytomeData/books/Книга","chapters":"1-3"}}
← {"jsonrpc":"2.0","id":3,"result":{"started":true,"book":"...","chapters":3}}
← {"jsonrpc":"2.0","method":"event","params":{"ev":"chapter_done","book":"...","n":1,"report":{...}}}
```
Ошибки: `{"error": {"code": -32000, "message": "...", "data": {"type": "PolytomeError"}}}`; неизвестный метод —
`-32601`. Каждый запрос выполняется в своём потоке, поэтому ответы могут прийти не по порядку: сверяйте `id`.

Node.js:
```js
const { spawn } = require("child_process");
const p = spawn("polytome", ["serve"]);
let id = 0; const wait = new Map();
require("readline").createInterface({ input: p.stdout }).on("line", (line) => {
  const m = JSON.parse(line);
  if (m.method === "event") return console.log("event", m.params.ev);
  wait.get(m.id)?.(m); wait.delete(m.id);
});
const rpc = (method, params = {}) => new Promise((ok) => {
  wait.set(++id, ok); p.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
});
```

## Методы

| метод | параметры | результат |
|---|---|---|
| `version` | — | `{"polytome": "0.5.0", "api": 1}` |
| `check` | `ping=false` | готовность: модель и ключ для каждой роли; `ping` — короткий запрос к моделям |
| `providers` | — | каталог провайдеров: `id, title, local, needs_key, has_key, keys_url, suggest` |
| `profiles` | — | `quality / balanced / economy / local` с описанием |
| `models` | `provider` | актуальные модели провайдера из его `/models`: `id, name`, а если провайдер сообщает — `context`, `price_in` / `price_out` (USD за 1 млн токенов), `free` |
| `config_summary` | — | профиль, модели по ролям, параметры движка |
| `languages` | `source_lang="zh"` | языки для выбора: `code, name, native, script`, какие проверки есть для пары (`name_system`, `gender_check`), `default` |
| `import_book` | `source, library?, title?, fix_breaks=false, target_lang?, source_lang?` | сведения о книге (+ `import`: отчёт импорта). EPUB, TXT или папка глав; язык оригинала определяется по тексту |
| `set_languages` | `book, source_lang?, target_lang?, force=false` | сменить язык перевода (с готовыми главами — только `force`) |
| `books` | `library?` | книги в библиотеке (по умолчанию `<home>/books`) |
| `book_info` | `book` | `title, original_title, author, source_lang, target_lang, chapters_total, chapters_translated, cover, job` |
| `chapters` | `book` | оглавление: `n, title, source_title, translated, partial` |
| `chapter` | `book, n, which="both"` | `source[]`, `translation[]` (или `null`), `aligned` — абзацы 1:1 |
| `estimate` | `book, chapters?, profile?` | токены и минуты на непереведённые главы |
| `translate` | `book, chapters?, profile?, review=true, retranslate=false, prepare=true, export?, wait` | отчёт или `{"started": true}` |
| `resume` | `book, wait` | продолжить прерванную задачу с теми же настройками |
| `cancel` | `book` | `{"stopping": true}` |
| `status` | `book` | прогресс без LLM: `job.state` (`running / done / cancelled / interrupted / failed`), `done, left, eta_min, tokens_this_run` |
| `translate_text` | `text, book?, source_lang?, target_lang?, profile?` | перевод фрагмента; с `book` — имена по глоссарию книги, без — язык определяется по тексту |
| `translate_meta` | `book, toc=true` | название и автор на языке перевода (китайский автор — транскрипцией, без модели; «佚名» — «автор неизвестен») и, с `toc`, заголовки ещё не переведённых глав — библиотека и оглавление сразу на языке читателя; готовое пропускается; прогресс — события `toc_progress` |
| `explain` | `book, n, paragraph, fragment?, question?` | ответ читателю о фрагменте (слова, имена, термины, идиомы) с учётом глоссария и библии книги; `paragraph` — с 0 → `{answer}` |
| `suggest_fix` | `book, n, paragraph, instruction?, mode=fix` | новый перевод одного абзаца, **без сохранения**: `fix` — поправить текущий по указанию, `retranslate` — перевести заново → `{source, old, new}` |
| `set_paragraph` | `book, n, paragraph, text` | сохранить перевод абзаца (прежний текст остаётся в истории главы) |
| `export` | `book, fmt="epub"\|"fb2"\|"txt"\|"all", out?, chapters?` | `{"path", "chapters", "missing_total", ...}` |

`chapters` везде принимает `"all"`, `"1-10,15"`, число или список чисел. Через `call()`/JSON-RPC `translate`
и `resume` всегда работают в фоне (`wait=false`).

## События

Каждое событие — словарь с полями `ev`, `book` (путь), `t` (unix-время) и своими полями:

| `ev` | поля | когда |
|---|---|---|
| `prepare_start` / `prepare_done` | `report` (глоссарий, библия, конфликты пола) | новая книга: поиск имён и библия перед первым переводом |
| `job_start` | `total, resumed` | начало перевода набора глав |
| `extract_start` / `extract_done` | `chapters` / `added, pending` | поиск новых имён перед каждой волной (25 глав) |
| `chapter_start` | `n, segments` | глава взята в работу |
| `resume` | `n, done, total, stage` | глава продолжается с места остановки |
| `batch_done` | `n, batch, batches` | готов батч главы (для прогресс-бара внутри главы) |
| `repair` | `n, seg, why` | код нашёл проблему в абзаце и переводит его заново |
| `chapter_done` | `n, report` (`complete, segments, hard_issues, review_fixes, seconds`) | глава готова и записана |
| `chapter_cancelled` | `n, done, total` | остановлено; сохранено `done` абзацев |
| `job_done` | `report` (`translated, failed, cancelled, HALTED, tokens, export`) | задача закончилась (в том числе остановкой) |
| `job_error` | `error` | задача упала с исключением |
| `log` | `msg` | служебное сообщение (провайдер недоступен, получен STOP…) |

`HALTED` в `job_done` — провайдер несколько раз подряд не ответил, задача остановилась сама, чтобы не жечь
книгу на ошибках: проверьте ключ (`check`) и сделайте `resume`.

## Конфиг

Тот же, что у CLI (`polytome.json`), только может передаваться словарём:

```json
{
  "profile": "economy",
  "providers": {"openrouter": {"api_key": "sk-..."},
                "мой-сервер": {"base_url": "http://192.168.1.10:11434/v1", "local": true}},
  "slots": {"strong": "openrouter:<модель>", "fast": "openrouter:<модель>", "local": "мой-сервер:qwen3:14b"},
  "parallel": 6,
  "target_lang": "en",
  "style_text": "свои общие правила перевода (иначе встроенные styles/<язык>.md)"
}
```
Слоты: `strong` (основной переводчик), `fast` (дешёвая), `reviewer`, `extractor`, `agent`, `local`. Какой слот
какой роли достаётся, решает профиль (см. `profiles`).

## Языки

Перевод — с любого языка на любой: язык оригинала определяется по тексту (или `source_lang`), язык чтения —
`target_lang` книги (по умолчанию из конфига, иначе язык системы). Экран выбора языка в приложении — метод
`languages`. Сообщения API (`error.message`) — на языке системы (английский или русский); в приложении лучше
показывать свои тексты по `error.type`. Принудительно: переменная окружения `POLYTOME_UI_LANG=en|ru`.

## Совместная работа с CLI и Hermes

Папка книги общая: книгу, начатую в приложении, можно продолжить командой `polytome continue <книга>` и наоборот.
Одновременно книгу переводит только один процесс: `translate` вернёт ошибку, если над ней уже работает
другой (CLI, Hermes, другой экземпляр). `cancel` остановит и чужой процесс через файл `.polytome/STOP`.
