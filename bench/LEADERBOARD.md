# Polytome model leaderboard / Таблица моделей

Suite / набор: `1-f97c7303`. Score = average of the paragraphs clean right after translation and after the code-guided repair (0–100); checks are deterministic (names, gender, numbers, leftovers, omissions). Median over runs. Add your run: see [README.md](README.md).

Score — среднее долей абзацев без серьёзных замечаний сразу после перевода и после починки (0–100); проверки — кодом (имена, род, числа, остатки оригинала, пропуски). Медиана по прогонам.

**Limits / Ограничения.** The checks catch errors code can see — not style or a wrong meaning; strong models reach 100, and at the top they differ in speed and tokens. Every result file contains the translation: read it. / Проверки ловят ошибки, видимые коду, — не стиль и не искажённый смысл; сильные модели получают 100, и наверху они различаются скоростью и токенами. В каждом файле результата есть сам перевод — его стоит прочитать.

## zh-ru

| Model | Runs | Score | First pass | After repair | Sec / 1k chars | Tokens / 1k chars | $ / 1M chars | Last run |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `ollama-cloud:beam` | 1 | 100.0 | 100% | 100% | 29.8 | 10423 | — | 2026-10-24 |
| `ollama-cloud:deepseek-v4-pro:0813` | 1 | 100.0 | 100% | 100% | 76.3 | 9653 | — | 2026-10-24 |
| `ollama-cloud:deepseek-v4.1-flash:1017` | 1 | 100.0 | 100% | 100% | 13.2 | 6887 | — | 2026-10-24 |
| `ollama-cloud:gemma4:31b` | 1 | 100.0 | 100% | 100% | 5.8 | 4886 | — | 2026-10-24 |
| `ollama-cloud:glm-5.2` | 1 | 100.0 | 100% | 100% | 13.6 | 6449 | — | 2026-10-24 |
| `ollama-cloud:glm-5.3-flash` | 1 | 100.0 | 100% | 100% | 38.0 | 16632 | — | 2026-10-24 |
| `ollama-cloud:glm-5.3` | 1 | 100.0 | 100% | 100% | 119.0 | 20872 | — | 2026-10-24 |
| `ollama-cloud:kimi-k2.6` | 1 | 100.0 | 100% | 100% | 1104.2 | 37666 | — | 2026-10-24 |
| `ollama-cloud:kimi-k3` | 1 | 100.0 | 100% | 100% | 61.7 | 11485 | — | 2026-10-24 |
| `ollama-cloud:minimax-m3.1-flash` | 1 | 100.0 | 100% | 100% | 106.1 | 16529 | — | 2026-10-24 |
| `ollama-cloud:minimax-m3` | 1 | 100.0 | 100% | 100% | 159.7 | 22596 | — | 2026-10-24 |
| `ollama-cloud:mistral-large-4` | 1 | 100.0 | 100% | 100% | 537.8 | 59228 | — | 2026-10-24 |
| `ollama-cloud:muse-spark-open` | 1 | 100.0 | 100% | 100% | 40.3 | 9824 | — | 2026-10-24 |
| `ollama-cloud:deepseek-v4.1-flash` | 1 | 97.2 | 94% | 100% | 11.9 | 8151 | — | 2026-10-24 |
| `ollama-cloud:kimi-k2.7-code` | 1 | 97.2 | 94% | 100% | 113.4 | 26354 | — | 2026-10-24 |
| `ollama-cloud:gpt-oss:20b` | 1 | 94.5 | 89% | 100% | 76.1 | 20885 | — | 2026-10-24 |
| `ollama-cloud:nemotron-3-ultra` | 1 | 94.5 | 89% | 100% | 73.6 | 12088 | — | 2026-10-24 |
| `ollama-cloud:gpt-oss:120b` | 1 | 91.7 | 89% | 94% | 13.6 | 10897 | — | 2026-10-24 |
| `ollama-cloud:nemotron-3-super` | 1 | 91.7 | 89% | 94% | 37.6 | 12093 | — | 2026-10-24 |
| `ollama-cloud:minimax-m2.7` | 1 | 88.9 | 78% | 100% | 322.6 | 44529 | — | 2026-10-24 |
| `ollama-cloud:mistral-large-3:675b` | 1 | 83.3 | 78% | 89% | 27.5 | 8413 | — | 2026-10-24 |

Did not fit the token budget (100,000 per run) / Не уложились в лимит токенов: `ollama-cloud:nemotron-3-nano:30b`

## zh-en

| Model | Runs | Score | First pass | After repair | Sec / 1k chars | Tokens / 1k chars | $ / 1M chars | Last run |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `ollama-cloud:beam` | 1 | 100.0 | 100% | 100% | 13.9 | 6856 | — | 2026-10-24 |
| `ollama-cloud:deepseek-v4-pro:0813` | 1 | 100.0 | 100% | 100% | 25.4 | 7801 | — | 2026-10-24 |
| `ollama-cloud:deepseek-v4.1-flash:1017` | 1 | 100.0 | 100% | 100% | 9.2 | 5191 | — | 2026-10-24 |
| `ollama-cloud:deepseek-v4.1-flash` | 1 | 100.0 | 100% | 100% | 7.6 | 4625 | — | 2026-10-24 |
| `ollama-cloud:gemma4:31b` | 1 | 100.0 | 100% | 100% | 4.7 | 3875 | — | 2026-10-24 |
| `ollama-cloud:glm-5.2` | 1 | 100.0 | 100% | 100% | 6.3 | 3744 | — | 2026-10-24 |
| `ollama-cloud:glm-5.3-flash` | 1 | 100.0 | 100% | 100% | 19.7 | 9617 | — | 2026-10-24 |
| `ollama-cloud:glm-5.3` | 1 | 100.0 | 100% | 100% | 45.7 | 14485 | — | 2026-10-24 |
| `ollama-cloud:gpt-oss:120b` | 1 | 100.0 | 100% | 100% | 8.6 | 6145 | — | 2026-10-24 |
| `ollama-cloud:gpt-oss:20b` | 1 | 100.0 | 100% | 100% | 20.7 | 6278 | — | 2026-10-24 |
| `ollama-cloud:kimi-k2.6` | 1 | 100.0 | 100% | 100% | 742.3 | 23498 | — | 2026-10-24 |
| `ollama-cloud:kimi-k2.7-code` | 1 | 100.0 | 100% | 100% | 18.2 | 5779 | — | 2026-10-24 |
| `ollama-cloud:kimi-k3` | 1 | 100.0 | 100% | 100% | 59.2 | 8603 | — | 2026-10-24 |
| `ollama-cloud:minimax-m2.7` | 1 | 100.0 | 100% | 100% | 81.8 | 8192 | — | 2026-10-24 |
| `ollama-cloud:minimax-m3.1-flash` | 1 | 100.0 | 100% | 100% | 58.9 | 9566 | — | 2026-10-24 |
| `ollama-cloud:minimax-m3` | 1 | 100.0 | 100% | 100% | 36.8 | 6216 | — | 2026-10-24 |
| `ollama-cloud:mistral-large-4` | 1 | 100.0 | 100% | 100% | 254.6 | 36220 | — | 2026-10-24 |
| `ollama-cloud:muse-spark-open` | 1 | 100.0 | 100% | 100% | 34.1 | 8244 | — | 2026-10-24 |
| `ollama-cloud:nemotron-3-ultra` | 1 | 100.0 | 100% | 100% | 80.6 | 6643 | — | 2026-10-24 |
| `ollama-cloud:mistral-large-3:675b` | 1 | 94.4 | 94% | 94% | 14.0 | 4010 | — | 2026-10-24 |
| `ollama-cloud:nemotron-3-super` | 1 | 94.4 | 94% | 94% | 35.2 | 8639 | — | 2026-10-24 |

Did not fit the token budget (100,000 per run) / Не уложились в лимит токенов: `ollama-cloud:nemotron-3-nano:30b`

## en-ru

| Model | Runs | Score | First pass | After repair | Sec / 1k chars | Tokens / 1k chars | $ / 1M chars | Last run |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `ollama-cloud:gemma4:31b` | 1 | 100.0 | 100% | 100% | 2.1 | 1791 | — | 2026-10-24 |
| `ollama-cloud:gpt-oss:120b` | 1 | 100.0 | 100% | 100% | 4.0 | 3066 | — | 2026-10-24 |
| `ollama-cloud:kimi-k2.7-code` | 1 | 100.0 | 100% | 100% | 23.7 | 5312 | — | 2026-10-24 |
| `ollama-cloud:kimi-k3` | 1 | 100.0 | 100% | 100% | 21.7 | 3992 | — | 2026-10-24 |
| `ollama-cloud:muse-spark-open` | 1 | 100.0 | 100% | 100% | 15.8 | 3844 | — | 2026-10-24 |
| `ollama-cloud:beam` | 1 | 96.7 | 93% | 100% | 13.9 | 5092 | — | 2026-10-24 |
| `ollama-cloud:deepseek-v4.1-flash` | 1 | 96.7 | 93% | 100% | 4.4 | 3292 | — | 2026-10-24 |
| `ollama-cloud:glm-5.2` | 1 | 96.7 | 93% | 100% | 5.0 | 3086 | — | 2026-10-24 |
| `ollama-cloud:glm-5.3-flash` | 1 | 96.7 | 93% | 100% | 6.5 | 4255 | — | 2026-10-24 |
| `ollama-cloud:glm-5.3` | 1 | 96.7 | 93% | 100% | 49.4 | 8091 | — | 2026-10-24 |
| `ollama-cloud:mistral-large-4` | 1 | 96.7 | 93% | 100% | 52.5 | 8334 | — | 2026-10-24 |
| `ollama-cloud:kimi-k2.6` | 1 | 93.3 | 87% | 100% | 370.4 | 15243 | — | 2026-10-24 |
| `ollama-cloud:minimax-m3` | 1 | 93.3 | 87% | 100% | 42.4 | 7187 | — | 2026-10-24 |
| `ollama-cloud:deepseek-v4-pro:0813` | 1 | 90.0 | 80% | 100% | 16.7 | 5778 | — | 2026-10-24 |
| `ollama-cloud:deepseek-v4.1-flash:1017` | 1 | 90.0 | 80% | 100% | 4.9 | 4603 | — | 2026-10-24 |
| `ollama-cloud:gpt-oss:20b` | 1 | 90.0 | 80% | 100% | 23.1 | 7193 | — | 2026-10-24 |
| `ollama-cloud:minimax-m3.1-flash` | 1 | 90.0 | 80% | 100% | 65.0 | 10301 | — | 2026-10-24 |
| `ollama-cloud:nemotron-3-super` | 1 | 90.0 | 80% | 100% | 15.7 | 5589 | — | 2026-10-24 |
| `ollama-cloud:nemotron-3-ultra` | 1 | 90.0 | 80% | 100% | 48.5 | 5249 | — | 2026-10-24 |
| `ollama-cloud:minimax-m2.7` | 1 | 86.7 | 73% | 100% | 140.8 | 17076 | — | 2026-10-24 |
| `ollama-cloud:mistral-large-3:675b` | 1 | 80.0 | 67% | 93% | 12.7 | 6226 | — | 2026-10-24 |

Did not fit the token budget (100,000 per run) / Не уложились в лимит токенов: `ollama-cloud:nemotron-3-nano:30b`

