# Embedding Polytome in apps

**English** · [Русский](INTEGRATION.ru.md)

Polytome can be called in four ways. All of them sit on one API (`polytome/api.py`), so methods, parameters and
events are the same everywhere.

| way | for | how |
|---|---|---|
| Python | scripts, bots, Python GUIs | `from polytome import Polytome` |
| Android (Kotlin/Java) | readers and other mobile apps | Chaquopy: Python inside the APK, `call(method, json)` |
| JSON-RPC over stdin/stdout | Electron, C#, Go, Rust, Kotlin desktop | `polytome serve` |
| MCP | AI agents (Hermes, Claude Code) | `polytome mcp <book>`, the `polytome integrate hermes` skill |

Inputs and outputs are JSON-compatible dicts. A book is identified by the path to its folder (returned by
`import_book`).

## Python

```python
from polytome import Polytome

h = Polytome({                                   # the whole config in memory: nothing is written to disk
    "providers": {"openrouter": {"api_key": "sk-..."}},
    "slots": {"strong": "openrouter:<model>", "fast": "openrouter:<model>"},
    "profile": "economy",
    "target_lang": "en",                         # default reading language (otherwise — the system language)
}, home="/path/to/working/folder")               # books/ and drafts go there
# Polytome() without arguments uses the CLI config (polytome.json + secrets.json).

book = h.import_book("D:/Downloads/book.epub", target_lang="es")["path"]   # the source language is detected
print(h.check())                               # {"ready": true, ...} — keys and models are in place
h.translate(book, chapters="1-5", on_event=print)   # blocks until done; wait=False — in a background thread
page = h.chapter(book, 1)                      # {"source": [...], "translation": [...], "aligned": true}
h.export(book, "epub")                         # {"path": ".../Title.epub", ...}
```

## Android (Kotlin) via Chaquopy

[Chaquopy](https://chaquo.com/chaquopy/) (MIT) embeds Python into an APK. All Polytome dependencies are pure
Python (httpx, pypinyin, pymorphy3), so they build without native code.

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
            install("polytome")                              // from PyPI, once published
            // install("../polytome/dist/polytome-0.6.0-py3-none-any.whl")  // or a local build: python -m build
        }
    }
}
```

A wrapper: everything goes as JSON strings, no fiddling with Python types.
```kotlin
interface PolytomeListener { fun onEvent(json: String) }       // Python calls listener.onEvent(...)

class PolytomeBridge(filesDir: File, configJson: String, listener: PolytomeListener) {
    private val api: PyObject = Python.getInstance().getModule("polytome.api").get("Polytome")!!
        .callAttr("from_json", configJson, filesDir.resolve("polytome").absolutePath, listener)

    /** Any API method: call("chapter", """{"book": "...", "n": 1}""") → {"ok": true, "result": ...} */
    fun call(method: String, paramsJson: String = "{}"): JSONObject =
        JSONObject(api.callAttr("call", method, paramsJson).toString())
}
```

Rules for Android:
- **Do not call from the UI thread** (`Dispatchers.IO`). `call("translate")` and `call("resume")` go to the
  background by themselves and return `{"started": true}` immediately; progress arrives in `onEvent`, the current
  state — `call("status")`.
- **Keep a long translation in a foreground service** (`foregroundServiceType="dataSync"`) with a progress
  notification. If the system kills the process anyway, nothing is lost: progress is saved after every batch; on
  the next start `call("status")` shows `"state": "interrupted"` and `call("resume")` continues where it stopped.
- **Keys** — keep them in Android Keystore / EncryptedSharedPreferences and pass them in `configJson`
  (`providers.<id>.api_key`). Polytome keeps them in memory only.
- `cancel` is a gentle stop: current batches finish (seconds, up to ~1 min), `job_done` arrives with `cancelled`.

## JSON-RPC over stdin/stdout (`polytome serve`)

```
polytome serve --home D:/PolytomeData
```
One line — one JSON-RPC 2.0 message:
```
→ {"jsonrpc":"2.0","id":1,"method":"configure","params":{"config":{"providers":{"openrouter":{"api_key":"sk-..."}},"target_lang":"en"},"home":"D:/PolytomeData"}}
← {"jsonrpc":"2.0","id":1,"result":{"profile":"balanced",...}}
→ {"jsonrpc":"2.0","id":2,"method":"import_book","params":{"source":"D:/book.epub"}}
← {"jsonrpc":"2.0","id":2,"result":{"path":"D:/PolytomeData/books/Book","chapters_total":120,"source_lang":"zh","target_lang":"en",...}}
→ {"jsonrpc":"2.0","id":3,"method":"translate","params":{"book":"D:/PolytomeData/books/Book","chapters":"1-3"}}
← {"jsonrpc":"2.0","id":3,"result":{"started":true,"book":"...","chapters":3}}
← {"jsonrpc":"2.0","method":"event","params":{"ev":"chapter_done","book":"...","n":1,"report":{...}}}
```
Errors: `{"error": {"code": -32000, "message": "...", "data": {"type": "PolytomeError"}}}`; an unknown method —
`-32601`. Every request runs in its own thread, so replies may come out of order: match them by `id`.

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

## Methods

| method | parameters | result |
|---|---|---|
| `version` | — | `{"polytome": "0.6.0", "api": 1}` |
| `check` | `ping=false` | readiness: a model and a key for every role; `ping` — a short request to the models |
| `providers` | — | provider catalog: `id, title, local, needs_key, has_key, keys_url, suggest` |
| `profiles` | — | `quality / balanced / economy / local` with descriptions |
| `languages` | `source_lang="zh"` | languages for a picker: `code, name, native, script`, which checks exist for the pair (`name_system`, `gender_check`), `default` |
| `models` | `provider` | the provider's current models from its `/models`: `id, name`, and when the provider reports them `context`, `price_in` / `price_out` (USD per 1M tokens), `free` |
| `config_summary` | — | profile, models per role, engine parameters |
| `import_book` | `source, library?, title?, fix_breaks=false, target_lang?, source_lang?` | book info (+ `import`: the import report). EPUB, TXT or a folder of chapters; the source language is detected from the text |
| `set_languages` | `book, source_lang?, target_lang?, force=false` | change the target language (with translated chapters — only with `force`) |
| `books` | `library?` | books in the library (default `<home>/books`) |
| `book_info` | `book` | `title, original_title, author, source_lang, target_lang, chapters_total, chapters_translated, cover, job` |
| `chapters` | `book` | table of contents: `n, title, source_title, translated, partial` |
| `chapter` | `book, n, which="both"` | `source[]`, `translation[]` (or `null`), `aligned` — paragraphs 1:1 |
| `estimate` | `book, chapters?, profile?` | tokens and minutes for untranslated chapters |
| `translate` | `book, chapters?, profile?, review=true, retranslate=false, prepare=true, export?, wait` | report or `{"started": true}` |
| `resume` | `book, wait` | continue an interrupted job with the same settings |
| `cancel` | `book` | `{"stopping": true}` |
| `status` | `book` | progress without LLM: `job.state` (`running / done / cancelled / interrupted / failed`), `done, left, eta_min, tokens_this_run` |
| `translate_text` | `text, book?, source_lang?, target_lang?, profile?` | translate a fragment; with `book` — names by the book's glossary, without — the language is detected |
| `translate_meta` | `book, toc=true` | title and author in the target language (a Chinese author — by transcription, no model; «佚名» — “unknown author”) and, with `toc`, titles of not yet translated chapters, so the library and the contents read in the reader's language; done parts are skipped; progress — `toc_progress` events |
| `explain` | `book, n, paragraph, fragment?, question?` | answer a reader's question about a passage (words, names, terms, idioms) using the book's glossary and bible; `paragraph` counts from 0 → `{answer}` |
| `suggest_fix` | `book, n, paragraph, instruction?, mode=fix` | a new translation of one paragraph, **not saved**: `fix` corrects the current one by the instruction, `retranslate` translates it anew → `{source, old, new}` |
| `set_paragraph` | `book, n, paragraph, text` | save a paragraph's translation (the previous text stays in the chapter history) |
| `bench_info` | — | the model bench: suite version, language pairs, size and a rough token estimate per pair |
| `bench` | `model?, pair=zh-ru, save_dir?` | run a model on the shared test texts and score it with the code checks → a result record (no keys, no server addresses; see [bench/README.md](../bench/README.md)); progress — `bench_progress`, end — `bench_done` |
| `bench_cancel` | — | stop the running bench |
| `export` | `book, fmt="epub"\|"fb2"\|"txt"\|"all", out?, chapters?` | `{"path", "chapters", "missing_total", ...}` |

`chapters` everywhere accepts `"all"`, `"1-10,15"`, a number or a list of numbers. Through `call()`/JSON-RPC
`translate` and `resume` always run in the background (`wait=false`).

## Events

Every event is a dict with `ev`, `book` (path), `t` (unix time) and its own fields:

| `ev` | fields | when |
|---|---|---|
| `prepare_start` / `prepare_done` | `report` (glossary, bible, gender conflicts) | a new book: finding names and writing the bible before the first translation |
| `job_start` | `total, resumed` | translation of a set of chapters starts |
| `extract_start` / `extract_done` | `chapters` / `added, pending` | looking for new names before each wave (25 chapters) |
| `chapter_start` | `n, segments` | a chapter is taken |
| `resume` | `n, done, total, stage` | a chapter continues from where it stopped |
| `batch_done` | `n, batch, batches` | a batch of the chapter is ready (for a progress bar inside the chapter) |
| `repair` | `n, seg, why` | the code found a problem in a paragraph and translates it again |
| `chapter_done` | `n, report` (`complete, segments, hard_issues, review_fixes, seconds`) | the chapter is done and written |
| `chapter_cancelled` | `n, done, total` | stopped; `done` paragraphs saved |
| `job_done` | `report` (`translated, failed, cancelled, HALTED, tokens, export`) | the job ended (including a stop) |
| `job_error` | `error` | the job failed with an exception |
| `log` | `msg` | a service message (provider unavailable, STOP received…) |

`HALTED` in `job_done`: the provider failed several times in a row and the job stopped itself so as not to burn
the book on errors — check the key (`check`) and `resume`.

## Config

The same as the CLI's (`polytome.json`), but it can be passed as a dict:

```json
{
  "profile": "economy",
  "providers": {"openrouter": {"api_key": "sk-..."},
                "my-server": {"base_url": "http://192.168.1.10:11434/v1", "local": true}},
  "slots": {"strong": "openrouter:<model>", "fast": "openrouter:<model>", "local": "my-server:qwen3:14b"},
  "parallel": 6,
  "target_lang": "en",
  "style": {"numbers": "auto"},
  "style_text": "your own house rules for translation (otherwise the built-in styles/<language>.md)"
}
```
Slots: `strong` (main translator), `fast` (cheap), `reviewer`, `extractor`, `agent`, `local`. Which slot serves
which role is decided by the profile (see `profiles`). `style.numbers`: `digits` | `natural` | `auto` (Russian —
digits, other languages — as usual in that language).

## Languages

Translation works from any language to any language: the source language is detected from the text (or
`source_lang`), the reading language is the book's `target_lang` (default — from the config, otherwise the system
language). A language picker in an app — the `languages` method. API messages (`error.message`) follow the system
language (English or Russian); an app should rather show its own texts by `error.type`. Force it with the
`POLYTOME_UI_LANG=en|ru` environment variable.

## Working together with the CLI and Hermes

The book folder is shared: a book started in an app can be continued with `polytome continue <book>` and vice
versa. Only one process translates a book at a time: `translate` returns an error if another one (CLI, Hermes,
another instance) is working on it. `cancel` also stops another process via the `.polytome/STOP` file.
