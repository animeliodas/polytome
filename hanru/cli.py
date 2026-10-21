"""Командная строка Hanru.

Два способа работы:
1. Готовые сценарии (без агента, предсказуемо по токенам):
     hanru new книга.epub → hanru run <книга> → hanru status / stop / continue → hanru export <книга>
2. Диалог с агентом (как Claude Code): hanru <книга>  или  hanru ask <книга> "задача"
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import threading
import time
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from .agent import Agent
from .config import PKG_DIR, PROFILES, PROVIDER_CATALOG, ROLES, SLOTS, Config
from .engine import Engine
from .llm import LLMClient, LLMError
from .tools import Toolbox
from .workspace import Workspace, is_book, parse_range

console = Console(highlight=False, soft_wrap=bool(os.environ.get("HANRU_PLAIN")))

HELP = """\
[bold]Пишите задачу обычным языком[/bold], например:
  «подготовь книгу и переведи главы 1-5», «проверь имена в главах 1-20», «почему в гл.3 абзац 12 так переведён?»

[bold]Готовые действия (без агента)[/bold]
  /run [главы]         перевести (по умолчанию — все непереведённые)   /continue   доделать прерванное
  /prepare             глоссарий + библия для новой книги               /audit [главы]   проверка без LLM
  /export [epub|fb2|txt]  собрать книгу                                 /estimate [главы]  оценка токенов/времени
  /profile [имя]       quality | balanced | economy | local             /status   состояние книги
[bold]Книга и модели[/bold]
  /glossary [фильтр]  /bible  /check <главы>  /model [роль] [провайдер:модель]  /models [провайдер]
  /tool <имя> {json}  /tools  /protocol native|text|auto  /cost  /compact  /clear  /resume  /book <папка>  /quit
Ctrl+C — остановить (текущие батчи допишутся и сохранятся); второй Ctrl+C — выйти сразу."""


def _short(v, n=60) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


# ====================================================================== вывод
class ConsoleUI:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def assistant(self, text: str):
        console.print(Markdown(text))

    def tool_start(self, name: str, args: dict):
        a = ", ".join(f"{k}={_short(v, 40)}" for k, v in args.items())
        console.print(f"[cyan]●[/cyan] [bold]{name}[/bold]([dim]{a}[/dim])")

    def tool_end(self, name: str, result: str, seconds: float):
        console.print(f"  [dim]⎿ {self._summarize(name, result)}  ({seconds:.1f}s)[/dim]")
        if self.verbose:
            console.print(Panel(result[:3000], border_style="dim"))

    @staticmethod
    def _summarize(name: str, result: str) -> str:
        try:
            d = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            first = result.strip().split("\n")
            return _short(first[0], 90) + (f" … ({len(first)} строк)" if len(first) > 1 else "")
        if isinstance(d, dict) and "error" in d:
            return f"[red]ошибка: {_short(d['error'], 100)}[/red]"
        if name == "translate" and isinstance(d, dict):
            return (f"переведено {d.get('translated') or '—'}; ошибок {len(d.get('failed', []))}; "
                    f"правок рецензента {d.get('review_fixes_total', 0)}; hard {d.get('hard_issues_total', 0)}")
        return _short(d, 110)

    def status(self, text: str):
        console.print(f"[yellow]⚑ {text}[/yellow]")

    def event(self, e: dict):
        ev = e.get("ev")
        if ev == "chapter_done":
            r = e["report"]
            mark = "[green]✓[/green]" if r.get("complete") else "[red]✗[/red]"
            console.print(f"    {mark} гл.{r['chapter']}: {r.get('segments', '?')} абз., рецензент {r.get('review_fixes', 0)}, "
                          f"починок {r.get('repairs', 0)}, hard {r.get('hard_issues', 0)}, {r.get('seconds', '?')}s")
        elif ev == "prepare_start":
            console.print("подготовка книги: имена, термины, библия…")
        elif ev == "prepare_done":
            p = e["report"]
            console.print(f"  глоссарий: {p['glossary_total']} записей, библия: {'написана' if p['bible_written'] else 'была'}"
                          + (f", [yellow]конфликты пола: {len(p['gender_conflicts'])}[/yellow]" if p["gender_conflicts"] else ""))
        elif ev == "extract_start":
            console.print(f"    [dim]поиск новых имён/терминов в {e['chapters']} гл.…[/dim]")
        elif ev == "extract_done":
            console.print(f"    [dim]имён добавлено: {e['added']}, терминов-кандидатов: {e.get('pending', 0)}[/dim]")
        elif ev == "repair":
            console.print(f"    [dim]⟲ гл.{e['n']} [{e['seg']}] {_short(e['why'], 70)}[/dim]")
        elif ev == "resume":
            console.print(f"    [cyan]↻ гл.{e['n']}: продолжаю с места остановки ({e['done']}/{e['total']} абз., "
                          f"стадия {e['stage']})[/cyan]")
        elif ev == "chapter_cancelled":
            console.print(f"    [yellow]⏸ гл.{e['n']}: остановлено, сохранено {e['done']}/{e['total']} абз.[/yellow]")
        elif ev == "edit_done":
            r = e["report"]
            console.print(f"    {'[red]✗[/red]' if r.get('error') else '[green]✓[/green]'} гл.{r['chapter']}: "
                          f"исправлено абзацев {r.get('fixed', 0)}" + (f" ({r['error'][:60]})" if r.get("error") else ""))
        elif ev == "log":
            console.print(f"    [dim yellow]{_short(e['msg'], 140)}[/dim yellow]")


def run_interruptible(fn, engine: Engine | None = None, agent: Agent | None = None):
    """Выполнить fn в потоке; Ctrl+C → мягкая остановка, второй Ctrl+C → выход."""
    box: dict = {}

    def work():
        try:
            box["out"] = fn()
        except Exception as e:  # noqa: BLE001
            box["err"] = e

    th = threading.Thread(target=work, daemon=True)
    th.start()
    try:
        while th.is_alive():
            th.join(0.2)
    except KeyboardInterrupt:
        console.print("[yellow]⏹ останавливаю: текущие батчи допишутся и сохранятся (обычно секунды, до ~1 мин). "
                      "Второй Ctrl+C — выйти сразу (потеряется только недописанный батч).[/yellow]")
        if agent:
            agent.cancel.set()
        if engine:
            engine.cancel.set()
        while th.is_alive():
            try:
                th.join(0.5)
            except KeyboardInterrupt:
                break
        if engine:
            engine.cancel.clear()
    if "err" in box:
        raise box["err"]
    return box.get("out")


# ====================================================================== REPL с агентом
class App:
    def __init__(self, book: Path, cfg: Config, verbose=False, protocol=None):
        self.cfg = cfg
        self.ui = ConsoleUI(verbose)
        self.llm = LLMClient(cfg.parallel)
        self.protocol = protocol
        self.open_book(book)

    def open_book(self, book: Path):
        self.ws = Workspace(book)
        if not self.ws.meta_dir.is_dir():
            self.ws.init()
        self.engine = Engine(self.ws, self.cfg, self.llm, on_event=self.ui.event)
        self.tb = Toolbox(self.engine)
        self.agent = Agent(self.tb, self.cfg, self.llm, self.ui, self.protocol)

    def run_agent(self, text: str):
        t0, tok0 = time.time(), self.llm.usage.total()
        try:
            run_interruptible(lambda: self.agent.run(text), self.engine, self.agent)
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]Ошибка: {e}[/red]")
        console.print(f"[dim]— {time.time() - t0:.0f}s, {self.llm.usage.total() - tok0:,} ток.[/dim]")

    def run_tool(self, name: str, args: dict):
        t0 = time.time()
        out = run_interruptible(lambda: self.tb.call(name, args), self.engine)
        if out is not None:
            self.ui.tool_end(name, out, time.time() - t0)
        return out

    def command(self, line: str) -> bool:
        parts = line.strip().split(maxsplit=2)
        cmd, args = parts[0].lower(), parts[1:]
        if cmd in ("/quit", "/exit", "/q"):
            return False
        if cmd == "/help":
            console.print(HELP)
        elif cmd == "/status":
            console.print_json(self.tb.call("workspace_status", {}))
        elif cmd == "/run":
            self.run_tool("translate", {"chapters": args[0] if args else "all"})
        elif cmd == "/prepare":
            self.run_tool("prepare_book", {})
        elif cmd == "/audit":
            console.print_json(self.tb.call("check", {"chapters": args[0] if args else "all"}))
            console.print_json(self.tb.call("verify_genders", {}))
        elif cmd == "/export":
            console.print_json(self.tb.call("export_book", {"format": args[0] if args else "epub"}))
        elif cmd == "/estimate":
            console.print_json(self.tb.call("estimate", {"chapters": args[0] if args else "all"}))
        elif cmd == "/profile":
            if args:
                if args[0] not in PROFILES:
                    console.print(f"профили: {', '.join(PROFILES)}")
                else:
                    self.ws.meta["profile"] = args[0]
                    self.ws.save_meta()
                    self.open_book(self.ws.root)
                    console.print(f"профиль книги: {args[0]} — {PROFILES[args[0]]['desc']}")
            else:
                print_profiles(self.engine.cfg)
        elif cmd == "/glossary":
            d = json.loads(self.tb.call("glossary_list", {"filter": args[0] if args else "", "limit": 500}))
            t = Table("оригинал", "перевод", "тип", "пол", "статус", "заметка", show_lines=False)
            for e in d["entries"]:
                t.add_row(e["source"], e["target"], e.get("kind", ""), e.get("gender", ""), e.get("status", ""),
                          _short(e.get("note", ""), 40))
            console.print(t)
            console.print(f"[dim]всего {d['total']}[/dim]")
        elif cmd == "/bible":
            console.print(Markdown(self.ws.bible() or "(пусто)"))
        elif cmd == "/check":
            console.print_json(self.tb.call("check", {"chapters": args[0] if args else "all"}))
        elif cmd == "/tools":
            for t in self.tb.tools.values():
                console.print(f"[bold]{t.name}[/bold] — {t.description}")
        elif cmd == "/tool":
            if not args:
                console.print("использование: /tool <имя> {json}")
            else:
                out = self.run_tool(args[0], json.loads(args[1]) if len(args) > 1 else {})
                console.print((out or "")[:6000])
        elif cmd == "/model":
            if len(args) == 2:
                role, spec = args
                self.cfg.resolve(spec.split(",")[0])
                target = "slots" if role in SLOTS else "roles"
                self.cfg.data.setdefault(target, {})[role] = spec.split(",") if role == "translator" else spec
                self.cfg.save()
                self.open_book(self.ws.root)
                console.print(f"{role} → {spec} (сохранено в {self.cfg.path})")
            else:
                print_config(self.engine.cfg)
        elif cmd == "/models":
            list_models(self.cfg, self.llm, args[0] if args else None)
        elif cmd == "/protocol":
            if args:
                self.agent.switch_protocol(args[0])
            console.print(f"протокол: {self.agent.protocol}")
        elif cmd == "/continue":
            job = self.ws.interrupted_job()
            if not job:
                console.print("прерванных задач нет")
            else:
                console.print(f"продолжаю: осталось {len(job['remaining'])} гл. (начатых: {len(job['partial'])})")
                self.run_tool("translate", {"chapters": ",".join(map(str, job["chapters"])),
                                            "retranslate": job.get("retranslate", False),
                                            "review": job.get("review", True)})
        elif cmd == "/cost":
            console.print(self.llm.usage.summary())
        elif cmd == "/compact":
            self.agent.compact(force=True)
        elif cmd == "/clear":
            self.agent = Agent(self.tb, self.cfg, self.llm, self.ui, self.protocol)
            console.print("новый диалог")
        elif cmd == "/resume":
            sess = sorted((self.ws.meta_dir / "sessions").glob("*.json"))
            if sess:
                self.agent.load(sess[-1])
                console.print(f"продолжаю {sess[-1].name} ({len(self.agent.messages)} сообщений)")
            else:
                console.print("сохранённых диалогов нет")
        elif cmd == "/book":
            if args:
                self.open_book(Path(" ".join(args)))
            console.print(f"книга: {self.ws.root}")
        else:
            console.print(f"неизвестная команда {cmd}; /help")
        return True

    def interrupted_banner(self) -> str:
        job = self.ws.interrupted_job()
        if not job:
            return ""
        why = "процесс прервался (сбой или выключение)" if job["status"] == "running" else "остановлена"
        return (f"\n[yellow]⚠ Незавершённая задача перевода ({why}, {job.get('updated_at', '')}): "
                f"главы {job['chapters'][0]}–{job['chapters'][-1]}, готово {len(job.get('done', []))}, "
                f"осталось {len(job['remaining'])}. Продолжить: /continue[/yellow]")

    def repl(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        session = PromptSession(history=FileHistory(str(self.cfg.base_dir / ".hanru_history")))
        st = self.ws.status()
        c = self.engine.cfg
        console.print(Panel.fit(
            f"[bold]Hanru[/bold] — агентный переводчик книг\n"
            f"книга: [cyan]{st['title']}[/cyan]  глав {st['chapters_total']}, переведено {st['chapters_translated']}\n"
            f"профиль: [bold]{c.profile_name}[/bold]   переводчик: {c.role_spec('translator')}   агент: {c.role_spec('agent')}\n"
            f"/help — команды" + self.interrupted_banner(), border_style="cyan"))
        while True:
            try:
                line = session.prompt("› ")
            except (EOFError, KeyboardInterrupt):
                break
            if not line.strip():
                continue
            if line.startswith("/"):
                try:
                    if not self.command(line):
                        break
                except Exception as e:  # noqa: BLE001
                    console.print(f"[red]{type(e).__name__}: {e}[/red]")
                continue
            self.run_agent(line)


# ====================================================================== общие печаталки
def print_config(cfg: Config):
    s = cfg.summary()
    t = Table("роль", "модель", title=f"профиль: {s['profile']} — {s['profile_desc']}")
    for r, spec in s["roles"].items():
        t.add_row(r, spec)
    console.print(t)
    console.print(f"[dim]движок: {s['engine']} · конфиг: {s['config_file']}[/dim]")


def print_profiles(cfg: Config):
    t = Table("профиль", "переводчик", "рецензент", "что это")
    for name, p in PROFILES.items():
        c = cfg.with_overrides(profile=name)
        mark = " ◀" if name == cfg.profile_name else ""
        t.add_row(name + mark, c.role_spec("translator"), f"{p['engine'].get('review')}", p["desc"])
    console.print(t)


def list_models(cfg: Config, llm: LLMClient, provider: str | None):
    provs = [provider] if provider else [p for p in cfg.providers() if cfg.provider_key(p)
                                         or cfg.providers()[p].get("local")]
    for p in provs:
        try:
            ms = llm.list_models(cfg.resolve(f"{p}:x"))
            sug = set((cfg.providers()[p].get("suggest") or {}).values())
            console.print(f"[bold]{p}[/bold] ({len(ms)}): " + ", ".join(f"[green]{m}[/green]" if m in sug else m for m in ms))
        except Exception as e:  # noqa: BLE001
            console.print(f"[bold]{p}[/bold]: [red]недоступен ({_short(str(e), 80)})[/red]")


def _engine(book: Path, cfg: Config, verbose=False, profile: str | None = None) -> tuple[Engine, Toolbox, ConsoleUI]:
    ui = ConsoleUI(verbose)
    ws = Workspace(book)
    if not ws.meta_dir.is_dir():
        ws.init()
    eng = Engine(ws, cfg, LLMClient(cfg.parallel), on_event=ui.event, profile=profile)
    return eng, Toolbox(eng), ui


# ====================================================================== подкоманды
def cmd_new(a, cfg):
    from .formats import create_book
    ws, rep = create_book(Path(a.source), Path(a.to) if a.to else None, a.title, fix_breaks=a.fix_breaks)
    console.print(Panel.fit(
        f"[bold]Книга создана:[/bold] {ws.root}\nглав: {rep['chapters']} · язык: {rep['lang']} · "
        f"{rep.get('split', '')}" + (f"\nавтор: {rep['author']}" if rep.get("author") else "")
        + ("\nобложка: да" if rep.get("cover") else "")
        + (f"\nсклеено разорванных абзацев: {rep['line_breaks_fixed']}" if rep.get("line_breaks_fixed") else ""),
        border_style="green"))
    if rep.get("line_breaks") and not rep.get("line_breaks_fixed"):
        console.print(f"[yellow]⚠ В оригинале {rep['line_breaks']} абзацев разорваны посреди фразы (жёсткие переносы) — "
                      f"переводчик на них сбивается. Исправить (с резервной копией): hanru new \"{a.source}\" "
                      f"--fix-breaks[/yellow]")
    if rep["lang"] == "ru":
        console.print("[yellow]⚠ Текст уже на русском — переводить нечего (можно проверить/собрать: audit, export).[/yellow]")
    eng = Engine(ws, cfg, LLMClient(1))
    est = eng.estimate(list(ws.chapters()))
    console.print(f"оценка ({est['profile']}): ~{est['tokens_estimate']:,} токенов, ~{est['time_estimate_min']} мин")
    console.print(f"\nДальше:\n  hanru run \"{ws.root}\"              — перевести всё\n"
                  f"  hanru run \"{ws.root}\" -c 1-10      — сначала 10 глав на пробу\n"
                  f"  hanru \"{ws.root}\"                  — открыть диалог с агентом")


def cmd_run(a, cfg, continuing=False):
    book = Path(a.book)
    if continuing:  # продолжаем с теми же настройками, с какими задача шла
        wsx = Workspace(book)
        prev = wsx.load_job() or {}
        if not a.profile and prev.get("profile") in PROFILES:
            a.profile = prev["profile"]
        if not a.export and wsx.meta.get("last_export"):
            a.export = wsx.meta["last_export"]
    elif a.export:
        wsx = Workspace(book)
        wsx.meta["last_export"] = a.export
        wsx.save_meta()
    if a.profile:
        cfg = cfg.with_overrides(profile=a.profile)
    if getattr(a, "background", False):
        from .jobs import spawn
        argv = ["continue" if continuing else "run", str(book)] + _passthrough(a)
        r = spawn(book, argv)
        console.print_json(json.dumps(r, ensure_ascii=False))
        return
    eng, tb, ui = _engine(book, cfg, a.verbose, a.profile)
    ws = eng.ws
    if continuing:
        job = ws.interrupted_job()
        if not job:
            console.print("прерванных задач нет — всё доделано")
            return
        if job.get("kind") == "edit":
            console.print(f"продолжаю редактуру ({job.get('task')}): осталось {len(job['remaining'])} гл.")
            r = run_interruptible(lambda: eng.edit_chapters(job["chapters"], job.get("task", "genders")), eng)
            if r:
                console.print(f"исправлено абзацев: {r['paragraphs_fixed']} · {r['tokens']}")
            return
        chapters, retr, review = job["chapters"], job.get("retranslate", False), job.get("review", True)
        console.print(f"продолжаю задачу {job['id']}: осталось {len(job['remaining'])} гл.")
    else:
        chapters = parse_range(a.chapters or "all", list(ws.chapters()))
        retr, review = a.retranslate, not a.no_review
    if not chapters:
        console.print("[red]нет глав для перевода (проверьте папку оригинала и диапазон)[/red]")
        return
    console.print(f"[bold]{ws.meta['title']}[/bold] · профиль {eng.cfg.profile_name} · переводчик "
                  f"{eng.cfg.role_spec('translator')}")
    est = eng.estimate(chapters)
    if est["chapters"]:
        console.print(f"[dim]к переводу {est['chapters']} гл., оценка ~{est['tokens_estimate']:,} ток., "
                      f"~{est['time_estimate_min']} мин[/dim]")
    t0 = time.time()
    r = run_interruptible(lambda: eng.run(chapters, retranslate=retr, review=review, wave=a.wave,
                                          prepare=not continuing and not a.no_prepare), eng)
    if r is None:
        return
    console.print(Panel.fit(
        f"переведено: {r['translated'] or '—'}\nпропущено (готовы): {r['skipped'] or '—'}\n"
        f"ошибок: {r['failed'] or 0} · hard-проблем: {r['hard_issues_total']} · правок рецензента: {r['review_fixes_total']}\n"
        f"{time.time() - t0:.0f} с · {r['tokens']}"
        + (f"\n[yellow]{r['note']}[/yellow]" if r.get("note") else "")
        + (f"\n[red]{r['HALTED']}[/red]" if r.get("HALTED") else "")
        + (f"\n[yellow]⚠ конфликты пола: {[i['target'] for i in r['GENDER_CONFLICTS']['items']]}[/yellow]"
           if r.get("GENDER_CONFLICTS") else ""), title="итог", border_style="green"))
    if a.export and not r.get("cancelled") and not r.get("HALTED"):
        cmd_export(argparse.Namespace(book=str(book), format=a.export, out=None, chapters=None, title=None,
                                      author=None), cfg)


def _passthrough(a) -> list[str]:
    out = []
    for flag, attr in (("-c", "chapters"), ("--profile", "profile"), ("--export", "export"), ("--wave", "wave")):
        v = getattr(a, attr, None)
        if v:
            out += [flag, str(v)]
    for flag, attr in (("--no-review", "no_review"), ("--retranslate", "retranslate"), ("--no-prepare", "no_prepare")):
        if getattr(a, attr, False):
            out.append(flag)
    return out


def cmd_status(a, cfg):
    from .jobs import status
    if a.watch:
        try:
            while True:
                s = status(Path(a.book))
                j = s.get("job") or {}
                console.print(f"{time.strftime('%H:%M:%S')} {j.get('state')}: готово {j.get('done')}/"
                              f"{(j.get('done') or 0) + (j.get('left') or 0)}, в работе {j.get('in_progress')}, "
                              f"ETA {j.get('eta_min')} мин, токенов {j.get('tokens_this_run')}")
                if j.get("state") != "running":
                    break
                time.sleep(15)
        except KeyboardInterrupt:
            pass
        return
    s = status(Path(a.book))
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=1))
        return
    j = s.get("job")
    console.print(f"[bold]{s['book']}[/bold]: переведено {s['chapters_translated']} из {s['chapters_total']} глав")
    if j:
        color = {"running": "cyan", "done": "green", "interrupted": "yellow", "cancelled": "yellow"}.get(j["state"], "red")
        bits = [f"главы {j['chapters']}"] + [f"{label} {j[k]}" for k, label in (
            ("done", "готово"), ("left", "осталось"), ("in_progress", "в работе"), ("eta_min", "ETA, мин"),
            ("tokens_this_run", "токенов"), ("failed", "ошибки")) if j.get(k) not in (None, [], 0)]
        console.print(f"задача [{color}]{j['state']}[/{color}]" + (f" ({j['profile']})" if j.get("profile") else "")
                      + ": " + ", ".join(bits))
        if j.get("hint"):
            console.print(f"[yellow]{j['hint']}[/yellow]")
    for line in s.get("log_tail", [])[-3:]:
        console.print(f"[dim]  {line}[/dim]")


def cmd_stop(a, cfg):
    from .jobs import request_stop
    console.print_json(json.dumps(request_stop(Path(a.book)), ensure_ascii=False))


def cmd_audit(a, cfg):
    eng, tb, ui = _engine(Path(a.book), cfg)
    chs = a.chapters or "all"
    rep = json.loads(tb.call("check", {"chapters": chs}))
    hard = {n: r for n, r in rep.items() if isinstance(r, dict) and r.get("hard")}
    names = json.loads(tb.call("name_consistency", {"chapters": chs}))
    genders = json.loads(tb.call("verify_genders", {}))
    total = sum(r["hard"] for r in hard.values())
    console.print(f"[bold]Аудит[/bold]: глав проверено {len(rep)}, с hard-ошибками {len(hard)}, всего hard {total}")
    for n, r in list(hard.items())[:15]:
        first = next(iter(r["issues"].values()), [""])[0]
        console.print(f"  гл.{n}: {r['hard']} — {_short(first, 110)}")
    if names:
        console.print(f"[yellow]варианты имён по книге: {len(names)}[/yellow] " + "; ".join(
            f"{x['target']}: {', '.join(list(x['suspicious'])[:3])}" for x in names[:6]))
    if genders.get("conflicts"):
        console.print(f"[yellow]конфликты пола: {[c['target'] for c in genders['conflicts']]}[/yellow]")
    if a.fix:
        norm = sum(eng.normalize_chapter(int(n)) for n in rep)
        if norm:
            console.print(f"без LLM нормализовано абзацев (числа цифрами, пунктуация): {norm}")
    if a.fix and hard:
        console.print(f"чиню {len(hard)} глав…")

        def fix_all():
            return [eng.repair_chapter(int(n)) for n in hard]
        res = run_interruptible(fix_all, eng) or []
        console.print(f"hard до: {sum(r['hard_before'] for r in res)} → после: {sum(r['hard_after'] for r in res)}")
    elif hard:
        console.print(f"исправить автоматически: hanru audit \"{a.book}\" --fix")


def cmd_clean(a, cfg):
    from .cleanup import apply, detect, load_rules, save_rules
    ws = Workspace(Path(a.book))
    if not ws.meta_dir.is_dir():
        ws.init()
    rules = load_rules(ws)
    for line in a.drop or []:
        rules["drop_lines"].append(line)
    for rx in a.drop_regex or []:
        rules["drop_regex"].append(rx)
    if a.drop or a.drop_regex:
        save_rules(ws, rules)
    targets = ["source", "translation"] if a.target == "both" else [a.target]
    for t in targets:
        rep = detect(ws, t, rules) if not a.apply else apply(ws, t, rules)
        console.print(f"[bold]{'Очищено' if a.apply else 'Найдено (предпросмотр)'} — "
                      f"{'оригинал' if t == 'source' else 'перевод'}[/bold]: глав {rep['chapters_affected']} из "
                      f"{rep['chapters']}, абзацев {rep['paragraphs_to_remove']}, хвостов с заметками автора "
                      f"{rep['author_tails']}")
        for r in rep["repeated_lines"][:5]:
            console.print(f"  служебная строка в {r['chapters']} гл.: [dim]{_short(r['line'], 90)}[/dim]")
        for e in rep["examples"][:8]:
            console.print(f"  [dim]{_short(e, 120)}[/dim]")
        if a.apply and rep.get("backup"):
            console.print(f"  резервная копия: {rep['backup']}")
    if not a.apply:
        console.print(f"Применить: hanru clean \"{a.book}\" --apply"
                      + (f" --target {a.target}" if a.target != "translation" else ""))


def cmd_cast(a, cfg):
    """Таблица персонажей для редактора рода: показать улики / задать пол."""
    from .glossary import Glossary
    ws = Workspace(Path(a.book))
    if not ws.meta_dir.is_dir():
        ws.init()
    gl = Glossary(ws.glossary_path)
    for item in a.set or []:  # «Линь Яо=f:младшая сестра героя»
        name, _, rest = item.partition("=")
        g, _, note = rest.partition(":")
        if g not in ("m", "f"):
            console.print(f"[red]пол для {name}: m или f[/red]")
            continue
        src = next((e["source"] for e in gl.entries if e.get("target") == name.strip()), name.strip())
        gl.upsert(src, name.strip(), "person", g, note or None, status="confirmed")
        console.print(f"{name.strip()} → {'мужчина' if g == 'm' else 'женщина'}")
    if a.set:
        gl.save()
    eng = Engine(ws, cfg, LLMClient(1))
    console.print(eng.cast_table() or "[dim](таблица пуста)[/dim]")
    if a.suggest:
        from .checks import find_chinese_names, gender_votes
        from collections import Counter
        names, votes = Counter(), {}
        texts = [ws.target_path(n).read_text(encoding="utf-8-sig") for n in ws.chapters() if ws.is_translated(n)]
        for t in texts:
            names.update(find_chinese_names(t))
        top = [n for n, c in names.most_common(60) if c >= 20]
        for t in texts:
            for nm, g, _ in gender_votes(t, top):
                votes.setdefault(nm, Counter())[g] += 1
        tb = Table("персонаж", "упоминаний", "муж", "жен", "в таблице")
        known = {e["target"] for e in gl.entries if e.get("gender")}
        for nm in top:
            v = votes.get(nm, Counter())
            tb.add_row(nm, str(names[nm]), str(v["masc"]), str(v["femn"]), "✓" if nm in known else "")
        console.print(tb)
        console.print("[dim]Машинный перевод с китайского ставит мужской род по умолчанию: заметная доля «жен» у "
                      "персонажа — признак, что это женщина. Задать: hanru cast <книга> --set \"Имя=f:кто это\"[/dim]")


def cmd_edit(a, cfg):
    if a.profile:
        cfg = cfg.with_overrides(profile=a.profile)
    book = Path(a.book)
    if a.background:
        from .jobs import spawn
        args = ["edit", str(book), "--genders"] + (["-c", a.chapters] if a.chapters else []) \
            + (["--names", a.names] if a.names else []) + (["--redo"] if a.redo else []) \
            + (["--profile", a.profile] if a.profile else [])
        console.print_json(json.dumps(spawn(book, args), ensure_ascii=False))
        return
    eng, tb, ui = _engine(book, cfg)
    if not eng.cast_table():
        console.print("[red]Сначала задайте пол персонажей: hanru cast <книга> --suggest / --set \"Имя=f\"[/red]")
        return
    chs = parse_range(a.chapters or "all", list(eng.ws.chapters()))
    redo = a.redo
    if a.names:  # только главы с этими персонажами (после уточнения таблицы) — заново
        names = [x.strip() for x in a.names.split(",") if x.strip()]
        chs = [n for n in chs if eng.ws.is_translated(n)
               and any(nm in eng.ws.target_path(n).read_text(encoding="utf-8-sig") for nm in names)]
        redo = True
    console.print(f"редактура рода: {len(chs)} гл., редактор {eng.cfg.role_spec('reviewer')}")
    r = run_interruptible(lambda: eng.edit_chapters(chs, redo=redo), eng)
    if r:
        console.print(Panel.fit(f"отредактировано: {r['edited'] or '—'}\nисправлено абзацев: {r['paragraphs_fixed']}"
                                f"\nпропущено (уже): {r['skipped'] or '—'}\n{r['seconds']} с · {r['tokens']}",
                                title="итог", border_style="green"))


def cmd_export(a, cfg):
    from .formats import export_book
    eng, tb, ui = _engine(Path(a.book), cfg)
    ws = eng.ws
    eng.ensure_title_meta()
    chs = parse_range(a.chapters, list(ws.chapters())) if a.chapters else None
    fmts = ["epub", "fb2", "txt"] if a.format == "all" else [a.format]
    for f in fmts:
        out = Path(a.out) if a.out and len(fmts) == 1 else None
        r = export_book(ws, f, out, chs, a.title, a.author)
        console.print(f"[green]✓[/green] {r['format'].upper()}: {r['path']} — {r['chapters']} гл., {r['bytes'] // 1024} КБ"
                      + (f" [yellow](нет перевода для {r['missing_total']} гл.)[/yellow]" if r["missing_total"] else ""))


def cmd_estimate(a, cfg):
    eng, tb, ui = _engine(Path(a.book), cfg)
    chs = parse_range(a.chapters or "all", list(eng.ws.chapters()))
    t = Table("профиль", "глав", "токенов (оценка)", "на главу", "время, мин")
    for name in PROFILES:
        e = Engine(eng.ws, cfg.with_overrides(profile=name), eng.llm).estimate(chs)
        t.add_row(name + (" ◀" if name == eng.cfg.profile_name else ""), str(e["chapters"]),
                  f"{e['tokens_estimate']:,}", f"{e['tokens_per_chapter']:,}", str(e["time_estimate_min"]))
    console.print(t)
    console.print("[dim]по замерам на китайских веб-новеллах; фактические цифры — в hanru status во время работы[/dim]")


def cmd_profiles(a, cfg):
    if a.set:
        if a.set not in PROFILES:
            console.print(f"[red]нет профиля {a.set}[/red]; есть: {', '.join(PROFILES)}")
            return
        if a.book:
            ws = Workspace(Path(a.book))
            ws.meta["profile"] = a.set
            ws.save_meta()
            console.print(f"профиль книги «{ws.meta['title']}»: {a.set}")
        else:
            cfg.data["profile"] = a.set
            cfg.save()
            console.print(f"профиль по умолчанию: {a.set}")
        return
    print_profiles(cfg)


def cmd_models(a, cfg):
    list_models(cfg, LLMClient(4), a.provider)


def cmd_doctor(a, cfg):
    """Проверка: ключи, связь, ответ каждой роли, tool-calling агента."""
    llm = LLMClient(4)
    console.print(f"конфиг: {cfg.path} · профиль: {cfg.profile_name}")
    t = Table("роль", "модель", "ключ", "ответ", "сек", "заметка")
    seen = {}
    for role in ROLES:
        for ref in cfg.role(role):
            key = "✓" if ref.api_key or cfg.providers().get(ref.provider, {}).get("local") else "[red]нет[/red]"
            sk = (ref.spec, role == "agent", str(ref.extra))
            if sk in seen:
                ok, sec, note = seen[sk]
            else:
                t0 = time.time()
                try:
                    tools = [{"type": "function", "function": {"name": "ping", "description": "ответить",
                                                                "parameters": {"type": "object", "properties": {}}}}]
                    res = llm.chat(ref, [{"role": "user", "content": "Вызови инструмент ping."}],
                                   tools=tools if role == "agent" else None, max_tokens=3000, role="doctor")
                    ok = "[green]ok[/green]"
                    note = ("tool-calling ✓" if res.tool_calls else "без нативных tools → text-протокол") \
                        if role == "agent" else ""
                except LLMError as e:
                    ok, note = "[red]ошибка[/red]", _short(str(e), 60)
                sec = f"{time.time() - t0:.1f}"
                seen[sk] = (ok, sec, note)
            t.add_row(role, ref.spec + (f"@{ref.extra.get('reasoning_effort')}" if ref.extra.get("reasoning_effort") else ""),
                      key, ok, sec, note)
    console.print(t)
    try:
        import pymorphy3  # noqa: F401
        console.print("морфология (проверка рода): [green]ok[/green]")
    except ImportError:
        console.print("морфология: [yellow]нет pymorphy3 — проверка рода отключена[/yellow] (pip install pymorphy3 pymorphy3-dicts-ru)")


def cmd_setup(a, cfg):
    """Мастер: провайдеры → ключи → модели по слотам → профиль. Неинтерактивно — флагами."""
    llm = LLMClient(4)
    if a.provider or a.strong or a.profile:  # неинтерактивный режим (скрипты, Hermes)
        if a.provider and a.key:
            cfg.set_secret(a.provider, a.key)
        if a.base_url and a.provider:
            cfg.data.setdefault("providers", {}).setdefault(a.provider, {})["base_url"] = a.base_url
        for slot in SLOTS:
            v = getattr(a, slot, None)
            if v:
                spec = v if ":" in v.split("/")[0] and v.split(":", 1)[0] in cfg.providers() else f"{a.provider}:{v}"
                cfg.data.setdefault("slots", {})[slot] = spec
        if a.profile:
            cfg.data["profile"] = a.profile
        fixed = fill_missing_slots(cfg)
        cfg.save()
        if fixed:
            console.print("[dim]слоты без ключа заполнены из указанных: " + ", ".join(fixed) + "[/dim]")
        print_config(Config.load(cfg.path))
        return
    console.print(Panel.fit("[bold]Настройка Hanru[/bold]\n1) провайдеры и ключи  2) модели  3) профиль\n"
                            "Enter — принять вариант в [скобках]", border_style="cyan"))
    names = list(PROVIDER_CATALOG)
    t = Table("№", "провайдер", "что это", "ключ")
    for i, p in enumerate(names, 1):
        info = PROVIDER_CATALOG[p]
        has = "локально" if info.get("local") else ("[green]есть[/green]" if cfg.provider_key(p) else "—")
        t.add_row(str(i), p, info["title"], has)
    console.print(t)
    default = [str(i) for i, p in enumerate(names, 1) if cfg.provider_key(p)] or ["1"]
    pick = input(f"Какие провайдеры использовать (номера через запятую) [{','.join(default)}]: ").strip() or ",".join(default)
    chosen = [names[int(x) - 1] for x in pick.replace(" ", "").split(",") if x.isdigit() and 0 < int(x) <= len(names)]
    available: dict[str, list[str]] = {}
    for p in chosen:
        info = PROVIDER_CATALOG[p]
        if not info.get("local") and not cfg.provider_key(p):
            console.print(f"Ключ для {info['title']}" + (f" — получить: {info['keys_url']}" if info.get("keys_url") else ""))
            key = getpass.getpass("  вставьте ключ (не отображается): ").strip()
            if key:
                cfg.set_secret(p, key)
        try:
            available[p] = llm.list_models(cfg.resolve(f"{p}:x"))
            console.print(f"  [green]✓[/green] {p}: доступно моделей {len(available[p])}")
        except Exception as e:  # noqa: BLE001
            console.print(f"  [red]✗[/red] {p}: {_short(str(e), 90)}")
    slots = cfg.data.setdefault("slots", {})
    hints = {"strong": "основной переводчик (качество)", "fast": "быстрая/дешёвая (профиль economy)",
             "agent": "ведёт диалог, вызывает инструменты", "reviewer": "рецензент (лучше другая модель, чем strong)",
             "extractor": "ищет имена и термины (дешёвая)", "local": "модель на вашем ПК (профиль local)"}
    for slot in SLOTS:
        if slot == "local" and not any(PROVIDER_CATALOG[p].get("local") for p in chosen):
            continue
        sug = next((f"{p}:{PROVIDER_CATALOG[p]['suggest'][slot]}" for p in chosen
                    if (PROVIDER_CATALOG[p].get("suggest") or {}).get(slot)
                    and PROVIDER_CATALOG[p]["suggest"][slot] in available.get(p, [PROVIDER_CATALOG[p]["suggest"][slot]])),
                   slots.get(slot) or f"{chosen[0]}:" + (available.get(chosen[0]) or ["?"])[0])
        v = input(f"{slot:9} — {hints[slot]} [{sug}]: ").strip()
        if v.isdigit() and chosen and available.get(chosen[0]):
            v = f"{chosen[0]}:{available[chosen[0]][int(v) - 1]}"
        elif v and ":" not in v and chosen:
            v = f"{chosen[0]}:{v}"
        slots[slot] = v or sug
    fill_missing_slots(cfg)
    print_profiles(cfg)
    prof = input(f"Профиль [{cfg.profile_name}]: ").strip() or cfg.profile_name
    if prof in PROFILES:
        cfg.data["profile"] = prof
    path = cfg.save()
    console.print(f"[green]Сохранено:[/green] {path} (ключи — в {cfg.secrets_path.name})")
    console.print("Проверить: hanru doctor")


def hermes_home() -> Path:
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"])
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        p = Path(os.environ["LOCALAPPDATA"]) / "hermes"
        if p.is_dir():
            return p
    return Path.home() / ".hermes"


def cmd_integrate(a, cfg):
    """Установить скил hanru-translator в Hermes (копия папки + настройка пути к hanru)."""
    import shutil
    import subprocess
    src = PKG_DIR / "integrations" / "hermes" / "hanru-translator"
    home = Path(a.hermes_home) if a.hermes_home else hermes_home()
    if not home.is_dir():
        console.print(f"[red]Hermes не найден в {home}[/red] — укажите --hermes-home")
        return
    dst = home / "skills" / a.category / "hanru-translator"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    console.print(f"[green]✓[/green] скил установлен: {dst}")
    for old in (home / "skills").glob("*/codek-translator"):  # скил под старым именем проекта
        shutil.rmtree(old, ignore_errors=True)
        console.print(f"[green]✓[/green] удалён старый скил: {old}")
    exe = Path(sys.executable).parent / ("hanru.exe" if os.name == "nt" else "hanru")
    command = str(exe) if exe.exists() else f'"{sys.executable}" -m hanru'
    library = a.library or str(Path.home() / "Books" / "Hanru")
    hermes = shutil.which("hermes") or next((str(x) for x in (home / "bin" / "hermes.exe", home / "bin" / "hermes")
                                             if x.exists()), None)
    if hermes:
        for key, val in (("skills.config.hanru.command", command), ("skills.config.hanru.library", library)):
            r = subprocess.run([hermes, "config", "set", key, val], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            console.print(("[green]✓[/green] " if r.returncode == 0 else "[yellow]?[/yellow] ") + f"{key} = {val}"
                          + ("" if r.returncode == 0 else f" ({_short(r.stderr or r.stdout, 120)})"))
    else:
        console.print(f"Настройте в Hermes: hermes config set skills.config.hanru.command \"{command}\"")
    console.print("Новые скилы видны в новых сессиях Hermes. Пример: «переведи книгу D:\книга.epub»")


def fill_missing_slots(cfg: Config) -> list[str]:
    """Слот указывает на провайдера без ключа (например, по умолчанию ollama-cloud, а у пользователя только
    OpenRouter) → взять модель из соседнего слота с рабочим провайдером."""
    slots = cfg.data.setdefault("slots", {})
    provs = cfg.providers()

    def usable(spec):
        p = (spec or "").split(":", 1)[0]
        return p in provs and (provs[p].get("local") or cfg.provider_key(p))
    chain = {"strong": ["fast"], "fast": ["strong"], "agent": ["strong", "fast"], "reviewer": ["fast", "strong"],
             "extractor": ["fast", "strong"]}
    fixed = []
    from .config import DEFAULT_CONFIG
    for slot, alts in chain.items():
        cur = slots.get(slot) or DEFAULT_CONFIG["slots"][slot]
        if usable(cur):
            continue
        alt = next((slots.get(x) for x in alts if usable(slots.get(x))), None)
        if alt:
            slots[slot] = alt
            fixed.append(f"{slot}←{alt}")
    return fixed


def cmd_mcp(a, cfg):
    from .mcp_server import serve
    serve(a.book)


def cmd_serve(a, cfg):
    from .server import main as serve_main
    serve_main((["--config", a.config] if a.config else []) + (["--home", a.home] if a.home else []))


def cmd_ask(a, cfg):
    app = App(Path(a.book), cfg, a.verbose, a.protocol)
    app.run_agent(a.prompt)
    console.print(f"[dim]{app.llm.usage.summary()}[/dim]")


COMMANDS = {"new", "run", "continue", "status", "stop", "audit", "export", "estimate", "profiles", "models",
            "doctor", "setup", "mcp", "serve", "ask", "chat", "integrate", "clean", "edit", "cast"}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hanru", description="Hanru — агентный переводчик книг",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Сценарии: docs/SCENARIOS.md. Без команды: hanru <папка книги> — диалог с агентом.")
    ap.add_argument("--config", help="путь к hanru.json")
    sub = ap.add_subparsers(dest="cmd")

    def book_arg(p):
        p.add_argument("book", help="папка книги")

    p = sub.add_parser("new", help="создать книгу из EPUB / TXT / папки с главами")
    p.add_argument("source")
    p.add_argument("--to", help="библиотека (папка, где создать книгу)")
    p.add_argument("--title")
    p.add_argument("--fix-breaks", action="store_true", help="для папки: склеить разорванные абзацы (с резервной копией)")
    for name in ("run", "continue"):
        p = sub.add_parser(name, help="перевести книгу (без агента)" if name == "run" else "доделать прерванный перевод")
        book_arg(p)
        p.add_argument("-c", "--chapters", help="диапазон глав: 1-50,60")
        p.add_argument("--profile", choices=list(PROFILES))
        p.add_argument("--background", action="store_true", help="запустить в фоне и сразу вернуться")
        p.add_argument("--export", choices=["epub", "fb2", "txt", "all"], help="собрать книгу после перевода")
        p.add_argument("--wave", type=int, help="глав в волне (по умолчанию 25)")
        p.add_argument("--no-review", action="store_true")
        p.add_argument("--retranslate", action="store_true", help="перевести заново уже готовые главы")
        p.add_argument("--no-prepare", action="store_true", help="не готовить глоссарий/библию автоматически")
        p.add_argument("-v", "--verbose", action="store_true")
    p = sub.add_parser("status", help="состояние книги и фоновой задачи")
    book_arg(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--watch", action="store_true", help="следить до окончания")
    p = sub.add_parser("stop", help="мягко остановить фоновый перевод")
    book_arg(p)
    p = sub.add_parser("audit", help="проверить перевод (свой или чужой) без LLM; --fix — починить")
    book_arg(p)
    p.add_argument("-c", "--chapters")
    p.add_argument("--fix", action="store_true")
    p = sub.add_parser("export", help="собрать книгу: EPUB / FB2 / TXT")
    book_arg(p)
    p.add_argument("--format", default="epub", choices=["epub", "fb2", "txt", "all"])
    p.add_argument("-o", "--out")
    p.add_argument("-c", "--chapters")
    p.add_argument("--title")
    p.add_argument("--author")
    p = sub.add_parser("clean", help="убрать мусор: служебные строки сайтов, заметки и благодарности автора")
    book_arg(p)
    p.add_argument("--apply", action="store_true", help="применить (без флага — только предпросмотр)")
    p.add_argument("--target", default="translation", choices=["translation", "source", "both"])
    p.add_argument("--drop", action="append", help="удалять эту строку (точно)")
    p.add_argument("--drop-regex", action="append", help="удалять строки по регулярному выражению")
    p = sub.add_parser("cast", help="персонажи и их пол (для редактора рода)")
    book_arg(p)
    p.add_argument("--set", action="append", help='"Имя=f:кто это" или "Имя=m"')
    p.add_argument("--suggest", action="store_true", help="показать персонажей и статистику рода по тексту")
    p = sub.add_parser("edit", help="редактура готового перевода: --genders — исправить род персонажей")
    book_arg(p)
    p.add_argument("--genders", action="store_true", required=True)
    p.add_argument("--names", help="только главы с этими персонажами (через запятую), заново")
    p.add_argument("--redo", action="store_true", help="заново и уже отредактированные главы")
    p.add_argument("-c", "--chapters")
    p.add_argument("--profile", choices=list(PROFILES))
    p.add_argument("--background", action="store_true")
    p = sub.add_parser("estimate", help="оценка токенов и времени по профилям")
    book_arg(p)
    p.add_argument("-c", "--chapters")
    p = sub.add_parser("profiles", help="профили; --set — выбрать")
    p.add_argument("--set")
    p.add_argument("--book", help="задать профиль только этой книге")
    p = sub.add_parser("models", help="модели провайдера")
    p.add_argument("provider", nargs="?")
    sub.add_parser("doctor", help="проверить ключи, связь и модели")
    p = sub.add_parser("setup", help="мастер настройки (или флагами без вопросов)")
    p.add_argument("--provider")
    p.add_argument("--key")
    p.add_argument("--base-url")
    for slot in SLOTS:
        p.add_argument(f"--{slot}", help=f"модель слота {slot}")
    p.add_argument("--profile", choices=list(PROFILES))
    p = sub.add_parser("integrate", help="подключить к агенту: integrate hermes — установить скил")
    p.add_argument("target", choices=["hermes"])
    p.add_argument("--hermes-home")
    p.add_argument("--category", default="productivity")
    p.add_argument("--library", help="папка-библиотека книг для Hermes")
    p = sub.add_parser("serve", help="JSON-RPC API по stdin/stdout для приложений (docs/INTEGRATION.md)")
    p.add_argument("--home", help="рабочая папка: библиотека и черновики")
    p = sub.add_parser("mcp", help="MCP-сервер (stdio) для Hermes / Claude Code")
    book_arg(p)
    for name in ("ask", "chat"):
        p = sub.add_parser(name, help="задача агенту одной фразой" if name == "ask" else "диалог с агентом")
        book_arg(p)
        if name == "ask":
            p.add_argument("prompt")
        p.add_argument("--protocol", choices=["native", "text", "auto"])
        p.add_argument("-v", "--verbose", action="store_true")
    return ap


def main(argv=None):
    if os.name == "nt":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    first, skip = None, False
    for x in argv:  # первое позиционное слово, не считая значения глобального --config
        if skip:
            skip = False
        elif x == "--config":
            skip = True
        elif not x.startswith("-"):
            first = x
            break
    if first is None and any(x in ("-h", "--help") for x in argv):
        build_parser().print_help()
        return
    if first not in COMMANDS:
        return legacy_main(argv)
    a = build_parser().parse_args(argv)
    cfg = Config.load(a.config)
    handlers = {"new": cmd_new, "run": cmd_run, "continue": lambda a, c: cmd_run(a, c, continuing=True),
                "status": cmd_status, "stop": cmd_stop, "audit": cmd_audit, "export": cmd_export,
                "clean": cmd_clean, "edit": cmd_edit, "cast": cmd_cast,
                "estimate": cmd_estimate, "profiles": cmd_profiles, "models": cmd_models, "doctor": cmd_doctor,
                "setup": cmd_setup, "mcp": cmd_mcp, "serve": cmd_serve, "ask": cmd_ask, "integrate": cmd_integrate,
                "chat": lambda a, c: App(Path(a.book), c, a.verbose, a.protocol).repl()}
    try:
        handlers[a.cmd](a, cfg)
    except (ValueError, KeyError, FileNotFoundError) as e:
        console.print(f"[red]{type(e).__name__}: {e}[/red]")
        sys.exit(1)


def legacy_main(argv):
    """hanru [папка] [-p задача] — как раньше: диалог с агентом."""
    ap = argparse.ArgumentParser(prog="hanru", description="Hanru — агентный переводчик книг. "
                                 "Команды: new run continue status stop audit export estimate profiles models "
                                 "doctor setup mcp ask chat (hanru <команда> -h)")
    ap.add_argument("book", nargs="?", default=".", help="папка книги (по умолчанию текущая)")
    ap.add_argument("-p", "--prompt", help="выполнить одну задачу и выйти")
    ap.add_argument("--agent-model")
    ap.add_argument("--translator")
    ap.add_argument("--reviewer")
    ap.add_argument("--profile", choices=list(PROFILES))
    ap.add_argument("--protocol", choices=["native", "text", "auto"])
    ap.add_argument("--config")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    cfg = Config.load(a.config)
    if a.profile:
        cfg = cfg.with_overrides(profile=a.profile)
    roles = {k: v for k, v in (("agent", a.agent_model), ("translator", a.translator.split(",") if a.translator else None),
                               ("reviewer", a.reviewer)) if v}
    if roles:
        cfg = cfg.with_overrides(roles=roles)
    book = Path(a.book)
    if not a.prompt and not is_book(book) and not Workspace(book).chapters():
        console.print(Panel.fit(
            "[bold]Hanru[/bold] — агентный переводчик книг\n\n"
            "Быстрый старт:\n  hanru setup                     — выбрать провайдера и модели\n"
            "  hanru new книга.epub            — создать книгу из EPUB/TXT/папки глав\n"
            "  hanru run <папка книги>         — перевести (без агента)\n"
            "  hanru <папка книги>             — диалог с агентом\n"
            "  hanru -h                        — все команды", border_style="cyan"))
        return
    app = App(book, cfg, a.verbose, a.protocol)
    if a.prompt:
        app.run_agent(a.prompt)
        console.print(f"[dim]{app.llm.usage.summary()}[/dim]")
    else:
        app.repl()


if __name__ == "__main__":
    main()
