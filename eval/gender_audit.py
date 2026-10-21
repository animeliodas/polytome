"""Аудит рода персонажей в переводе: модели-аудиторы ищут ошибки пола (глаголы/прилагательные/местоимения).

python eval/gender_audit.py <workspace> <главы> <аудитор1,аудитор2> <метка>=<шаблон пути с {n}> ...
Пример: ... books/flower 1-5 ollama-cloud:kimi-k3,ollama-cloud:glm-5.3 hanru=books/flower/Перевод/Глава {n}.txt
Ошибка засчитывается, если её нашли ОБА аудитора (пересечение по номеру абзаца) — меньше ложных.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from hanru.align import align  # noqa: E402
from hanru.config import Config  # noqa: E402
from hanru.engine import parse_json_lenient  # noqa: E402
from hanru.llm import LLMClient  # noqa: E402
from hanru.workspace import Workspace, parse_range, split_paragraphs  # noqa: E402

CAST = """Пол персонажей (проверено по оригиналу):
- Су Янь (苏言, Янь Эр 言儿) — МУЖЧИНА, юноша 19 лет с женственной внешностью. Всё о нём — в мужском роде.
- Шэнь Син (沈星, директор Шэнь) — ЖЕНЩИНА.
- Сюй Бин (许冰, «божественный врач») — ЖЕНЩИНА.
- Сюй Чунь (许纯, Сяо Чунь 小纯) — МУЖЧИНА, младший брат Сюй Бин (许冰的弟弟).
- дядя Линь (林叔) — МУЖЧИНА. Секретарь Шэнь Син — ЖЕНЩИНА.
Мир — «женское превосходство»: властные роли у женщин; это не повод менять пол."""

SYS = """Ты проверяешь ТОЛЬКО род/пол персонажей в русском переводе китайского романа.
{cast}
Ошибка: глагол прошедшего времени, прилагательное, причастие или местоимение, относящееся к персонажу, стоит
не в том роде (например «Су Янь улыбнулась», «Шэнь Син был недоволен», «юноша… она»), либо персонаж назван
словом не того пола («девушка» о Су Яне). Сверяйся с оригиналом (他/她). Прочее (стиль, смысл) не оценивай.
Ответ JSON: {{"errors": [{{"id": <номер абзаца>, "quote": "<фрагмент>", "who": "<персонаж>"}}]}}. Нет ошибок — пустой список."""


def main():
    ws_path, rng, auditors = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
    variants = dict(a.split("=", 1) for a in sys.argv[4:])
    ws = Workspace(ws_path)
    chapters = parse_range(rng, list(ws.chapters()))
    cfg, llm = Config.load(), LLMClient(12)
    jobs = []
    for label, pattern in variants.items():
        for n in chapters:
            src = ws.source_paragraphs(n)
            tr = align(src, split_paragraphs(Path(pattern.format(n=n)).read_text(encoding="utf-8-sig")))
            for start in range(0, len(src), 25):
                ids = range(start, min(len(src), start + 25))
                body = "\n".join(f"[{i + 1}] ОРИГ: {src[i]}\n[{i + 1}] ПЕР: {tr[i]}" for i in ids)
                for a in auditors:
                    jobs.append((label, n, a, body))

    def run(job):
        label, n, a, body = job
        try:
            res = llm.chat(cfg.resolve(a), [{"role": "system", "content": SYS.format(cast=CAST)},
                                            {"role": "user", "content": body}],
                           temperature=0.1, json_mode=True, max_tokens=12000, role="audit")
            errs = (parse_json_lenient(res.content) or {}).get("errors", [])
        except Exception as e:  # noqa: BLE001
            print("audit error", a, e)
            errs = []
        return label, n, a, [e for e in errs if isinstance(e, dict)]

    with ThreadPoolExecutor(12) as ex:
        results = list(ex.map(run, jobs))
    report = {}
    for label in variants:
        per_ch = {}
        for n in chapters:
            by_aud = {a: {} for a in auditors}
            for lab, ch, a, errs in results:
                if lab == label and ch == n:
                    for e in errs:
                        try:
                            by_aud[a][int(e.get("id"))] = f"{e.get('who', '')}: {e.get('quote', '')}"
                        except (TypeError, ValueError):
                            pass
            common = set.intersection(*(set(v) for v in by_aud.values())) if by_aud else set()
            per_ch[n] = {"both": sorted(common), "any": sorted(set().union(*(set(v) for v in by_aud.values()))),
                         "examples": [by_aud[auditors[0]][i] for i in sorted(common)][:6]}
        report[label] = per_ch
        both = sum(len(v["both"]) for v in per_ch.values())
        anyc = sum(len(v["any"]) for v in per_ch.values())
        print(f"{label:12} ошибок рода (оба аудитора): {both:3}   (хотя бы один): {anyc:3}   по главам: "
              + ", ".join(f"{n}:{len(v['both'])}" for n, v in per_ch.items()))
    out = ROOT / "eval" / "out" / f"gender_audit_{Path(ws_path).name}_{rng}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for label, per_ch in report.items():
        ex = [f"гл.{n} {e}" for n, v in per_ch.items() for e in v["examples"]][:8]
        print(f"--- {label}:", *ex, sep="\n   ")


if __name__ == "__main__":
    main()
