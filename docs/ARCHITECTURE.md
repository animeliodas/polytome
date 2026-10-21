# Polytome — architecture

**English** · [Русский](ARCHITECTURE.ru.md)

An agentic translator in the spirit of Claude Code: a REPL where an LLM agent calls tools itself. The difference:
the tools are not about code but about translating a book.

## The main idea

Division of labour between three kinds of workers:

| Who | What it does | Why |
|---|---|---|
| **Director agent** (a strong model with tool-calling) | reads the book, keeps the "bible" and the glossary, starts translation, reads check reports, makes targeted fixes | reasoning and decisions are expensive but take few tokens |
| **Engine** (deterministic code + worker models) | splits a chapter, translates in parallel batches, repairs omissions, runs the reviewer | mass text never goes through the agent's context → no truncation, cheap, stable |
| **Deterministic tools** (no LLM) | name transcription, glossary contract, leftovers/truncation detection, search in the book | where LLMs make mistakes and code does not |

The agent never prints a chapter translation itself. It calls `translate`, gets a report ("ch. 5: 48 paragraphs,
2 glossary violations, 2 fixed automatically") and decides what to do next.

## Layers

```
cli.py        subcommands (new/run/status/stop/continue/audit/export/estimate/setup/doctor/languages/integrate/
              mcp/serve) + the agent REPL with slash commands
agent.py      agent loop: messages ↔ model ↔ tools, folding old results, compaction
tools.py      PROTOCOL: tool registry with JSON schemas (agent, MCP, Hermes — one set)
engine.py     translation: waves → batches → seam → repair → reviewer (full/flagged/off) → per-batch checkpoints;
              book preparation without the agent (prepare/draft_bible/refresh_bible), estimates, job journal
checks.py     deterministic QA: leftovers, truncation, names (grounded in the original), gender (morphology +
              evidence in the original), numbers as digits, punctuation — switched on per language pair
lang.py       languages and pairs: script, transcription system, which checks apply, system language
prompting.py  prompts for a pair: Russian (prompts.py, tuned on zh→ru) or English (prompts_en.py) + pair rules
i18n.py       interface language (ru/en from the system), _() and the locales/en.json catalog
palladius.py  pinyin → Cyrillic (Palladius) and toneless pinyin for Latin-script targets
formats.py    EPUB/TXT/folder → book (source language detection); book → EPUB 3 / FB2 / TXT in the target language
jobs.py       background jobs: a separate process, status from job.json, stop via the STOP flag
glossary.py   the book glossary: terms, gender, kind, stems for inflection
align.py      alignment of someone else's translation (Gale–Church, beads)
workspace.py  the book folder: original, translation, .polytome/; atomic writes; chapter cache
llm.py        client for OpenAI-compatible APIs, AIMD pool, retries, a guard against "endless reasoning"
config.py     provider catalog, model slots, profiles (quality/balanced/economy/local), secrets, target language
mcp_server.py MCP (stdio) without dependencies
api.py        API for apps: the Polytome class (dicts in/out, events, background, cancel) + call(json)
server.py     JSON-RPC 2.0 over stdin/stdout on top of api.py (`polytome serve`)
integrations/hermes/polytome-translator — a ready skill for Hermes (inside the package)
styles/       house rules per target language (ru.md, default.md); overridden by STYLE.md / STYLE.<lang>.md next
              to the config or by style_text
locales/      interface strings (en.json; the key is the Russian text). tools/i18n_wrap.py wraps and checks them
```

## Languages

A book stores `source_lang` and `target_lang` (`.polytome/book.json`). They give a `Pair` (`lang.py`) that decides:
- **prompts**: Russian output — Russian prompts (tuned on Chinese → Russian; for zh→ru they are word for word the
  previous ones, verified by a snapshot); other languages — English prompts with pair rules;
- **name transcription**: Chinese → Russian — Palladius, Chinese → Latin script — pinyin, otherwise the model;
- **checks**: leftovers of the original script — when scripts differ; paragraph length — for CJK → alphabetic;
  gender morphology and "numbers as digits" — for Russian; name variants — by the transcription system;
  omissions, numbers, duplicates and the glossary — for every pair.

CLI/API messages use the interface language (`i18n.py`); texts for the model use the prompt language of the pair.

## Configuration: slots and profiles

```
slots (which models you have)        profile (how carefully to work)
strong   ollama-cloud:kimi-k3        translator ← strong@low | strong | fast | local
fast     ollama-cloud:deepseek…      reviewer   ← reviewer | fast | local; mode full | flagged
agent    ollama-cloud:glm-5.3        batches, repair attempts, parallelism
reviewer / extractor / local
```
A role = (slot, reasoning_effort) from the profile; explicit `roles` in the config override the profile; a book
can set its own profile in `.polytome/book.json`; an explicit `--profile` for a run beats the book's profile.

## Model roles

- `agent` — the director (needs good tool-calling).
- `translator` — does the translation (can be a list → round-robin).
- `reviewer` — reviewer (better a different model than the translator: errors do not correlate).
- `extractor` — a cheap model for extracting names/terms.

A model is written as `provider:model` (`ollama-cloud:gemma4:31b` — split at the first `:`).

## Tool-calling protocol

- `native` — OpenAI `tools` / `tool_calls`.
- `text` — for models without native tools: the model writes `<tool_call>{"name": ..., "arguments": {...}}</tool_call>`,
  the result comes back as `<tool_result>`.
- `auto` — native; on an API error or a text call inside content — switch to text.

## The book folder (workspace)

```
<book>/
  Original/Chapter N.txt    (folder name is configurable: source_dir; older books: Оригинал/Глава N.txt)
  Translation/Chapter N.txt (target_dir)
  .polytome/
    book.json              book settings (languages, folders, chapter file pattern, model overrides)
    bible.md               the book "bible": style, characters, rules (like CLAUDE.md)
    glossary.json          terms: source, target, kind, gender, note, status
    state/ch00001.json     per-segment state: original, translation, flags, edit history
    sessions/              agent chat history
    job.json               job journal: chapters, done/errors, pid, tokens, ETA (for status/continue)
    logs/                  logs of background runs
    scanned.json           chapters already scanned for new names
    source_map.json        table of contents of the source EPUB/TXT
```
Books created under the old project names (`.codek`, `.hanru`) are migrated to `.polytome` when opened.
