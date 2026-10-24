# Polytome — an LLM book translator that checks its own work

**English** · [Русский](README.ru.md)

Polytome translates whole books (EPUB, TXT, folders of chapters) **from any language into any language** and
watches the quality itself: names, character gender, numbers, omissions and leftovers of the original are checked
by **code**, not by the model. It works with any model — in the cloud or on your own computer.

It started as a translator of Chinese web novels, so that pair is the most polished: Chinese names are
transcribed deterministically (Hanyu Pinyin for Latin-script languages, Palladius for Russian), and character
genders are verified against evidence in the original.

Three ways to use it:
- **Ready-made commands** — predictable and cheap: `polytome new` → `polytome run` → `polytome export`.
- **Chat with an agent** (like Claude Code): "in chapter 7 the heroine speaks about herself in the masculine — fix it".
- **Inside an app** — a Python API, Android (Chaquopy) or JSON-RPC: [docs/INTEGRATION.md](docs/INTEGRATION.md).

## Quick start

```
install.cmd                                      # once (Windows): environment and the polytome command
polytome setup                                   # provider → key → models → profile
polytome new "D:\Downloads\book.epub" --to en    # create a book (chapters, cover, author, token estimate)
polytome run "D:\Downloads\book" --export epub   # translate and build an EPUB
```
On Linux/macOS: `python -m venv .venv && .venv/bin/pip install -e .`, then the same commands.
All scenarios with examples: **[docs/SCENARIOS.md](docs/SCENARIOS.md)** (a long novel in the background, resuming
after a crash, saving tokens, local models, checking someone else's translation, audiobooks, Hermes).

## Languages

```
polytome languages                         # supported languages and which checks exist for a pair
polytome new book.epub --to es             # reading language; the source language is detected
polytome setup --to en                     # default reading language for new books
```
| check | pairs |
|---|---|
| omissions, truncated paragraphs, numbers, duplicated/shifted paragraphs, glossary contract | all |
| leftovers of the original script (hanzi, kana, hangul, Cyrillic…) | scripts differ |
| Chinese names: deterministic transcription and misspelled-variant detection | Chinese → Latin-script languages (pinyin), Chinese → Russian (Palladius) |
| character gender from evidence in the original (他/她, kinship) | Chinese → any |
| grammatical gender of characters, "numbers as digits" | → Russian (morphology) |

Any other ISO 639-1 code works too — the model gets it as the language name. Prompts for Russian output are tuned
and measured; for every other target Polytome uses English prompts with language-specific rules (pinyin and
xianxia terms for Chinese → English, Hepburn for Japanese, dialogue punctuation of the target language…).

## Commands

| command | what it does |
|---|---|
| `polytome setup` / `polytome doctor` | configure providers and models / check connection and keys |
| `polytome new <epub\|txt\|folder> [--to en] [--from zh]` | create a book |
| `polytome run <book> [-c 1-50] [--profile economy] [--background] [--export epub]` | translate |
| `polytome status <book> [--json\|--watch]` | progress, ETA, tokens (no LLM) |
| `polytome stop` / `polytome continue <book>` | stop gently / continue after a crash or a stop |
| `polytome estimate <book>` | tokens and time for every profile |
| `polytome audit <book> [--fix]` | check a translation (own or someone else's) without LLM; repair what was found |
| `polytome export <book> --format epub\|fb2\|txt\|all` | build the book |
| `polytome profiles [--set economy [--book <book>]]` | quality/economy profiles |
| `polytome models [provider]` / `polytome languages` | available models / languages |
| `polytome bench -m <provider:model> [-p zh-ru]` | score a model on the shared test texts ([leaderboard](bench/LEADERBOARD.md)) |
| `polytome <book>` / `polytome ask <book> "task"` | chat with the agent / a one-sentence task |
| `polytome integrate hermes` | install the Hermes skill |
| `polytome mcp <book>` / `polytome serve` | MCP server for agents / JSON-RPC API for apps |

## Profiles: quality or economy

| profile | translator | reviewer | for |
|---|---|---|---|
| `quality` | strong model with full reasoning | whole chapter | maximum quality, tokens don't matter |
| `balanced` | strong model without long reasoning | whole chapter | default |
| `economy` | fast model, large batches | only where issues were found | fewer tokens, longer wait |
| `local` | a model on your computer | where issues were found | free and private |

The profile is set globally (`polytome profiles --set`), per book (`--book`) or for one run (`--profile`).

## Models and providers

Out of the box: Ollama Cloud, Ollama and LM Studio (local), llama.cpp, vLLM, OpenRouter, OpenAI, Anthropic,
Google Gemini, DeepSeek, Mistral, Groq, Together, AITunnel. Any other OpenAI-compatible server — one line in
`polytome.json`.

Models are arranged in **slots**: `strong` (main translator), `fast` (cheap), `agent` (chat), `reviewer`,
`extractor`, `local`. `polytome setup` suggests options; without questions:
```
polytome setup --provider openrouter --key sk-... --strong <model> --fast <model> --profile economy --to en
```
Keys are stored in `secrets.json` (or environment variables), not in `polytome.json`. A model without native
tool-calling works too — the agent switches to a text protocol by itself.

Which model to choose: `polytome bench` scores models on the same test texts with the code checks (no model judges
another), with speed and price; everyone's results are collected in [bench/LEADERBOARD.md](bench/LEADERBOARD.md) —
add yours, see [bench/README.md](bench/README.md).

## Reliability

- Progress is saved after every batch with atomic writes: a power cut does not corrupt files, `continue` resumes
  where it stopped. Tested by killing the process in the middle of a translation.
- Long novels are translated in waves of 25 chapters: the glossary and the bible grow with the book.
- If the provider is down, the job stops itself instead of burning the book on errors.
- Paragraphs broken mid-sentence (a common problem of downloaded texts) are joined on import; shifted and
  duplicated paragraphs are caught by the chapter check. Tested on a 100-chapter novel (JOURNAL.md).

## Embedding in apps

```python
from polytome import Polytome
h = Polytome({"providers": {"openrouter": {"api_key": "sk-..."}}, "slots": {"strong": "openrouter:<model>"},
              "target_lang": "en"})
book = h.import_book("book.epub")["path"]
h.translate(book, chapters="1-5", on_event=print)
h.chapter(book, 1)          # {"source": [...], "translation": [...]} — paragraphs 1:1
```
The same for any language: `polytome serve` (JSON-RPC over stdin/stdout) or `call(method, json)` from Kotlin via
Chaquopy. Methods, events, an Android example — [docs/INTEGRATION.md](docs/INTEGRATION.md).

## Hermes

```
polytome integrate hermes
```
Copies the `polytome-translator` skill into Hermes and sets the path to Polytome. Then in Hermes: "translate the
book D:\book.epub into English". Hermes starts the translation in the background, watches the status (within the
300 s cell limit), continues after a crash and builds the EPUB.

## Interface language

CLI and API messages follow the system language: Russian on a Russian system, English otherwise. Force it with
`POLYTOME_UI_LANG=en` or `ru`. Strings live in `polytome/locales/en.json`; `python tools/i18n_wrap.py check`
shows untranslated ones.

## Documentation

- [docs/SCENARIOS.md](docs/SCENARIOS.md) — recipes for every case;
- [docs/INTEGRATION.md](docs/INTEGRATION.md) — API for apps (Python, Android, JSON-RPC);
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how it works;
- [docs/PROTOCOL.md](docs/PROTOCOL.md) — agent/MCP tools;
- [JOURNAL.md](JOURNAL.md) — development log and measurements (in Russian).

## License

GPL-3.0-or-later — see [LICENSE](LICENSE). Former names of the project: Codek, then hanru.
