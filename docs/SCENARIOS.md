# Polytome scenarios

**English** · [Русский](SCENARIOS.ru.md)

Short recipes: "I have X — I want Y". Everywhere `<book>` is a book folder (created by `polytome new`).

## 0. First run (once)

```
install.cmd               # environment + the polytome command (Windows; elsewhere: pip install -e .)
polytome setup            # provider → key → models → profile (a wizard with suggestions)
polytome doctor           # check: keys, connection, a reply from every model
```
Without questions (scripts): `polytome setup --provider openrouter --key sk-... --strong <model> --fast <model> --profile economy --to en`.

## 1. I bought a book as EPUB → I want an EPUB in my language

```
polytome new "D:\Downloads\book.epub" --to en -L "D:\Books"
polytome run "D:\Books\book" --export epub
```
`--to` is the language you want to read in (default — from settings or the system language), `-L` is the library
folder. `new` splits the book by its table of contents, extracts the cover and author, detects the source language
and shows a token estimate. `run` prepares the glossary and the bible, translates, checks and builds the book.

## 1a. Any language → any language

```
polytome languages                               # languages and which checks exist for a pair
polytome new "D:\novel.epub" --to en             # Chinese → English (names in pinyin: Li Xiaobai)
polytome new "D:\light-novel.epub" --to es       # Japanese → Spanish
polytome new "D:\book.epub" --from en --to ru    # the source language can be given explicitly
polytome setup --to en                           # default target language for new books
```
Translation works for any pair. Deterministic checks switch on where they apply: Chinese names via Palladius
(Russian) or Hanyu Pinyin (Latin-script languages); character gender and "numbers as digits" via Russian
morphology; leftover characters of the original when the scripts differ; omissions, numbers, duplicates and the
glossary contract for every pair. To change the language of an existing book: `polytome new "<book folder>" --to
<code>` (while it has no translation yet).

## 2. Try a few chapters first

```
polytome run "<book>" -c 1-5
polytome export "<book>" --format epub -c 1-5      # read the sample
polytome run "<book>"                              # the rest (finished chapters are not retranslated)
```

## 3. A long novel (hundreds of chapters) — in the background

```
polytome estimate "<book>"                         # tokens and time per profile
polytome run "<book>" --background --export all    # returns immediately
polytome status "<book>"                           # done / left / ETA / tokens
polytome status "<book>" --watch                   # watch until it finishes
polytome stop "<book>"                             # stop gently
polytome continue "<book>" --background            # continue
```
Translation goes in waves of 25 chapters: before each wave new names are found, genders are verified and new
characters are added to the bible. If the provider goes down, the job stops itself after several errors in a row
instead of burning the whole book.

## 4. The computer turned off / the process was killed / Ctrl+C

```
polytome continue "<book>"
```
Progress is saved after every batch (a few paragraphs), writes are atomic. Finished chapters are not
retranslated; started ones continue from their stage (draft → repair → review).

## 5. Fewer tokens (I can wait longer)

```
polytome run "<book>" --profile economy
polytome profiles --set economy --book "<book>"    # pin it for the book
```
| profile | what it does | tokens* |
|---|---|---|
| quality | translator with full reasoning + reviewer on the whole chapter | ~×4 of balanced |
| balanced | strong translator + reviewer on the whole chapter (default) | 1 |
| economy | fast model, large batches, reviewer only where issues were found | ~0.4–0.5 |
| local | everything on your computer (Ollama/LM Studio), free and private, slow | free |

\* measured on Chinese → Russian (JOURNAL.md); the exact estimate for your book — `polytome estimate`.

## 6. Free, on your own computer

```
ollama pull qwen3:14b                            # or any model of ~14B or more
polytome setup --provider ollama --local qwen3:14b --profile local
polytome run "<book>"
```

## 7. Another provider / another model

```
polytome models                                      # models of all configured providers
polytome models openrouter
polytome setup --provider openrouter --key ... --strong <model> --reviewer <model>
```
In the REPL: `/model strong openrouter:<model>`, `/profile economy`. Any OpenAI-compatible server — in
`polytome.json` → `"providers": {"mine": {"base_url": "http://.../v1"}}`, the key — `polytome setup --provider mine --key ...`.

## 8. I already have a translation (another tool, a person, a website) — check and repair it

Put `Original/` and `Translation/` into one folder (files named "Chapter N.txt"):
```
polytome audit "<book>"            # no LLM: wrong/misspelled names, gender, numbers, leftovers, truncated paragraphs
polytome audit "<book>" --fix      # repair only what was found (correct paragraphs are not touched)
```
Paragraphs are aligned automatically even if the translator split or merged them; edits keep the file paragraph
for paragraph.

## 8a. A machine translation from the web: wrong genders, lots of junk — fix it up

No original needed. Put the translated chapters into `Translation/` (and a copy into `Original/` if there is no
original).
```
polytome clean "<book>"                       # preview: site boilerplate, author notes and thanks
polytome clean "<book>" --apply               # remove (with a backup)
polytome cast "<book>" --suggest              # characters and gender statistics from the text
polytome cast "<book>" --set "Lin Yao=f:the hero's younger sister" --set "Xia Fan=m"
polytome edit "<book>" --genders --background # fix gender across the book (status / stop / continue)
polytome export "<book>" --format epub
```
Machine translation defaults to the masculine, so the "majority" in the text lies. You set the characters'
genders (or take them from the novel's wiki); `--suggest` shows whom to check. The editor changes only gender:
an edit that changes more (similarity < 85%) is rejected. The automatic gender detector currently exists for
Russian; for other languages the LLM editor works on its own.

## 9. One big TXT or a folder of chapters

```
polytome new "D:\novel.txt"          # splits by 第N章 / Chapter N / Capítulo N / Kapitel N … headings
polytome new "D:\Books\My book"      # a folder with Original/Chapter N.txt — just attach it
polytome new "D:\Books\My book" --fix-breaks   # if it warns about broken paragraphs
```
Texts downloaded from websites are often broken mid-sentence (hard line breaks), which confuses the translator.
From EPUB/TXT Polytome joins the breaks itself; for a ready folder use `--fix-breaks` (with a backup of the
original).

## 10. Audiobook / text-to-speech

House rules live in `polytome/styles/<language>.md` (`default.md` for languages without their own file). Your own
rules: `STYLE.md` or `STYLE.<language>.md` next to `polytome.json`. Build: `polytome export "<book>" --format fb2`
or `--format epub`.

## 11. Fine work with the agent (like Claude Code)

```
polytome "<book>"                                   # chat
polytome ask "<book>" "in chapter 7 the heroine speaks about herself in the masculine — find and fix it"
polytome ask "<book>" "rename Fatty to Chubby in all chapters"
```

## 12. From Hermes / Claude Code / an app

- Hermes: `polytome integrate hermes` installs the `polytome-translator` skill; then just say "translate the
  book D:\book.epub into English" — Hermes starts the translation in the background and watches its status.
- MCP (any client): `polytome mcp "<book>"` — 30+ tools; long jobs via `job_start` / `job_status` / `job_stop`
  (an MCP call is limited to ~120 s, so translation runs in the background).
- Apps: `from polytome import Polytome` (Python), JSON-RPC `polytome serve`, Android via Chaquopy —
  [INTEGRATION.md](INTEGRATION.md).

## Interface language

CLI messages follow the system language (English or Russian). Force it with `POLYTOME_UI_LANG=en` or `ru`.
