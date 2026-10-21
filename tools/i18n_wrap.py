"""Localisation helper for contributors: finds user-facing Russian strings and wraps them in _() .

    python tools/i18n_wrap.py list  polytome/cli.py        # show candidates
    python tools/i18n_wrap.py wrap  polytome/cli.py        # rewrite the file in place
    python tools/i18n_wrap.py check                        # every _("...") in the package has an English entry

f-strings become  _("text {0} more {1:,}").format(expr0, expr1)  — the catalog key is the template.
Skipped: docstrings, strings already inside _(), regex patterns, dict keys, comparisons, model prompts.
Catalog: polytome/locales/en.json  {"русская строка": "English string"}.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "polytome" / "locales" / "en.json"
CYR = re.compile(r"[А-Яа-яЁё]")
SKIP_CALLS = {"compile", "search", "match", "sub", "findall", "finditer", "split", "fullmatch", "_", "startswith",
              "endswith", "replace", "find", "index", "count"}


def _parents(tree):
    par = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            par[ch] = node
    return par


def _is_docstring(node, par):
    p = par.get(node)
    if not isinstance(p, ast.Expr):
        return False
    gp = par.get(p)
    body = getattr(gp, "body", None)
    return isinstance(body, list) and body and body[0] is p


def candidates(src: str):
    tree = ast.parse(src)
    par = _parents(tree)
    src_lines = src.splitlines()
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if isinstance(par.get(node), (ast.JoinedStr, ast.FormattedValue)):
                continue
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            if isinstance(par.get(node), ast.FormattedValue):
                continue
            text = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
        else:
            continue
        if not CYR.search(text) or _is_docstring(node, par) or "i18n: skip" in src_lines[node.lineno - 1]:
            continue  # пометка «# i18n: skip» — данные или текст для модели, не интерфейс
        p = par.get(node)
        if isinstance(p, ast.Call) and isinstance(p.func, (ast.Attribute, ast.Name)):
            name = p.func.attr if isinstance(p.func, ast.Attribute) else p.func.id
            if name in SKIP_CALLS:
                continue
        if isinstance(p, ast.Compare) or (isinstance(p, ast.Dict) and node in p.keys):
            continue
        if isinstance(p, (ast.Tuple, ast.List, ast.Set)) and isinstance(par.get(p), ast.Compare):
            continue
        out.append(node)
    return out


def template(src: str, node) -> tuple[str, list[str]]:
    if isinstance(node, ast.Constant):
        return node.value, []
    parts, args = [], []
    for v in node.values:
        if isinstance(v, ast.Constant):
            parts.append(v.value.replace("{", "{{").replace("}", "}}"))
        else:
            i = len(args)
            args.append(ast.get_source_segment(src, v.value))
            conv = {-1: "", 115: "!s", 114: "!r", 97: "!a"}[v.conversion]
            spec = ""
            if v.format_spec is not None:
                spec = ":" + "".join(x.value for x in v.format_spec.values if isinstance(x, ast.Constant))
            parts.append("{" + str(i) + conv + spec + "}")
    return "".join(parts), args


def _literal(s: str, indent: int) -> str:
    lit = json.dumps(s, ensure_ascii=False)
    if len(lit) <= 100:
        return lit
    chunks, cur = [], ""
    for word in re.split(r"(?<= )", s):
        if len(cur) + len(word) > 90 and cur:
            chunks.append(cur)
            cur = ""
        cur += word
    chunks.append(cur)
    pad = " " * indent
    return ("\n" + pad).join(json.dumps(c, ensure_ascii=False) for c in chunks)


def wrap(path: Path) -> int:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    offs = [0]
    for ln in lines:
        offs.append(offs[-1] + len(ln))
    nodes = candidates(src)
    spans = [((n.lineno, n.col_offset), (n.end_lineno, n.end_col_offset)) for n in nodes]
    # строки внутри выражений другой f-строки обернёт следующий проход (они станут аргументами .format)
    nodes = [n for n, (a, b) in zip(nodes, spans)
             if not any(a2 <= a and b <= b2 and (a2, b2) != (a, b) for a2, b2 in spans)]
    edits = []
    for node in nodes:
        tpl, args = template(src, node)
        start = offs[node.lineno - 1] + len(lines[node.lineno - 1].encode("utf-8")[:node.col_offset].decode("utf-8"))
        end = offs[node.end_lineno - 1] + len(lines[node.end_lineno - 1].encode("utf-8")[:node.end_col_offset]
                                              .decode("utf-8"))
        col = len(lines[node.lineno - 1].encode("utf-8")[:node.col_offset].decode("utf-8"))
        lit = _literal(tpl, col + 2)
        new = f"_({lit})" + (f".format({', '.join(args)})" if args else "")
        edits.append((start, end, new))
    for start, end, new in sorted(edits, reverse=True):
        src = src[:start] + new + src[end:]
    if edits and "from .i18n import _" not in src:
        m = re.search(r"^from \.\w+ import .*$", src, re.M)
        if m:
            src = src[:m.start()] + "from .i18n import _\n" + src[m.start():]
        else:  # нет относительных импортов: после последнего импорта модуля
            last = list(re.finditer(r"^(?:import|from) [\w.]+.*$", src, re.M))[-1]
            src = src[:last.end()] + "\n\nfrom .i18n import _" + src[last.end():]
    for attempt in range(5):  # Windows иногда держит файл (антивирус) — повторить
        try:
            path.write_text(src, encoding="utf-8")
            break
        except OSError:
            import time
            time.sleep(1 + attempt)
    else:
        raise OSError(f"не удалось записать {path}")
    return len(edits)


def used_keys() -> set[str]:
    keys = set()
    for f in (ROOT / "polytome").rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_" and node.args \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                keys.add(node.args[0].value)
    return keys


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1]
    if cmd == "list":
        for f in sys.argv[2:]:
            src = Path(f).read_text(encoding="utf-8")
            for n in candidates(src):
                print(f"{f}:{n.lineno}: {template(src, n)[0][:110]!r}")
    elif cmd == "wrap":
        for f in sys.argv[2:]:
            total = 0
            while True:  # повторять, пока находятся строки (вложенные — на следующем проходе)
                n = wrap(Path(f))
                total += n
                if not n:
                    break
            print(f, total)
    elif cmd == "check":
        cat = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else {}
        missing = sorted(used_keys() - set(cat))
        for k in missing:
            print("missing:", repr(k))
        print(f"{len(missing)} missing of {len(used_keys())}")
        sys.exit(1 if missing else 0)
    elif cmd == "missing-json":
        cat = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else {}
        print(json.dumps({k: "" for k in sorted(used_keys() - set(cat))}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
