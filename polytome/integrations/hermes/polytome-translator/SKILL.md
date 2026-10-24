---
name: polytome-translator
description: "Translate whole books with the Polytome CLI from any language into any language (EPUB/TXT/folder of chapters; Chinese web novels and more): new book, background translation, status, resume after a crash, check/repair an existing translation, build EPUB/FB2/TXT. Use for translating books."
version: 0.5.0
author: animeliodas
license: GPL-3.0-or-later
metadata:
  hermes:
    tags: [translation, books, epub, fb2, novel, multilingual, chinese, japanese, korean, audiobook]
    category: productivity
    config:
      - key: polytome.command
        description: "Command that runs Polytome: path to polytome.exe or just polytome if it is on PATH"
        default: "polytome"
      - key: polytome.library
        description: "Library folder where Polytome creates books from EPUB/TXT"
        default: "~/Books/Polytome"
---

# Polytome — book translation

Polytome is a command-line book translator: it prepares the book's glossary and "bible", translates, checks names,
character gender, numbers and omissions with deterministic code, repairs errors and builds EPUB/FB2. It works for
any language pair. You **drive** it — do NOT translate the book text yourself and do not send it to LLM providers
directly. Talk to the user in their language.

Below `POLYTOME` = the value of `polytome.command` from the skill config (default `polytome`), `LIB` =
`polytome.library`. Always quote paths (spaces, non-Latin characters).

## When to use
- "translate this book/EPUB/novel (into English/Spanish/…)", "continue the translation", "how is the translation
  going?", "build an EPUB/FB2", "check the translation for errors", "translate cheaper", "change the translator model".

## The main rule: long work only in the background
An `execute_code` cell lives 300 s, a book translation takes minutes to hours. So:
1. start — `POLYTOME run "<book>" --background` (returns immediately, prints the pid and the log);
2. wait — `python ${HERMES_SKILL_DIR}/scripts/polytome_wait.py "<book>" --cmd "POLYTOME" --max 240`
   (exits by itself before 300 s; exit code 0 — the job ended, 3 — still running: call it again);
3. never run `POLYTOME run` without `--background` inside execute_code/terminal;
4. do NOT check the process with tasklist/ps and do NOT read the log by hand: `POLYTOME status "<book>" --json`
   already has `job.state` (starting → running → done/cancelled/failed/interrupted), `job.phase`
   (prepare/translate), `job.alive`, progress, ETA and `log_tail`. While `polytome_wait.py` returns 3, just call it again.

## Quick reference
| task | command |
|---|---|
| new book from a file | `POLYTOME new "<file.epub/.txt or folder>" --to <lang> -L "LIB"` |
| supported languages | `POLYTOME languages` |
| translate everything (background) | `POLYTOME run "<book>" --background --export epub` |
| translate a part | `POLYTOME run "<book>" -c 1-20 --background` |
| status (JSON, no tokens) | `POLYTOME status "<book>" --json` |
| stop | `POLYTOME stop "<book>"` |
| continue after a crash/stop | `POLYTOME continue "<book>" --background` |
| token/time estimate | `POLYTOME estimate "<book>"` |
| check without LLM / repair | `POLYTOME audit "<book>"` / `POLYTOME audit "<book>" --fix` |
| build the book | `POLYTOME export "<book>" --format epub` (`fb2`, `txt`, `all`) |
| economy profile | `POLYTOME profiles --set economy --book "<book>"` |
| models / connection check | `POLYTOME models` / `POLYTOME doctor` |
| targeted fix by the Polytome agent | `POLYTOME ask "<book>" "<task>"` |
| remove junk (site ads, author notes/thanks) | `POLYTOME clean "<book>"` → `--apply` |
| character gender in a finished translation | `POLYTOME cast "<book>" --suggest` / `--set "Name=f:who"` → `POLYTOME edit "<book>" --genders --background` |

`--to` is a language code (en, ru, es, fr, de, ja, ko, zh, pt, it, …). If the user does not say which language to
translate into, use the language the user writes to you in.

## Procedures

### A. Translate a book from a file
1. `POLYTOME new "<file>" --to <lang> -L "LIB"` → the output has the book path, chapter count, languages
   (`zh → en`) and a token estimate. If it warns that the text is already in the target language — offer
   `audit`/`export` instead.
2. If the book is big (>50 chapters) — show the user the estimate (`POLYTOME estimate`) and ask for a profile if
   they did not say (quality / balanced / economy / local).
3. `POLYTOME run "<book>" --background --export epub` (+ `--profile economy` if they asked for cheaper).
4. Wait in `polytome_wait.py` cycles (see above). Between cycles report progress briefly: "done 40/120, ETA ~25 min".
5. When done: the EPUB path is in the log tail (`status --json` → `log_tail`), or build it: `POLYTOME export`.
6. Verify: `status --json` → `chapters_translated == chapters_total`; if not — `POLYTOME continue … --background`.

### B. "Continue the translation" / after a crash
`POLYTOME status "<book>" --json`: `job.state` = `interrupted` (the process died — computer turned off),
`cancelled` (stopped) or `failed` (provider errors; see `halt_reason`). In every case:
`POLYTOME continue "<book>" --background`. Finished chapters are not retranslated, started ones continue where
they stopped. On `failed` run `POLYTOME doctor` first (key/provider), then continue.

### C. Check/repair an existing translation (own, another tool, a website)
The book folder: `Original/Chapter N.txt` + `Translation/Chapter N.txt` (older books: `Оригинал/Глава N.txt` +
`Перевод/Глава N.txt`). `POLYTOME audit "<book>"` (no LLM) → show the user the summary (chapters with hard
errors, wrong names, gender conflicts). Repair only with their consent: `POLYTOME audit "<book>" --fix` (changes
only the paragraphs with found errors; before a mass repair suggest copying the translation folder).

### D. Models and providers
`POLYTOME setup --provider <name> --key <key> --strong <model> --fast <model> --profile <profile> --to <lang>`
(providers: ollama-cloud, ollama, lmstudio, openrouter, openai, anthropic, gemini, deepseek, mistral, groq,
together, aitunnel). The user types keys themselves — never print them in the chat. Check: `POLYTOME doctor`.

## Pitfalls
- Quote paths with spaces and non-Latin characters; the output is UTF-8.
- `new` warned "paragraphs are broken mid-sentence" (hard line breaks in a downloaded text) → suggest
  `POLYTOME new "<folder>" --fix-breaks` (joins them, with a backup of the original). From EPUB/TXT this is done
  automatically. On broken text the translator shifts paragraphs; `audit` catches it as "translations are the same".
- One book — one background job: a second `run` while a job is alive returns "already running".
- `status --json` never shows `job.alive=false` with `state=running`: such a job is shown as `interrupted`.
- `GENDER_CONFLICTS` warnings in the summary/status: a character's gender in the glossary contradicts the
  original — tell the user or resolve it with `POLYTOME ask "<book>" "check the gender of X against the original and fix it"`.
- Do not edit translation files by hand while a job is running.
- Polytome prints messages in the system language (English or Russian); `POLYTOME_UI_LANG=en` forces English.

## Checking the result
- `POLYTOME status "<book>" --json` → `chapters_translated`, `job.state == "done"`, `job.failed == []`.
- `POLYTOME audit "<book>"` → 0 hard errors (or explain the remaining ones).
- The EPUB/FB2 file exists and is not empty.

Detailed scenarios: `references/scenarios.md`.
