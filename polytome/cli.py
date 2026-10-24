"""Командная строка Polytome.

Два способа работы:
1. Готовые сценарии (без агента, предсказуемо по токенам):
     polytome new книга.epub → polytome run <книга> → polytome status / stop / continue → polytome export <книга>
2. Диалог с агентом (как Claude Code): polytome <книга>  или  polytome ask <книга> "задача"
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

from .i18n import _
from .agent import Agent
from .config import PKG_DIR, PROFILES, PROVIDER_CATALOG, ROLES, SLOTS, Config
from .engine import Engine
from .llm import LLMClient, LLMError
from .tools import Toolbox
from .workspace import Workspace, is_book, parse_range

console = Console(highlight=False, soft_wrap=bool(os.environ.get("POLYTOME_PLAIN")))

HELP = _("[bold]Пишите задачу обычным языком[/bold], например:\n  «подготовь книгу и переведи главы "
         "1-5», «проверь имена в главах 1-20», «почему в гл.3 абзац 12 так "
         "переведён?»\n\n[bold]Готовые действия (без агента)[/bold]\n  /run [главы]         перевести "
         "(по умолчанию — все непереведённые)   /continue   доделать прерванное\n  /prepare          "
         "   глоссарий + библия для новой книги               /audit [главы]   проверка без LLM\n  "
         "/export [epub|fb2|txt]  собрать книгу                                 /estimate [главы]  "
         "оценка токенов/времени\n  /profile [имя]       quality | balanced | economy | local        "
         "     /status   состояние книги\n[bold]Книга и модели[/bold]\n  /glossary [фильтр]  /bible  "
         "/check <главы>  /model [роль] [провайдер:модель]  /models [провайдер]\n  /tool <имя> "
         "{json}  /tools  /protocol native|text|auto  /cost  /compact  /clear  /resume  /book "
         "<папка>  /quit\nCtrl+C — остановить (текущие батчи допишутся и сохранятся); второй Ctrl+C "
         "— выйти сразу.")


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
            return _short(first[0], 90) + (_(" … ({0} строк)").format(len(first)) if len(first) > 1 else "")
        if isinstance(d, dict) and "error" in d:
            return _("[red]ошибка: {0}[/red]").format(_short(d['error'], 100))
        if name == "translate" and isinstance(d, dict):
            return (_("переведено {0}; ошибок {1}; правок рецензента {2}; hard {3}").format(d.get('translated') or '—', len(d.get('failed', [])), d.get('review_fixes_total', 0), d.get('hard_issues_total', 0)))
        return _short(d, 110)

    def status(self, text: str):
        console.print(f"[yellow]⚑ {text}[/yellow]")

    def event(self, e: dict):
        ev = e.get("ev")
        if ev == "chapter_done":
            r = e["report"]
            mark = "[green]✓[/green]" if r.get("complete") else "[red]✗[/red]"
            console.print(_("    {0} гл.{1}: {2} абз., рецензент {3}, починок {4}, hard {5}, {6}s").format(mark, r['chapter'], r.get('segments', '?'), r.get('review_fixes', 0), r.get('repairs', 0), r.get('hard_issues', 0), r.get('seconds', '?')))
        elif ev == "prepare_start":
            console.print(_("подготовка книги: имена, термины, библия…"))
        elif ev == "prepare_done":
            p = e["report"]
            console.print(_("  глоссарий: {0} записей, библия: {1}").format(p['glossary_total'], _("написана") if p['bible_written'] else _("была"))
                          + (_(", [yellow]конфликты пола: {0}[/yellow]").format(len(p['gender_conflicts'])) if p["gender_conflicts"] else ""))
        elif ev == "extract_start":
            console.print(_("    [dim]поиск новых имён/терминов в {0} гл.…[/dim]").format(e['chapters']))
        elif ev == "extract_done":
            console.print(_("    [dim]имён добавлено: {0}, терминов-кандидатов: {1}[/dim]").format(e['added'], e.get('pending', 0)))
        elif ev == "repair":
            console.print(_("    [dim]⟲ гл.{0} [{1}] {2}[/dim]").format(e['n'], e['seg'], _short(e['why'], 70)))
        elif ev == "resume":
            console.print(_("    [cyan]↻ гл.{0}: продолжаю с места остановки ({1}/{2} абз., стадия {3})[/cyan]").format(e['n'], e['done'], e['total'], e['stage']))
        elif ev == "chapter_cancelled":
            console.print(_("    [yellow]⏸ гл.{0}: остановлено, сохранено {1}/{2} абз.[/yellow]").format(e['n'], e['done'], e['total']))
        elif ev == "edit_done":
            r = e["report"]
            console.print(_("    {0} гл.{1}: исправлено абзацев {2}").format('[red]✗[/red]' if r.get('error') else '[green]✓[/green]', r['chapter'], r.get('fixed', 0)) + (f" ({r['error'][:60]})" if r.get("error") else ""))
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
        console.print(_("[yellow]⏹ останавливаю: текущие батчи допишутся и сохранятся (обычно секунды, до ~1 мин). "
                        "Второй Ctrl+C — выйти сразу (потеряется только недописанный батч).[/yellow]"))
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
            console.print(_("[red]Ошибка: {0}[/red]").format(e))
        console.print(_("[dim]— {0:.0f}s, {1:,} ток.[/dim]").format(time.time() - t0, self.llm.usage.total() - tok0))

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
                    console.print(_("профили: {0}").format(', '.join(PROFILES)))
                else:
                    self.ws.meta["profile"] = args[0]
                    self.ws.save_meta()
                    self.open_book(self.ws.root)
                    console.print(_("профиль книги: {0} — {1}").format(args[0], PROFILES[args[0]]['desc']))
            else:
                print_profiles(self.engine.cfg)
        elif cmd == "/glossary":
            d = json.loads(self.tb.call("glossary_list", {"filter": args[0] if args else "", "limit": 500}))
            t = Table(_("оригинал"), _("перевод"), _("тип"), _("пол"), _("статус"), _("заметка"), show_lines=False)
            for e in d["entries"]:
                t.add_row(e["source"], e["target"], e.get("kind", ""), e.get("gender", ""), e.get("status", ""),
                          _short(e.get("note", ""), 40))
            console.print(t)
            console.print(_("[dim]всего {0}[/dim]").format(d['total']))
        elif cmd == "/bible":
            console.print(Markdown(self.ws.bible() or _("(пусто)")))
        elif cmd == "/check":
            console.print_json(self.tb.call("check", {"chapters": args[0] if args else "all"}))
        elif cmd == "/tools":
            for t in self.tb.tools.values():
                console.print(f"[bold]{t.name}[/bold] — {t.description}")
        elif cmd == "/tool":
            if not args:
                console.print(_("использование: /tool <имя> {json}"))
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
                console.print(_("{0} → {1} (сохранено в {2})").format(role, spec, self.cfg.path))
            else:
                print_config(self.engine.cfg)
        elif cmd == "/models":
            list_models(self.cfg, self.llm, args[0] if args else None)
        elif cmd == "/protocol":
            if args:
                self.agent.switch_protocol(args[0])
            console.print(_("протокол: {0}").format(self.agent.protocol))
        elif cmd == "/continue":
            job = self.ws.interrupted_job()
            if not job:
                console.print(_("прерванных задач нет"))
            else:
                console.print(_("продолжаю: осталось {0} гл. (начатых: {1})").format(len(job['remaining']), len(job['partial'])))
                self.run_tool("translate", {"chapters": ",".join(map(str, job["chapters"])),
                                            "retranslate": job.get("retranslate", False),
                                            "review": job.get("review", True)})
        elif cmd == "/cost":
            console.print(self.llm.usage.summary())
        elif cmd == "/compact":
            self.agent.compact(force=True)
        elif cmd == "/clear":
            self.agent = Agent(self.tb, self.cfg, self.llm, self.ui, self.protocol)
            console.print(_("новый диалог"))
        elif cmd == "/resume":
            sess = sorted((self.ws.meta_dir / "sessions").glob("*.json"))
            if sess:
                self.agent.load(sess[-1])
                console.print(_("продолжаю {0} ({1} сообщений)").format(sess[-1].name, len(self.agent.messages)))
            else:
                console.print(_("сохранённых диалогов нет"))
        elif cmd == "/book":
            if args:
                self.open_book(Path(" ".join(args)))
            console.print(_("книга: {0}").format(self.ws.root))
        else:
            console.print(_("неизвестная команда {0}; /help").format(cmd))
        return True

    def interrupted_banner(self) -> str:
        job = self.ws.interrupted_job()
        if not job:
            return ""
        why = _("процесс прервался (сбой или выключение)") if job["status"] == "running" else _("остановлена")
        return (_("\n[yellow]⚠ Незавершённая задача перевода ({0}, {1}): главы {2}–{3}, готово {4}, осталось "
                  "{5}. Продолжить: /continue[/yellow]").format(why, job.get('updated_at', ''), job['chapters'][0], job['chapters'][-1], len(job.get('done', [])), len(job['remaining'])))

    def repl(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        session = PromptSession(history=FileHistory(str(self.cfg.base_dir / ".polytome_history")))
        st = self.ws.status()
        c = self.engine.cfg
        console.print(Panel.fit(
            _("[bold]Polytome[/bold] — агентный переводчик книг\nкнига: [cyan]{0}[/cyan]  глав {1}, "
              "переведено {2}\nпрофиль: [bold]{3}[/bold]   переводчик: {4}   агент: {5}\n/help — команды").format(st['title'], st['chapters_total'], st['chapters_translated'], c.profile_name, c.role_spec('translator'), c.role_spec('agent')) + self.interrupted_banner(), border_style="cyan"))
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
    t = Table(_("роль"), _("модель"), title=_("профиль: {0} — {1}").format(s['profile'], s['profile_desc']))
    for r, spec in s["roles"].items():
        t.add_row(r, spec)
    console.print(t)
    console.print(_("[dim]движок: {0} · конфиг: {1}[/dim]").format(s['engine'], s['config_file']))


def print_profiles(cfg: Config):
    t = Table(_("профиль"), _("переводчик"), _("рецензент"), _("что это"))
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
            console.print(_("[bold]{0}[/bold]: [red]недоступен ({1})[/red]").format(p, _short(str(e), 80)))


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
    ws, rep = create_book(Path(a.source), Path(a.library) if a.library else None, a.title, fix_breaks=a.fix_breaks,
                          source_lang=a.from_lang, target_lang=a.to or cfg.target_lang)
    console.print(Panel.fit(
        _("[bold]Книга создана:[/bold] {0}\nглав: {1} · язык: {2} → {3} · {4}").format(ws.root, rep['chapters'], rep['lang'], ws.meta['target_lang'], rep.get('split', '')) + (_("\nавтор: {0}").format(rep['author']) if rep.get("author") else "")
        + (_("\nобложка: да") if rep.get("cover") else "")
        + (_("\nсклеено разорванных абзацев: {0}").format(rep['line_breaks_fixed']) if rep.get("line_breaks_fixed") else ""),
        border_style="green"))
    if rep.get("line_breaks") and not rep.get("line_breaks_fixed"):
        console.print(_("[yellow]⚠ В оригинале {0} абзацев разорваны посреди фразы (жёсткие переносы) — переводчик "
                        "на них сбивается. Исправить (с резервной копией): polytome new \"{1}\" --fix-breaks[/yellow]").format(rep['line_breaks'], a.source))
    if rep["lang"] == ws.meta["target_lang"]:
        console.print(_("[yellow]⚠ Текст уже на языке перевода ({0}) — переводить нечего (можно проверить/собрать: "
                        "audit, export). Другой язык: --to <код>.[/yellow]").format(rep['lang']))
    eng = Engine(ws, cfg, LLMClient(1))
    est = eng.estimate(list(ws.chapters()))
    console.print(_("оценка ({0}): ~{1:,} токенов, ~{2} мин").format(est['profile'], est['tokens_estimate'], est['time_estimate_min']))
    console.print(_("\nДальше:\n  polytome run \"{0}\"              — перевести всё\n  polytome run \"{1}\" -c 1-10   "
                    "   — сначала 10 глав на пробу\n  polytome \"{2}\"                  — открыть диалог с агентом").format(ws.root, ws.root, ws.root))


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
            console.print(_("прерванных задач нет — всё доделано"))
            return
        if job.get("kind") == "edit":
            console.print(_("продолжаю редактуру ({0}): осталось {1} гл.").format(job.get('task'), len(job['remaining'])))
            r = run_interruptible(lambda: eng.edit_chapters(job["chapters"], job.get("task", "genders")), eng)
            if r:
                console.print(_("исправлено абзацев: {0} · {1}").format(r['paragraphs_fixed'], r['tokens']))
            return
        chapters, retr, review = job["chapters"], job.get("retranslate", False), job.get("review", True)
        console.print(_("продолжаю задачу {0}: осталось {1} гл.").format(job['id'], len(job['remaining'])))
    else:
        chapters = parse_range(a.chapters or "all", list(ws.chapters()))
        retr, review = a.retranslate, not a.no_review
    if not chapters:
        console.print(_("[red]нет глав для перевода (проверьте папку оригинала и диапазон)[/red]"))
        return
    console.print(_("[bold]{0}[/bold] · профиль {1} · переводчик {2}").format(ws.meta['title'], eng.cfg.profile_name, eng.cfg.role_spec('translator')))
    est = eng.estimate(chapters)
    if est["chapters"]:
        console.print(_("[dim]к переводу {0} гл., оценка ~{1:,} ток., ~{2} мин[/dim]").format(est['chapters'], est['tokens_estimate'], est['time_estimate_min']))
    t0 = time.time()
    r = run_interruptible(lambda: eng.run(chapters, retranslate=retr, review=review, wave=a.wave,
                                          prepare=not continuing and not a.no_prepare), eng)
    if r is None:
        return
    console.print(Panel.fit(
        _("переведено: {0}\nпропущено (готовы): {1}\nошибок: {2} · hard-проблем: {3} · правок "
          "рецензента: {4}\n{5:.0f} с · {6}").format(r['translated'] or '—', r['skipped'] or '—', r['failed'] or 0, r['hard_issues_total'], r['review_fixes_total'], time.time() - t0, r['tokens'])
        + (f"\n[yellow]{r['note']}[/yellow]" if r.get("note") else "")
        + (f"\n[red]{r['HALTED']}[/red]" if r.get("HALTED") else "")
        + (_("\n[yellow]⚠ конфликты пола: {0}[/yellow]").format([i['target'] for i in r['GENDER_CONFLICTS']['items']])
           if r.get("GENDER_CONFLICTS") else ""), title=_("итог"), border_style="green"))
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
                console.print(_("{0} {1}: готово {2}/{3}, в работе {4}, ETA {5} мин, токенов {6}").format(time.strftime('%H:%M:%S'), j.get('state'), j.get('done'), (j.get('done') or 0) + (j.get('left') or 0), j.get('in_progress'), j.get('eta_min'), j.get('tokens_this_run')))
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
    console.print(_("[bold]{0}[/bold]: переведено {1} из {2} глав").format(s['book'], s['chapters_translated'], s['chapters_total']))
    if j:
        color = {"running": "cyan", "done": "green", "interrupted": "yellow", "cancelled": "yellow"}.get(j["state"], "red")
        bits = [_("главы {0}").format(j['chapters'])] + [f"{label} {j[k]}" for k, label in (
            ("done", _("готово")), ("left", _("осталось")), ("in_progress", _("в работе")), ("eta_min", _("ETA, мин")),
            ("tokens_this_run", _("токенов")), ("failed", _("ошибки"))) if j.get(k) not in (None, [], 0)]
        console.print(_("задача [{0}]{1}[/{2}]").format(color, j['state'], color) + (f" ({j['profile']})" if j.get("profile") else "")
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
    console.print(_("[bold]Аудит[/bold]: глав проверено {0}, с hard-ошибками {1}, всего hard {2}").format(len(rep), len(hard), total))
    for n, r in list(hard.items())[:15]:
        first = next(iter(r["issues"].values()), [""])[0]
        console.print(_("  гл.{0}: {1} — {2}").format(n, r['hard'], _short(first, 110)))
    if names:
        console.print(_("[yellow]варианты имён по книге: {0}[/yellow] ").format(len(names)) + "; ".join(
            f"{x['target']}: {', '.join(list(x['suspicious'])[:3])}" for x in names[:6]))
    if genders.get("conflicts"):
        console.print(_("[yellow]конфликты пола: {0}[/yellow]").format([c['target'] for c in genders['conflicts']]))
    if a.fix:
        norm = sum(eng.normalize_chapter(int(n)) for n in rep)
        if norm:
            console.print(_("без LLM нормализовано абзацев (числа цифрами, пунктуация): {0}").format(norm))
    if a.fix and hard:
        console.print(_("чиню {0} глав…").format(len(hard)))

        def fix_all():
            return [eng.repair_chapter(int(n)) for n in hard]
        res = run_interruptible(fix_all, eng) or []
        console.print(_("hard до: {0} → после: {1}").format(sum(r['hard_before'] for r in res), sum(r['hard_after'] for r in res)))
    elif hard:
        console.print(_("исправить автоматически: polytome audit \"{0}\" --fix").format(a.book))


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
        console.print(_("[bold]{0} — {1}[/bold]: глав {2} из {3}, абзацев {4}, хвостов с заметками автора {5}").format(_("Очищено") if a.apply else _("Найдено (предпросмотр)"), _("оригинал") if t == 'source' else _("перевод"), rep['chapters_affected'], rep['chapters'], rep['paragraphs_to_remove'], rep['author_tails']))
        for r in rep["repeated_lines"][:5]:
            console.print(_("  служебная строка в {0} гл.: [dim]{1}[/dim]").format(r['chapters'], _short(r['line'], 90)))
        for e in rep["examples"][:8]:
            console.print(f"  [dim]{_short(e, 120)}[/dim]")
        if a.apply and rep.get("backup"):
            console.print(_("  резервная копия: {0}").format(rep['backup']))
    if not a.apply:
        console.print(_("Применить: polytome clean \"{0}\" --apply").format(a.book)
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
            console.print(_("[red]пол для {0}: m или f[/red]").format(name))
            continue
        src = next((e["source"] for e in gl.entries if e.get("target") == name.strip()), name.strip())
        gl.upsert(src, name.strip(), "person", g, note or None, status="confirmed")
        console.print(f"{name.strip()} → {_("мужчина") if g == 'm' else _("женщина")}")
    if a.set:
        gl.save()
    eng = Engine(ws, cfg, LLMClient(1))
    console.print(eng.cast_table() or _("[dim](таблица пуста)[/dim]"))
    if a.suggest:
        from .checks import find_chinese_names, gender_votes
        from collections import Counter
        names, votes = Counter(), {}
        texts = [ws.target_path(n).read_text(encoding="utf-8-sig") for n in ws.chapters() if ws.is_translated(n)]
        for t in texts:
            names.update(find_chinese_names(t))
        top = [n for n, c in names.most_common(60) if c >= 20]
        for t in texts:
            for nm, g, _w in gender_votes(t, top):
                votes.setdefault(nm, Counter())[g] += 1
        tb = Table(_("персонаж"), _("упоминаний"), _("муж"), _("жен"), _("в таблице"))
        known = {e["target"] for e in gl.entries if e.get("gender")}
        for nm in top:
            v = votes.get(nm, Counter())
            tb.add_row(nm, str(names[nm]), str(v["masc"]), str(v["femn"]), "✓" if nm in known else "")
        console.print(tb)
        console.print(_("[dim]Машинный перевод с китайского ставит мужской род по умолчанию: заметная доля «жен» у "
                        "персонажа — признак, что это женщина. Задать: polytome cast <книга> --set \"Имя=f:кто "
                        "это\"[/dim]"))


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
        console.print(_("[red]Сначала задайте пол персонажей: polytome cast <книга> --suggest / --set \"Имя=f\"[/red]"))
        return
    chs = parse_range(a.chapters or "all", list(eng.ws.chapters()))
    redo = a.redo
    if a.names:  # только главы с этими персонажами (после уточнения таблицы) — заново
        names = [x.strip() for x in a.names.split(",") if x.strip()]
        chs = [n for n in chs if eng.ws.is_translated(n)
               and any(nm in eng.ws.target_path(n).read_text(encoding="utf-8-sig") for nm in names)]
        redo = True
    console.print(_("редактура рода: {0} гл., редактор {1}").format(len(chs), eng.cfg.role_spec('reviewer')))
    r = run_interruptible(lambda: eng.edit_chapters(chs, redo=redo), eng)
    if r:
        console.print(Panel.fit(_("отредактировано: {0}\nисправлено абзацев: {1}\nпропущено (уже): {2}\n{3} с · {4}").format(r['edited'] or '—', r['paragraphs_fixed'], r['skipped'] or '—', r['seconds'], r['tokens']),
                                title=_("итог"), border_style="green"))


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
        console.print(_("[green]✓[/green] {0}: {1} — {2} гл., {3} КБ").format(r['format'].upper(), r['path'], r['chapters'], r['bytes'] // 1024)
                      + (_(" [yellow](нет перевода для {0} гл.)[/yellow]").format(r['missing_total']) if r["missing_total"] else ""))


def cmd_estimate(a, cfg):
    eng, tb, ui = _engine(Path(a.book), cfg)
    chs = parse_range(a.chapters or "all", list(eng.ws.chapters()))
    t = Table(_("профиль"), _("глав"), _("токенов (оценка)"), _("на главу"), _("время, мин"))
    for name in PROFILES:
        e = Engine(eng.ws, cfg.with_overrides(profile=name), eng.llm).estimate(chs)
        t.add_row(name + (" ◀" if name == eng.cfg.profile_name else ""), str(e["chapters"]),
                  f"{e['tokens_estimate']:,}", f"{e['tokens_per_chapter']:,}", str(e["time_estimate_min"]))
    console.print(t)
    console.print(_("[dim]по замерам на китайских веб-новеллах; фактические цифры — в polytome status во время "
                    "работы[/dim]"))


def cmd_profiles(a, cfg):
    if a.set:
        if a.set not in PROFILES:
            console.print(_("[red]нет профиля {0}[/red]; есть: {1}").format(a.set, ', '.join(PROFILES)))
            return
        if a.book:
            ws = Workspace(Path(a.book))
            ws.meta["profile"] = a.set
            ws.save_meta()
            console.print(_("профиль книги «{0}»: {1}").format(ws.meta['title'], a.set))
        else:
            cfg.data["profile"] = a.set
            cfg.save()
            console.print(_("профиль по умолчанию: {0}").format(a.set))
        return
    print_profiles(cfg)


def cmd_bench(a, cfg):
    from . import bench
    results = Path(a.results)
    if a.leaderboard:
        md = bench.leaderboard(bench.load(results))
        out = results.parent / "LEADERBOARD.md"
        out.write_text(md, encoding="utf-8")
        console.print(_("таблица: {0} ({1} результатов)").format(out, len(bench.load(results))))
        return
    models = a.model or [cfg.role_specs("translator")[0][0]]
    pairs = a.pair or ["zh-ru"]
    if "all" in pairs:
        from .bench_suite import PAIRS
        pairs = list(PAIRS)
    for model in models:
        for pair in pairs:
            console.print(f"[bold]{model}[/bold] · {pair}…")

            def show(e):
                if e.get("ev") == "bench_progress":
                    console.print(_("  гл.{0}/{1}: {2}").format(e["chapter"], e["chapters"], e["stage"]))

            rec = bench.run(cfg, model, pair, cfg.base_dir or Path.cwd(), on_event=show, keep=a.keep,
                            budget=a.budget or None)
            if rec.get("error") == "budget":
                console.print(_("  [yellow]не уложилась в лимит {0:,} токенов[/yellow] — для перевода книг слишком "
                                "расточительна").format(a.budget))
                if not a.no_save:
                    console.print(_("  сохранено: {0}").format(bench.save(rec, results)))
                continue
            if rec.get("error"):
                console.print(_("  [red]ошибка:[/red] {0}").format(rec["error"]))
                continue
            cost = f", ${rec['cost_usd']:.4f}" if rec.get("cost_usd") is not None else ""
            console.print(_("  score {0} · сразу {1}% · после починки {2}% · {3} с · {4:,} ток.{5}").format(
                rec["score"], rec["first_pass_ok"], rec["final_ok"], rec["seconds"],
                rec["tokens"]["prompt"] + rec["tokens"]["completion"], cost))
            if rec["issues_final"]:
                console.print(_("  осталось: {0}").format(", ".join(f"{k} ×{v}" for k, v in rec["issues_final"].items())))
            if not a.no_save:
                console.print(_("  сохранено: {0}").format(bench.save(rec, results)))


def cmd_models(a, cfg):
    list_models(cfg, LLMClient(4), a.provider)


def cmd_doctor(a, cfg):
    """Проверка: ключи, связь, ответ каждой роли, tool-calling агента."""
    llm = LLMClient(4)
    console.print(_("конфиг: {0} · профиль: {1}").format(cfg.path, cfg.profile_name))
    t = Table(_("роль"), _("модель"), _("ключ"), _("ответ"), _("сек"), _("заметка"))
    seen = {}
    for role in ROLES:
        for ref in cfg.role(role):
            key = "✓" if ref.api_key or cfg.providers().get(ref.provider, {}).get("local") else _("[red]нет[/red]")
            sk = (ref.spec, role == "agent", str(ref.extra))
            if sk in seen:
                ok, sec, note = seen[sk]
            else:
                t0 = time.time()
                try:
                    tools = [{"type": "function", "function": {"name": "ping", "description": _("ответить"),
                                                                "parameters": {"type": "object", "properties": {}}}}]
                    res = llm.chat(ref, [{"role": "user", "content": _("Вызови инструмент ping.")}],
                                   tools=tools if role == "agent" else None, max_tokens=3000, role="doctor")
                    ok = "[green]ok[/green]"
                    note = ("tool-calling ✓" if res.tool_calls else _("без нативных tools → text-протокол")) \
                        if role == "agent" else ""
                except LLMError as e:
                    ok, note = _("[red]ошибка[/red]"), _short(str(e), 60)
                sec = f"{time.time() - t0:.1f}"
                seen[sk] = (ok, sec, note)
            t.add_row(role, ref.spec + (f"@{ref.extra.get('reasoning_effort')}" if ref.extra.get("reasoning_effort") else ""),
                      key, ok, sec, note)
    console.print(t)
    try:
        import pymorphy3  # noqa: F401
        console.print(_("морфология (проверка рода): [green]ok[/green]"))
    except ImportError:
        console.print(_("морфология: [yellow]нет pymorphy3 — проверка рода отключена[/yellow] (pip install "
                        "pymorphy3 pymorphy3-dicts-ru)"))


def cmd_setup(a, cfg):
    """Мастер: провайдеры → ключи → модели по слотам → профиль. Неинтерактивно — флагами."""
    llm = LLMClient(4)
    if a.provider or a.strong or a.profile or a.to:  # неинтерактивный режим (скрипты, Hermes)
        if a.to:
            from .lang import normalize
            cfg.data["target_lang"] = normalize(a.to)
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
            console.print(_("[dim]слоты без ключа заполнены из указанных: ") + ", ".join(fixed) + "[/dim]")
        print_config(Config.load(cfg.path))
        return
    console.print(Panel.fit(_("[bold]Настройка Polytome[/bold]\n1) провайдеры и ключи  2) модели  3) профиль\nEnter — "
                              "принять вариант в [скобках]"), border_style="cyan"))
    names = list(PROVIDER_CATALOG)
    t = Table("№", _("провайдер"), _("что это"), _("ключ"))
    for i, p in enumerate(names, 1):
        info = PROVIDER_CATALOG[p]
        has = _("локально") if info.get("local") else (_("[green]есть[/green]") if cfg.provider_key(p) else "—")
        t.add_row(str(i), p, info["title"], has)
    console.print(t)
    default = [str(i) for i, p in enumerate(names, 1) if cfg.provider_key(p)] or ["1"]
    pick = input(_("Какие провайдеры использовать (номера через запятую) [{0}]: ").format(','.join(default))).strip() or ",".join(default)
    chosen = [names[int(x) - 1] for x in pick.replace(" ", "").split(",") if x.isdigit() and 0 < int(x) <= len(names)]
    available: dict[str, list[str]] = {}
    for p in chosen:
        info = PROVIDER_CATALOG[p]
        if not info.get("local") and not cfg.provider_key(p):
            console.print(_("Ключ для {0}").format(info['title']) + (_(" — получить: {0}").format(info['keys_url']) if info.get("keys_url") else ""))
            key = getpass.getpass(_("  вставьте ключ (не отображается): ")).strip()
            if key:
                cfg.set_secret(p, key)
        try:
            available[p] = llm.list_models(cfg.resolve(f"{p}:x"))
            console.print(_("  [green]✓[/green] {0}: доступно моделей {1}").format(p, len(available[p])))
        except Exception as e:  # noqa: BLE001
            console.print(f"  [red]✗[/red] {p}: {_short(str(e), 90)}")
    slots = cfg.data.setdefault("slots", {})
    hints = {"strong": _("основной переводчик (качество)"), "fast": _("быстрая/дешёвая (профиль economy)"),
             "agent": _("ведёт диалог, вызывает инструменты"), "reviewer": _("рецензент (лучше другая модель, чем strong)"),
             "extractor": _("ищет имена и термины (дешёвая)"), "local": _("модель на вашем ПК (профиль local)")}
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
    prof = input(_("Профиль [{0}]: ").format(cfg.profile_name)).strip() or cfg.profile_name
    if prof in PROFILES:
        cfg.data["profile"] = prof
    path = cfg.save()
    console.print(_("[green]Сохранено:[/green] {0} (ключи — в {1})").format(path, cfg.secrets_path.name))
    console.print(_("Проверить: polytome doctor"))


def hermes_home() -> Path:
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"])
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        p = Path(os.environ["LOCALAPPDATA"]) / "hermes"
        if p.is_dir():
            return p
    return Path.home() / ".hermes"


def cmd_integrate(a, cfg):
    """Установить скил polytome-translator в Hermes (копия папки + настройка пути к polytome)."""
    import shutil
    import subprocess
    src = PKG_DIR / "integrations" / "hermes" / "polytome-translator"
    home = Path(a.hermes_home) if a.hermes_home else hermes_home()
    if not home.is_dir():
        console.print(_("[red]Hermes не найден в {0}[/red] — укажите --hermes-home").format(home))
        return
    dst = home / "skills" / a.category / "polytome-translator"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    console.print(_("[green]✓[/green] скил установлен: {0}").format(dst))
    for old in [*(home / "skills").glob("*/codek-translator"), *(home / "skills").glob("*/hanru-translator")]:
        shutil.rmtree(old, ignore_errors=True)  # скил под прежним именем проекта
        console.print(_("[green]✓[/green] удалён старый скил: {0}").format(old))
    exe = Path(sys.executable).parent / ("polytome.exe" if os.name == "nt" else "polytome")
    command = str(exe) if exe.exists() else f'"{sys.executable}" -m polytome'
    library = a.library or str(Path.home() / "Books" / "Polytome")
    hermes = shutil.which("hermes") or next((str(x) for x in (home / "bin" / "hermes.exe", home / "bin" / "hermes")
                                             if x.exists()), None)
    if hermes:
        for key, val in (("skills.config.polytome.command", command), ("skills.config.polytome.library", library)):
            r = subprocess.run([hermes, "config", "set", key, val], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            console.print(("[green]✓[/green] " if r.returncode == 0 else "[yellow]?[/yellow] ") + f"{key} = {val}"
                          + ("" if r.returncode == 0 else f" ({_short(r.stderr or r.stdout, 120)})"))
    else:
        console.print(_("Настройте в Hermes: hermes config set skills.config.polytome.command \"{0}\"").format(command))
    console.print(_("Новые скилы видны в новых сессиях Hermes. Пример: «переведи книгу D:\\книга.epub»"))


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


def cmd_languages(a, cfg):
    from .lang import LANGS, Pair
    t = Table(_("код"), _("язык"), _("письмо"), _("имена {0}→…").format(a.from_lang), _("проверки рода/чисел"))
    for code, lg in LANGS.items():
        pr = Pair.of(a.from_lang, code)
        t.add_row(code + (" ◀" if code == cfg.target_lang else ""), f"{lg.native} ({lg.name})", lg.script,
                  pr.name_system or _("модель"), _("да") if pr.ru_target else "—")
    console.print(t)
    console.print(_("[dim]Перевод работает для любой пары; язык книги: polytome new <файл> --to <код>. Любой "
                    "другой код ISO 639-1 тоже можно указать — модель получит его как название языка.[/dim]"))


def cmd_serve(a, cfg):
    from .server import main as serve_main
    serve_main((["--config", a.config] if a.config else []) + (["--home", a.home] if a.home else []))


def cmd_ask(a, cfg):
    app = App(Path(a.book), cfg, a.verbose, a.protocol)
    app.run_agent(a.prompt)
    console.print(f"[dim]{app.llm.usage.summary()}[/dim]")


COMMANDS = {"new", "run", "continue", "status", "stop", "audit", "export", "estimate", "profiles", "models",
            "doctor", "setup", "mcp", "serve", "languages", "ask", "chat", "integrate", "clean", "edit", "cast", "bench"}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="polytome", description=_("Polytome — агентный переводчик книг"),
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=_("Сценарии: docs/SCENARIOS.ru.md. Без команды: polytome <папка книги> — диалог с агентом."))
    ap.add_argument("--config", help=_("путь к polytome.json"))
    sub = ap.add_subparsers(dest="cmd")

    def book_arg(p):
        p.add_argument("book", help=_("папка книги"))

    p = sub.add_parser("new", help=_("создать книгу из EPUB / TXT / папки с главами"))
    p.add_argument("source")
    p.add_argument("--to", help=_("язык перевода: en, ru, es, ja… (по умолчанию — из настроек или язык системы)"))
    p.add_argument("--from", dest="from_lang", help=_("язык оригинала (по умолчанию определяется по тексту)"))
    p.add_argument("--library", "-L", help=_("библиотека (папка, где создать книгу)"))
    p.add_argument("--title")
    p.add_argument("--fix-breaks", action="store_true", help=_("для папки: склеить разорванные абзацы (с резервной копией)"))
    for name in ("run", "continue"):
        p = sub.add_parser(name, help=_("перевести книгу (без агента)") if name == "run" else _("доделать прерванный перевод"))
        book_arg(p)
        p.add_argument("-c", "--chapters", help=_("диапазон глав: 1-50,60"))
        p.add_argument("--profile", choices=list(PROFILES))
        p.add_argument("--background", action="store_true", help=_("запустить в фоне и сразу вернуться"))
        p.add_argument("--export", choices=["epub", "fb2", "txt", "all"], help=_("собрать книгу после перевода"))
        p.add_argument("--wave", type=int, help=_("глав в волне (по умолчанию 25)"))
        p.add_argument("--no-review", action="store_true")
        p.add_argument("--retranslate", action="store_true", help=_("перевести заново уже готовые главы"))
        p.add_argument("--no-prepare", action="store_true", help=_("не готовить глоссарий/библию автоматически"))
        p.add_argument("-v", "--verbose", action="store_true")
    p = sub.add_parser("status", help=_("состояние книги и фоновой задачи"))
    book_arg(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--watch", action="store_true", help=_("следить до окончания"))
    p = sub.add_parser("stop", help=_("мягко остановить фоновый перевод"))
    book_arg(p)
    p = sub.add_parser("audit", help=_("проверить перевод (свой или чужой) без LLM; --fix — починить"))
    book_arg(p)
    p.add_argument("-c", "--chapters")
    p.add_argument("--fix", action="store_true")
    p = sub.add_parser("export", help=_("собрать книгу: EPUB / FB2 / TXT"))
    book_arg(p)
    p.add_argument("--format", default="epub", choices=["epub", "fb2", "txt", "all"])
    p.add_argument("-o", "--out")
    p.add_argument("-c", "--chapters")
    p.add_argument("--title")
    p.add_argument("--author")
    p = sub.add_parser("clean", help=_("убрать мусор: служебные строки сайтов, заметки и благодарности автора"))
    book_arg(p)
    p.add_argument("--apply", action="store_true", help=_("применить (без флага — только предпросмотр)"))
    p.add_argument("--target", default="translation", choices=["translation", "source", "both"])
    p.add_argument("--drop", action="append", help=_("удалять эту строку (точно)"))
    p.add_argument("--drop-regex", action="append", help=_("удалять строки по регулярному выражению"))
    p = sub.add_parser("cast", help=_("персонажи и их пол (для редактора рода)"))
    book_arg(p)
    p.add_argument("--set", action="append", help=_("\"Имя=f:кто это\" или \"Имя=m\""))
    p.add_argument("--suggest", action="store_true", help=_("показать персонажей и статистику рода по тексту"))
    p = sub.add_parser("edit", help=_("редактура готового перевода: --genders — исправить род персонажей"))
    book_arg(p)
    p.add_argument("--genders", action="store_true", required=True)
    p.add_argument("--names", help=_("только главы с этими персонажами (через запятую), заново"))
    p.add_argument("--redo", action="store_true", help=_("заново и уже отредактированные главы"))
    p.add_argument("-c", "--chapters")
    p.add_argument("--profile", choices=list(PROFILES))
    p.add_argument("--background", action="store_true")
    p = sub.add_parser("estimate", help=_("оценка токенов и времени по профилям"))
    book_arg(p)
    p.add_argument("-c", "--chapters")
    p = sub.add_parser("profiles", help=_("профили; --set — выбрать"))
    p.add_argument("--set")
    p.add_argument("--book", help=_("задать профиль только этой книге"))
    p = sub.add_parser("bench", help=_("проверить модель на общем наборе текстов (качество — проверками кода)"))
    p.add_argument("-m", "--model", action="append", help=_("провайдер:модель (можно несколько; по умолчанию — переводчик)"))
    p.add_argument("-p", "--pair", action="append", help=_("пара языков: zh-ru, ja-ru, fr-en… или all (можно несколько)"))
    p.add_argument("--results", default="bench/results", help=_("куда сохранять результаты"))
    p.add_argument("--no-save", action="store_true", help=_("не сохранять результат"))
    p.add_argument("--keep", action="store_true", help=_("оставить рабочую папку прогона для разбора"))
    p.add_argument("--budget", type=int, default=100_000, help=_("лимит токенов на прогон (0 — без лимита)"))
    p.add_argument("--leaderboard", action="store_true", help=_("собрать bench/LEADERBOARD.md из результатов"))
    p = sub.add_parser("models", help=_("модели провайдера"))
    p.add_argument("provider", nargs="?")
    sub.add_parser("doctor", help=_("проверить ключи, связь и модели"))
    p = sub.add_parser("setup", help=_("мастер настройки (или флагами без вопросов)"))
    p.add_argument("--provider")
    p.add_argument("--key")
    p.add_argument("--base-url")
    for slot in SLOTS:
        p.add_argument(f"--{slot}", help=_("модель слота {0}").format(slot))
    p.add_argument("--profile", choices=list(PROFILES))
    p.add_argument("--to", help=_("язык перевода по умолчанию для новых книг (en, ru, es…)"))
    p = sub.add_parser("languages", help=_("поддерживаемые языки и какие проверки есть для пары"))
    p.add_argument("--from", dest="from_lang", default="zh")
    p = sub.add_parser("integrate", help=_("подключить к агенту: integrate hermes — установить скил"))
    p.add_argument("target", choices=["hermes"])
    p.add_argument("--hermes-home")
    p.add_argument("--category", default="productivity")
    p.add_argument("--library", help=_("папка-библиотека книг для Hermes"))
    p = sub.add_parser("serve", help=_("JSON-RPC API по stdin/stdout для приложений (docs/INTEGRATION.ru.md)"))
    p.add_argument("--home", help=_("рабочая папка: библиотека и черновики"))
    p = sub.add_parser("mcp", help=_("MCP-сервер (stdio) для Hermes / Claude Code"))
    book_arg(p)
    for name in ("ask", "chat"):
        p = sub.add_parser(name, help=_("задача агенту одной фразой") if name == "ask" else _("диалог с агентом"))
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
                "estimate": cmd_estimate, "profiles": cmd_profiles, "models": cmd_models, "bench": cmd_bench, "doctor": cmd_doctor,
                "setup": cmd_setup, "mcp": cmd_mcp, "serve": cmd_serve, "languages": cmd_languages, "ask": cmd_ask, "integrate": cmd_integrate,
                "chat": lambda a, c: App(Path(a.book), c, a.verbose, a.protocol).repl()}
    try:
        handlers[a.cmd](a, cfg)
    except (ValueError, KeyError, FileNotFoundError) as e:
        console.print(f"[red]{type(e).__name__}: {e}[/red]")
        sys.exit(1)


def legacy_main(argv):
    """polytome [папка] [-p задача] — как раньше: диалог с агентом."""
    ap = argparse.ArgumentParser(prog="polytome", description=_("Polytome — агентный переводчик книг. Команды: new run continue status stop audit export "
                                                                "estimate profiles models doctor setup mcp ask chat (polytome <команда> -h)"))
    ap.add_argument("book", nargs="?", default=".", help=_("папка книги (по умолчанию текущая)"))
    ap.add_argument("-p", "--prompt", help=_("выполнить одну задачу и выйти"))
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
            _("[bold]Polytome[/bold] — агентный переводчик книг\n\nБыстрый старт:\n  polytome setup         "
              "            — выбрать провайдера и модели\n  polytome new книга.epub            — создать "
              "книгу из EPUB/TXT/папки глав\n  polytome run <папка книги>         — перевести (без "
              "агента)\n  polytome <папка книги>             — диалог с агентом\n  polytome -h             "
              "           — все команды"), border_style="cyan"))
        return
    app = App(book, cfg, a.verbose, a.protocol)
    if a.prompt:
        app.run_agent(a.prompt)
        console.print(f"[dim]{app.llm.usage.summary()}[/dim]")
    else:
        app.repl()


if __name__ == "__main__":
    main()
