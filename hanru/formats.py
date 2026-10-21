"""Книги на входе и на выходе: EPUB / TXT / папка глав → рабочая папка Hanru; перевод → EPUB / FB2 / TXT.

Только стандартная библиотека (zipfile, xml, html.parser).
"""
from __future__ import annotations

import base64
import html
import json
import re
import shutil
import time
import uuid
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from .workspace import Workspace, atomic_write, chapter_number, split_paragraphs

NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/",
      "ncx": "http://www.daisy.org/z3986/2005/ncx/", "xhtml": "http://www.w3.org/1999/xhtml",
      "ops": "http://www.idpf.org/2007/ops", "c": "urn:oasis:names:tc:opendocument:xmlns:container"}
CHAPTER_RE = re.compile(r"^\s*(第[0-9零〇一二两兩三四五六七八九十百千万萬]+[章节節回卷].*|Глава\s*\d+.*|Chapter\s*\d+.*"
                        r"|CHAPTER\s*\d+.*|第\d+話.*|제\s*\d+\s*화.*)$", re.M)


# ---------- разорванные абзацы (жёсткие переносы в скачанных текстах) ----------
_OPENERS = "「“『【《（(〈‘'"
_CONT_START = re.compile(r"^[，,。．、！？!?；;：:」』”）)》〉…]")
_ENDS_OPEN = re.compile(r"([一-鿿豈-﫿0-9A-Za-z，,、「“『（(《])$")  # без «：» — разрыв после двоеточия авторский
_HEADING = re.compile(r"^\s*(第[0-9零〇一二两兩三四五六七八九十百千万萬]+[章节節回卷]|Глава\s*\d+|Chapter\s*\d+)", re.I)


def fix_line_breaks(paragraphs: list[str]) -> tuple[list[str], int]:
    """Склеивает абзацы, разорванные посреди фразы: «…她二» + «的拳館練了幾年…», «…娘» + «炮還敢…」».

    На таких обрывках переводчик путается в нумерации, и содержание съезжает между абзацами (найдено на
    романе в 100 глав: 7% абзацев были обрывками). Концы «......», «’», «」» — законные, их не трогаем.
    """
    out: list[str] = []
    merged = 0
    for p in paragraphs:
        if out and len(out) > 1 or (out and not _HEADING.match(out[0])):
            prev = out[-1]
            cont = bool(_CONT_START.match(p))
            open_end = bool(_ENDS_OPEN.search(prev)) and not p.startswith(tuple(_OPENERS)) \
                and not _HEADING.match(p) and not prev.endswith(("......", "...", "…"))
            if (cont or open_end) and not _HEADING.match(prev) and len(prev) >= 6:
                out[-1] = prev + p
                merged += 1
                continue
        out.append(p)
    return out, merged


# ---------- определение языка ----------
def detect_lang(text: str) -> str:
    sample = text[:20000]
    kana = len(re.findall(r"[぀-ヿ]", sample))
    hangul = len(re.findall(r"[가-힯]", sample))
    han = len(re.findall(r"[一-鿿]", sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    cyr = len(re.findall(r"[А-Яа-яЁё]", sample))
    total = max(1, kana + hangul + han + latin + cyr)
    if hangul / total > 0.2 and hangul > kana:
        return "ko"
    if kana / total > 0.05 and kana >= 5:
        return "ja"  # японский: кандзи + заметная доля каны
    if han / total > 0.3:
        return "zh"
    if cyr > latin:
        return "ru"
    return "en"


# ---------- HTML → блоки ----------
_BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "section", "article", "tr", "dd", "dt",
          "pre", "figcaption", "header", "footer"}
_HEAD = {"h1", "h2", "h3", "h4"}


class _Blocks(HTMLParser):
    """Абзацы документа + маркеры id (для границ глав по оглавлению). → [("p"|"h"|"id", text)]"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[tuple[str, str]] = []
        self.buf: list[str] = []
        self.skip = 0
        self.head = 0

    def flush(self):
        t = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if t:
            self.out.append(("h" if self.head else "p", t))
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
            return
        a = dict(attrs)
        if a.get("id"):
            self.flush()
            self.out.append(("id", a["id"]))
        if tag in _BLOCK:
            self.flush()
        if tag in _HEAD:
            self.head += 1
        if tag == "br":
            self.flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
            return
        if tag in _BLOCK:
            self.flush()
        if tag in _HEAD:
            self.head = max(0, self.head - 1)

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def close(self):
        super().close()
        self.flush()


def html_blocks(markup: str) -> list[tuple[str, str]]:
    p = _Blocks()
    p.feed(markup)
    p.close()
    return p.out


# ---------- EPUB ----------
def _zread(z: zipfile.ZipFile, name: str) -> str:
    raw = z.read(name)
    for enc in ("utf-8", "utf-16", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_epub(path: Path) -> dict:
    """→ {"title", "author", "lang", "cover": (имя, байты)|None, "chapters": [{"title", "paragraphs"}]}"""
    z = zipfile.ZipFile(path)
    container = ET.fromstring(z.read("META-INF/container.xml"))
    opf_path = container.find(".//c:rootfile", NS).get("full-path")
    base = PurePosixPath(opf_path).parent
    opf = ET.fromstring(z.read(opf_path))

    def full(href: str) -> str:
        return str((base / href.split("#")[0]).as_posix()).lstrip("./") if str(base) != "." else href.split("#")[0]

    title = (opf.findtext(".//dc:title", default="", namespaces=NS) or path.stem).strip()
    author = (opf.findtext(".//dc:creator", default="", namespaces=NS) or "").strip()
    manifest = {it.get("id"): it for it in opf.find("opf:manifest", NS)}
    spine = [manifest[r.get("idref")] for r in opf.find("opf:spine", NS) if r.get("idref") in manifest]

    # обложка
    cover = None
    cover_id = next((m.get("content") for m in opf.iter(f"{{{NS['opf']}}}meta") if m.get("name") == "cover"), None)
    for it in manifest.values():
        props = it.get("properties") or ""
        if "cover-image" in props or (cover_id and it.get("id") == cover_id):
            if (it.get("media-type") or "").startswith("image/"):
                try:
                    cover = (PurePosixPath(it.get("href")).name, z.read(full(it.get("href"))))
                except KeyError:
                    pass
                break

    # документы в порядке spine → поток блоков с границами документов
    stream: list[tuple[str, str, str]] = []  # (doc, kind, text)
    for it in spine:
        if "html" not in (it.get("media-type") or "") or "nav" in (it.get("properties") or ""):
            continue
        doc = full(it.get("href"))
        try:
            blocks = html_blocks(_zread(z, doc))
        except KeyError:
            continue
        stream.append((doc, "doc", ""))
        stream.extend((doc, k, t) for k, t in blocks)

    toc = _read_toc(z, opf, manifest, full)
    chapters = _split_by_toc(stream, toc) if len(toc) >= 2 else []
    if not chapters:
        chapters = _split_by_docs(stream)
    if not chapters:  # совсем короткие документы: берём всё, что есть, чем молча вернуть 0 глав
        chapters = _split_by_docs(stream, min_len=1)
    text_sample = "\n".join(p for ch in chapters[:5] for p in ch["paragraphs"])
    return {"title": title, "author": author, "lang": detect_lang(text_sample or title),
            "cover": cover, "chapters": chapters, "toc_used": bool(toc) and len(toc) >= 2}


def _read_toc(z, opf, manifest, full) -> list[tuple[str, str, str]]:
    """[(doc, fragment, label)] из EPUB3 nav или EPUB2 NCX."""
    out = []
    nav = next((it for it in manifest.values() if "nav" in (it.get("properties") or "")), None)
    if nav is not None:
        try:
            root_dir = PurePosixPath(full(nav.get("href"))).parent
            blocks = _NavParser()
            blocks.feed(_zread(z, full(nav.get("href"))))
            for href, label in blocks.links:
                doc = str((root_dir / href.split("#")[0]).as_posix()) if href.split("#")[0] else ""
                out.append((_norm(doc), href.split("#")[1] if "#" in href else "", label))
        except KeyError:
            pass
    if not out:
        spine = opf.find("opf:spine", NS)
        ncx_id = spine.get("toc") if spine is not None else None
        ncx = manifest.get(ncx_id) if ncx_id else next(
            (it for it in manifest.values() if it.get("media-type") == "application/x-dtbncx+xml"), None)
        if ncx is not None:
            try:
                root_dir = PurePosixPath(full(ncx.get("href"))).parent
                tree = ET.fromstring(z.read(full(ncx.get("href"))))
                for np in tree.iter(f"{{{NS['ncx']}}}navPoint"):
                    label = (np.findtext(".//ncx:text", default="", namespaces=NS) or "").strip()
                    src = np.find("ncx:content", NS).get("src")
                    out.append((_norm(str((root_dir / src.split("#")[0]).as_posix())),
                                src.split("#")[1] if "#" in src else "", label))
            except (KeyError, AttributeError):
                pass
    return out


def _norm(p: str) -> str:
    parts = []
    for seg in p.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg and seg != ".":
            parts.append(seg)
    return "/".join(parts)


class _NavParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.in_toc = False
        self.href = None
        self.buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "nav" and ("toc" in (a.get("epub:type") or "") or a.get("id") == "toc" or not self.links):
            self.in_toc = True
        if tag == "a" and self.in_toc and a.get("href"):
            self.href, self.buf = a["href"], []

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            self.links.append((self.href, re.sub(r"\s+", " ", "".join(self.buf)).strip()))
            self.href = None
        if tag == "nav":
            self.in_toc = False

    def handle_data(self, data):
        if self.href is not None:
            self.buf.append(data)


def _split_by_toc(stream, toc) -> list[dict]:
    # позиции начала каждой записи оглавления в потоке
    starts = []
    for doc, frag, label in toc:
        pos = None
        for i, (d, k, t) in enumerate(stream):
            if d.endswith(doc) or doc.endswith(d):
                if not frag and k == "doc":
                    pos = i
                    break
                if frag and k == "id" and t == frag:
                    pos = i
                    break
        if pos is not None and (not starts or pos > starts[-1][0]):
            starts.append((pos, label))
    chapters = []
    for j, (pos, label) in enumerate(starts):
        end = starts[j + 1][0] if j + 1 < len(starts) else len(stream)
        paras = [t for d, k, t in stream[pos:end] if k in ("p", "h")]
        if not paras:
            continue
        if label and paras[0].strip() != label.strip() and label not in paras[0]:
            paras.insert(0, label)
        chapters.append({"title": label or paras[0], "paragraphs": paras})
    return chapters


def _split_by_docs(stream, min_len: int = 200) -> list[dict]:
    """Без оглавления: документ = глава; если в документе несколько заголовков «第N章…» — режем по ним."""
    docs: dict[str, list[tuple[str, str]]] = {}
    order = []
    for d, k, t in stream:
        if d not in docs:
            docs[d] = []
            order.append(d)
        if k in ("p", "h"):
            docs[d].append((k, t))
    chapters = []
    for d in order:
        blocks = docs[d]
        text_len = sum(len(t) for _, t in blocks)
        if text_len < min_len:
            continue  # обложка, титул, копирайт
        cur: list[str] = []
        for k, t in blocks:
            if CHAPTER_RE.match(t) and cur and sum(len(x) for x in cur) > 200:
                chapters.append({"title": cur[0], "paragraphs": cur})
                cur = []
            cur.append(t)
        if cur:
            chapters.append({"title": cur[0], "paragraphs": cur})
    return chapters


# ---------- TXT ----------
def read_txt(path: Path) -> dict:
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-16", "gb18030", "big5", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    text = text or raw.decode("utf-8", errors="replace")
    marks = list(CHAPTER_RE.finditer(text))
    chapters = []
    if len(marks) >= 2:
        for i, m in enumerate(marks):
            body = text[m.start(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
            paras = split_paragraphs(body)
            if paras:
                chapters.append({"title": paras[0], "paragraphs": paras})
    else:  # нет заголовков — режем на куски ~3000 символов по абзацам
        paras, cur, size = split_paragraphs(text), [], 0
        for p in paras:
            cur.append(p)
            size += len(p)
            if size > 3000:
                chapters.append({"title": f"Часть {len(chapters) + 1}", "paragraphs": [f"Часть {len(chapters) + 1}"] + cur})
                cur, size = [], 0
        if cur:
            chapters.append({"title": f"Часть {len(chapters) + 1}", "paragraphs": [f"Часть {len(chapters) + 1}"] + cur})
    return {"title": path.stem, "author": "", "lang": detect_lang(text), "cover": None, "chapters": chapters,
            "toc_used": False}


# ---------- создание книги ----------
def create_book(source: Path, library: Path | None = None, title: str | None = None,
                fix_breaks: bool = False) -> tuple[Workspace, dict]:
    """EPUB / TXT / папка с главами → рабочая папка книги. Возвращает (workspace, отчёт)."""
    source = Path(source)
    if source.is_dir():
        ws = Workspace(source)
        ws.init()
        n = len(ws.chapters())
        sample = "\n".join(ws.source_paragraphs(min(ws.chapters()))) if n else ""
        ws.meta["source_lang"] = detect_lang(sample) if sample else ws.meta.get("source_lang", "zh")
        ws.save_meta()
        breaks = {m: fix_line_breaks(ws.source_paragraphs(m)) for m in ws.chapters()}
        total = sum(v[1] for v in breaks.values())
        rep = {"chapters": n, "lang": ws.meta["source_lang"], "format": "folder", "line_breaks": total}
        if total and fix_breaks:
            backup = ws.root / f"{ws.meta['source_dir']}_backup_{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.copytree(ws.source_dir, backup)
            for m, (paras, cnt) in breaks.items():
                if cnt:
                    atomic_write(ws.chapters()[m], "\n".join(paras))
            rep.update({"line_breaks_fixed": total, "backup": str(backup)})
        return ws, rep
    ext = source.suffix.lower()
    book = read_epub(source) if ext == ".epub" else read_txt(source) if ext in (".txt", ".text") else None
    if book is None:
        raise ValueError(f"Формат {ext} не поддерживается: EPUB, TXT или папка с главами")
    if not book["chapters"]:
        raise ValueError("Не удалось найти главы в файле")
    name = re.sub(r'[\\/:*?"<>|]+', " ", title or book["title"] or source.stem).strip()[:80] or "book"
    root = (library or source.parent) / name
    ws = Workspace(root)
    ws.meta.update({"title": name, "original_title": book["title"], "author": book.get("author", ""),
                    "source_lang": book["lang"], "source_file": str(source),
                    "imported_at": time.strftime("%Y-%m-%d %H:%M"), "source_dir": "Оригинал", "target_dir": "Перевод"})
    ws.init()
    existing = ws.chapters()
    if existing:
        raise ValueError(f"В {ws.source_dir} уже есть главы ({len(existing)}) — выберите другую папку или удалите их")
    fixed = 0
    for i, ch in enumerate(book["chapters"], 1):
        paras, cnt = fix_line_breaks(ch["paragraphs"])
        fixed += cnt
        atomic_write(ws.source_dir / f"Глава {i}.txt", "\n".join(paras))
    if book.get("cover"):
        ext_c = Path(book["cover"][0]).suffix or ".jpg"
        (ws.root / f"cover{ext_c}").write_bytes(book["cover"][1])
    atomic_write(ws.meta_dir / "source_map.json",
                 json.dumps([{"n": i, "title": ch["title"]} for i, ch in enumerate(book["chapters"], 1)],
                            ensure_ascii=False, indent=1))
    chars = sum(len(p) for ch in book["chapters"] for p in ch["paragraphs"])
    return ws, {"chapters": len(book["chapters"]), "lang": book["lang"], "title": book["title"],
                "author": book.get("author", ""), "characters": chars, "cover": bool(book.get("cover")),
                "split": "по оглавлению" if book.get("toc_used") else "по документам/заголовкам",
                "line_breaks_fixed": fixed,
                "path": str(ws.root), "format": ext.lstrip(".")}


# ---------- экспорт ----------
def _chapters_for_export(ws: Workspace, chapters: list[int] | None):
    nums = chapters or list(ws.chapters())
    out, missing = [], []
    for n in nums:
        if ws.is_translated(n):
            paras = split_paragraphs(ws.target_path(n).read_text(encoding="utf-8-sig"))
            if paras:
                out.append((n, paras[0], paras[1:]))
        else:
            missing.append(n)
    return out, missing


def _cover(ws: Workspace) -> Path | None:
    for name in ("cover.jpg", "cover.jpeg", "cover.png", "cover.webp"):
        if (ws.root / name).is_file():
            return ws.root / name
    return None


def export_book(ws: Workspace, fmt: str = "epub", out: Path | None = None, chapters: list[int] | None = None,
                title: str | None = None, author: str | None = None) -> dict:
    items, missing = _chapters_for_export(ws, chapters)
    if not items:
        raise ValueError("Нет переведённых глав для сборки")
    title = title or ws.meta.get("title_ru") or ws.meta.get("title") or ws.root.name
    author = author or ws.meta.get("author_ru") or ws.meta.get("author") or "Неизвестный автор"
    fmt = fmt.lower()
    out = Path(out) if out else ws.root / f"{title}.{fmt}"
    if fmt == "txt":
        text = "\n\n\n".join("\n\n".join([t] + body) for _, t, body in items)
        atomic_write(out, text, encoding="utf-8-sig")
    elif fmt == "fb2":
        _write_fb2(ws, items, out, title, author)
    elif fmt == "epub":
        _write_epub(ws, items, out, title, author)
    else:
        raise ValueError("Формат: epub | fb2 | txt")
    return {"path": str(out), "format": fmt, "chapters": len(items), "missing_chapters": missing[:50],
            "missing_total": len(missing), "bytes": out.stat().st_size, "cover": bool(_cover(ws))}


def _x(s: str) -> str:
    return html.escape(s, quote=True)


def _write_epub(ws: Workspace, items, out: Path, title: str, author: str):
    uid = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "hanru:" + ws.meta.get("title", "") + title))
    cover = _cover(ws)
    tmp = out.with_suffix(".epub.tmp")
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0" encoding="UTF-8"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
                   'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
                   compress_type=zipfile.ZIP_DEFLATED)
        css = ("body{font-family:serif;line-height:1.5;margin:0 5%}h2{text-align:center;margin:1.5em 0 1em}"
               "p{text-indent:1.5em;margin:0 0 .3em 0;text-align:justify}")
        z.writestr("OEBPS/style.css", css, compress_type=zipfile.ZIP_DEFLATED)
        manifest, spine, nav, ncx = [], [], [], []
        if cover:
            mt = "image/png" if cover.suffix == ".png" else "image/jpeg"
            z.write(cover, f"OEBPS/images/cover{cover.suffix}", compress_type=zipfile.ZIP_DEFLATED)
            manifest.append(f'<item id="cover-img" href="images/cover{cover.suffix}" media-type="{mt}" '
                            'properties="cover-image"/>')
            z.writestr("OEBPS/text/cover.xhtml", _xhtml("Обложка", f'<div style="text-align:center"><img '
                       f'src="../images/cover{cover.suffix}" alt="cover" style="max-width:100%;max-height:100%"/></div>'),
                       compress_type=zipfile.ZIP_DEFLATED)
            manifest.append('<item id="cover" href="text/cover.xhtml" media-type="application/xhtml+xml"/>')
            spine.append('<itemref idref="cover"/>')
        for i, (n, t, body) in enumerate(items, 1):
            fid = f"ch{n:05d}"
            content = f"<h2>{_x(t)}</h2>\n" + "\n".join(f"<p>{_x(p)}</p>" for p in body)
            z.writestr(f"OEBPS/text/{fid}.xhtml", _xhtml(t, content), compress_type=zipfile.ZIP_DEFLATED)
            manifest.append(f'<item id="{fid}" href="text/{fid}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{fid}"/>')
            nav.append(f'<li><a href="text/{fid}.xhtml">{_x(t)}</a></li>')
            ncx.append(f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{_x(t)}</text></navLabel>'
                       f'<content src="text/{fid}.xhtml"/></navPoint>')
        z.writestr("OEBPS/nav.xhtml", _xhtml("Оглавление", f'<nav epub:type="toc" id="toc"><h2>Оглавление</h2><ol>'
                   + "".join(nav) + "</ol></nav>"), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", '<?xml version="1.0" encoding="UTF-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
                   f'version="2005-1"><head><meta name="dtb:uid" content="{uid}"/></head><docTitle><text>{_x(title)}'
                   f'</text></docTitle><navMap>{"".join(ncx)}</navMap></ncx>', compress_type=zipfile.ZIP_DEFLATED)
        meta_cover = '<meta name="cover" content="cover-img"/>' if cover else ""
        opf = (f'<?xml version="1.0" encoding="UTF-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
               f'unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
               f'<dc:identifier id="uid">{uid}</dc:identifier><dc:title>{_x(title)}</dc:title>'
               f'<dc:creator>{_x(author)}</dc:creator><dc:language>ru</dc:language>'
               f'<meta property="dcterms:modified">{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}</meta>'
               f'{meta_cover}</metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
               f'properties="nav"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
               f'<item id="css" href="style.css" media-type="text/css"/>{"".join(manifest)}</manifest>'
               f'<spine toc="ncx">{"".join(spine)}</spine></package>')
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
    shutil.move(str(tmp), str(out))


def _xhtml(title: str, body: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" '
            f'xmlns:epub="http://www.idpf.org/2007/ops" lang="ru" xml:lang="ru"><head><meta charset="utf-8"/>'
            f'<title>{_x(title)}</title><link rel="stylesheet" type="text/css" href="../style.css"/></head>'
            f'<body>{body}</body></html>')


def _write_fb2(ws: Workspace, items, out: Path, title: str, author: str):
    cover = _cover(ws)
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">',
             "<description><title-info><genre>sf</genre>",
             f"<author><nickname>{_x(author)}</nickname></author>", f"<book-title>{_x(title)}</book-title>",
             "<lang>ru</lang>", f"<src-lang>{_x(ws.meta.get('source_lang', 'zh'))}</src-lang>"]
    if cover:
        parts.append('<coverpage><image l:href="#cover.jpg"/></coverpage>')
    parts += ["</title-info>", "<document-info><author><nickname>Hanru</nickname></author>",
              "<program-used>Hanru</program-used>", f"<date>{time.strftime('%Y-%m-%d')}</date>",
              f"<id>{uuid.uuid5(uuid.NAMESPACE_URL, 'hanru-fb2:' + title)}</id><version>1.0</version>",
              "</document-info></description><body>", f"<title><p>{_x(title)}</p></title>"]
    for _, t, body in items:
        parts.append(f"<section><title><p>{_x(t)}</p></title>")
        parts += [f"<p>{_x(p)}</p>" for p in body]
        parts.append("</section>")
    parts.append("</body>")
    if cover:
        ct = "image/png" if cover.suffix == ".png" else "image/jpeg"
        parts.append(f'<binary id="cover.jpg" content-type="{ct}">{base64.b64encode(cover.read_bytes()).decode()}</binary>')
    parts.append("</FictionBook>")
    atomic_write(out, "\n".join(parts))


def chapter_titles_from_source(ws: Workspace) -> dict[int, str]:
    out = {}
    for n in ws.chapters():
        paras = ws.source_paragraphs(n)
        out[n] = paras[0] if paras else f"Глава {n}"
    return out


__all__ = ["create_book", "export_book", "read_epub", "read_txt", "detect_lang", "chapter_number"]
