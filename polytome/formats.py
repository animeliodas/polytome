"""Книги на входе и на выходе: EPUB / TXT / папка глав → рабочая папка Polytome; перевод → EPUB / FB2 / TXT.

Только стандартная библиотека (zipfile, xml, html.parser).
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from .i18n import _
from .workspace import Workspace, atomic_write, chapter_number, split_paragraphs

NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/",
      "ncx": "http://www.daisy.org/z3986/2005/ncx/", "xhtml": "http://www.w3.org/1999/xhtml",
      "ops": "http://www.idpf.org/2007/ops", "c": "urn:oasis:names:tc:opendocument:xmlns:container"}
CHAPTER_WORDS = (r"Глава|Розділ|Chapter|CHAPTER|Capítulo|Chapitre|Kapitel|Capitolo|Rozdział|Kapitola|Bölüm|"  # i18n: skip
                 r"Chương|Bab|Hoofdstuk|Kapittel|Kapitel|Luku|Fejezet|Capitol")
CHAPTER_RE = re.compile(r"^\s*(第[0-9零〇一二两兩三四五六七八九十百千万萬]+[章节節回卷].*|(?:" + CHAPTER_WORDS + r")\s*\d+.*"
                        r"|第\d+[話章].*|제\s*\d+\s*[화장].*)$", re.M)


# ---------- разорванные абзацы (жёсткие переносы в скачанных текстах) ----------
_OPENERS = "「“『【《（(〈‘'"
_CONT_START = re.compile(r"^[，,。．、！？!?；;：:」』”）)》〉…]")
_ENDS_OPEN = re.compile(r"([\u4e00-\u9fff\uf900-\ufaff0-9A-Za-z，,、「“『（(《])$")  # без «：» — разрыв после двоеточия авторский
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
_STOP = {
    "en": "the and of to was he she that it his her you in with for had not but said",
    "es": "el la de que y en los se del las por un una con no su para es lo como",
    "pt": "o a de que e do da em um uma para com não os se na no por mais ao",
    "fr": "le la de et les des en un une est que il elle ne pas dans pour qui au",
    "de": "der die und das ist nicht ich sie er es mit den ein eine zu auf sich dem",
    "it": "il la di che e un una non per è con del della si lo gli le ma",
    "nl": "de het een en van is dat niet ik je hij zij met op te voor",
    "pl": "się nie że na jest to w z do co jak ale tak już jego jej",
    "cs": "se je na že to ve jsem jak ale tak by byl jeho není",
    "tr": "bir ve bu da de ne için ile çok ama o ben sen gibi daha",
    "vi": "và của là có không một người cho được này những đã với",
    "id": "yang dan di itu dengan untuk tidak ini dari dalam akan ke ada",
    "sv": "och att det som en är på för av med inte jag han hon den",
    "ro": "și de la în nu că pe cu o un se este mai",
    "hu": "a az és hogy nem egy is meg de van már csak",
    "fi": "ja on ei se että hän oli mutta kun niin myös",
}
_STOPSETS = {k: set(v.split()) for k, v in _STOP.items()}


def detect_lang(text: str) -> str:
    """Язык текста по письменности; для латиницы — по частым словам. ISO 639-1."""
    from .lang import SCRIPT_RE
    sample = text[:20000]
    cnt = {k: len(re.findall(rx, sample)) for k, rx in SCRIPT_RE.items()}
    total = max(1, sum(cnt.values()))
    kana, hangul, han = cnt["kana"], cnt["hangul"], cnt["han"]
    if hangul / total > 0.2 and hangul > kana:
        return "ko"
    if kana / total > 0.05 and kana >= 5:
        return "ja"  # японский: кандзи + заметная доля каны
    if han / total > 0.3:
        return "zh"
    script = max(cnt, key=cnt.get)
    if script == "cyrillic":
        if re.search(r"[іїєґІЇЄҐ]", sample) and len(re.findall(r"[іїєґ]", sample.lower())) > cnt["cyrillic"] * 0.005:
            return "uk"
        if len(re.findall(r"[ўЎ]", sample)) > cnt["cyrillic"] * 0.002:
            return "be"
        return "ru"
    if script == "arabic":
        return "fa" if len(re.findall(r"[پچژگ]", sample)) > cnt["arabic"] * 0.01 else "ar"
    simple = {"hebrew": "he", "thai": "th", "devanagari": "hi", "bengali": "bn", "greek": "el", "georgian": "ka",
              "armenian": "hy"}
    if script in simple:
        return simple[script]
    words = re.findall(r"[a-zà-ɏ]+", sample.lower())
    if not words:
        return "en"
    scores = {k: sum(1 for w in words if w in st) for k, st in _STOPSETS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 3 else "en"


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


def read_epub(path: Path, on_progress=None) -> dict:
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
    meta_lang = (opf.findtext(".//dc:language", default="", namespaces=NS) or "").strip()
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
    for si, it in enumerate(spine):
        if on_progress and si % 25 == 0:
            on_progress("parse", si, len(spine))
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
    lang = detect_lang(text_sample or title)
    from .lang import LANGS, normalize
    ml = normalize(meta_lang)
    if ml in LANGS and lang not in ("zh", "ja", "ko") and ml not in ("zh", "ja", "ko"):
        lang = ml  # для алфавитных языков метаданные EPUB надёжнее частотного определения
    return {"title": title, "author": author, "lang": lang,
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
    # Позиции начала каждой записи оглавления. Индекс строится один раз: раньше каждая запись просматривала
    # весь поток — на романе в 1333 главы это 200+ млн сравнений (17 с на ПК, минута на телефоне).
    doc_start: dict[str, int] = {}
    id_pos: dict[tuple[str, str], int] = {}
    for i, (d, k, t) in enumerate(stream):
        if k == "doc":
            doc_start.setdefault(d, i)
        elif k == "id":
            id_pos.setdefault((d, t), i)
    resolved: dict[str, str | None] = {}

    def stream_doc(doc: str) -> str | None:
        if doc not in resolved:
            resolved[doc] = doc if doc in doc_start else next(
                (d for d in doc_start if d.endswith(doc) or doc.endswith(d)), None)
        return resolved[doc]

    starts = []
    for doc, frag, label in toc:
        d = stream_doc(doc)
        pos = None if d is None else (id_pos.get((d, frag)) if frag else doc_start.get(d))
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
FB2_NS = "{http://www.gribuser.ru/xml/fictionbook/2.0}"
_FB2_TEXT = {"p", "subtitle", "v", "text-author"}


def _fb2_text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _fb2_paragraphs(el, out: list[str]):
    """Абзацы элемента по порядку: p, subtitle, стихи (v), цитаты, эпиграфы; вложенные секции не трогаем."""
    for ch in el:
        tag = ch.tag.replace(FB2_NS, "")
        if tag == "section":
            continue
        if tag in _FB2_TEXT:
            t = _fb2_text(ch)
            if t:
                out.append(t)
        elif tag in ("poem", "stanza", "cite", "epigraph", "annotation"):
            _fb2_paragraphs(ch, out)


def _fb2_sections(body, chapters: list[dict]):
    """Глава — секция с текстом; часть/том (секции внутри секции) раскрывается, её заголовок идёт перед первой главой."""
    for sec in body.findall(f"{FB2_NS}section"):
        title_el = sec.find(f"{FB2_NS}title")
        title = " ".join(_fb2_text(p) for p in title_el.findall(f"{FB2_NS}p")) if title_el is not None else ""
        paras: list[str] = []
        _fb2_paragraphs(sec, paras)
        inner = sec.findall(f"{FB2_NS}section")
        if inner and len(paras) <= 3:
            before = len(chapters)
            _fb2_sections(sec, chapters)
            if title and len(chapters) > before:  # заголовок части — в начало её первой главы
                first = chapters[before]
                first["paragraphs"] = [title] + first["paragraphs"]
            continue
        if title or paras:
            chapters.append({"title": title or (paras[0] if paras else ""),
                             "paragraphs": ([title] if title else []) + paras})
        if inner:
            _fb2_sections(sec, chapters)


def read_fb2(path: Path) -> dict:
    """FB2 и FB2.ZIP → {"title", "author", "lang", "cover", "chapters", "toc_used"} (как read_epub)."""
    raw = path.read_bytes()
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".fb2")), None)
            if not name:
                raise ValueError(_("Не удалось найти главы в файле"))
            raw = z.read(name)
    root = ET.fromstring(raw)  # кодировку (utf-8, windows-1251…) ElementTree берёт из XML-заголовка
    info = root.find(f"{FB2_NS}description/{FB2_NS}title-info")
    title, author, meta_lang, cover = path.stem, "", "", None
    if info is not None:
        title = (info.findtext(f"{FB2_NS}book-title") or title).strip()
        a = info.find(f"{FB2_NS}author")
        if a is not None:
            parts = [a.findtext(f"{FB2_NS}{k}") or "" for k in ("first-name", "middle-name", "last-name")]
            author = " ".join(x.strip() for x in parts if x.strip()) or (a.findtext(f"{FB2_NS}nickname") or "").strip()
        meta_lang = (info.findtext(f"{FB2_NS}lang") or "").strip()
        img = info.find(f"{FB2_NS}coverpage/{FB2_NS}image")
        if img is not None:
            href = next((v for k, v in img.attrib.items() if k.endswith("href")), "").lstrip("#")
            for b in root.findall(f"{FB2_NS}binary"):
                if b.get("id") == href and b.text:
                    try:
                        ext = (b.get("content-type") or "image/jpeg").split("/")[-1].replace("jpeg", "jpg")
                        cover = (f"cover.{ext}", base64.b64decode(b.text))
                    except ValueError:
                        pass
    chapters: list[dict] = []
    for body in root.findall(f"{FB2_NS}body"):
        if body.get("name") in ("notes", "comments"):
            continue  # сноски — отдельное тело, не главы
        _fb2_sections(body, chapters)
        if not chapters:  # тело без секций
            paras: list[str] = []
            _fb2_paragraphs(body, paras)
            if paras:
                chapters.append({"title": paras[0], "paragraphs": paras})
    text_sample = "\n".join(p for ch in chapters[:5] for p in ch["paragraphs"])
    lang = detect_lang(text_sample or title)
    from .lang import LANGS, normalize
    ml = normalize(meta_lang)
    if ml in LANGS and lang not in ("zh", "ja", "ko") and ml not in ("zh", "ja", "ko"):
        lang = ml
    return {"title": title, "author": author, "lang": lang, "cover": cover, "chapters": chapters,
            "toc_used": True}


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
                chapters.append({"title": _("Часть {0}").format(len(chapters) + 1), "paragraphs": [_("Часть {0}").format(len(chapters) + 1)] + cur})
                cur, size = [], 0
        if cur:
            chapters.append({"title": _("Часть {0}").format(len(chapters) + 1), "paragraphs": [_("Часть {0}").format(len(chapters) + 1)] + cur})
    return {"title": path.stem, "author": "", "lang": detect_lang(text), "cover": None, "chapters": chapters,
            "toc_used": False}


# ---------- создание книги ----------
def create_book(source: Path, library: Path | None = None, title: str | None = None,
                fix_breaks: bool = False, source_lang: str | None = None,
                target_lang: str | None = None, on_progress=None) -> tuple[Workspace, dict]:
    """EPUB / TXT / папка с главами → рабочая папка книги. Возвращает (workspace, отчёт).
    source_lang — язык оригинала (по умолчанию определяется по тексту), target_lang — язык перевода."""
    from .lang import normalize
    source = Path(source)
    source_lang, target_lang = normalize(source_lang), normalize(target_lang)
    if source.is_dir():
        ws = Workspace(source)
        ws.init()
        n = len(ws.chapters())
        sample = "\n".join(ws.source_paragraphs(min(ws.chapters()))) if n else ""
        ws.meta["source_lang"] = source_lang or (detect_lang(sample) if sample else ws.meta.get("source_lang", "zh"))
        if target_lang:
            ws.meta["target_lang"] = target_lang
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
    ext = ".fb2" if source.name.lower().endswith((".fb2", ".fb2.zip")) else source.suffix.lower()
    book = (read_epub(source, on_progress) if ext == ".epub" else read_fb2(source) if ext == ".fb2"
            else read_txt(source) if ext in (".txt", ".text") else None)
    if book is None:
        raise ValueError(_("Формат {0} не поддерживается: EPUB, FB2, TXT или папка с главами").format(ext))
    if not book["chapters"]:
        raise ValueError(_("Не удалось найти главы в файле"))
    name = re.sub(r'[\\/:*?"<>|]+', " ", title or book["title"] or source.stem).strip()[:80] or "book"
    root = (library or source.parent) / name
    if Workspace(root).chapters():
        raise ValueError(_("В {0} уже есть главы ({1}) — выберите другую папку или удалите их").format(
            Workspace(root).source_dir, len(Workspace(root).chapters())))
    # Пишем во временную папку и переименовываем в конце: прерванный импорт (приложение закрыли, сбой) не
    # оставляет полкниги, которая потом мешает повторному импорту.
    final_root = root
    root = root.parent / f".importing-{name}"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    ws = Workspace(root)
    from .i18n import chapter_file, folder_names
    src_dir, tgt_dir = folder_names()
    book["lang"] = source_lang or book["lang"]
    ws.meta.update({"title": name, "original_title": book["title"], "author": book.get("author", ""),
                    "source_lang": book["lang"], "target_lang": target_lang or ws.meta.get("target_lang", "ru"),
                    "source_file": str(source), "imported_at": time.strftime("%Y-%m-%d %H:%M"),
                    "source_dir": src_dir, "target_dir": tgt_dir, "chapter_file": chapter_file()})
    ws.init()
    existing = ws.chapters()
    if existing:
        raise ValueError(_("В {0} уже есть главы ({1}) — выберите другую папку или удалите их").format(ws.source_dir, len(existing)))
    fixed = 0
    for i, ch in enumerate(book["chapters"], 1):
        paras, cnt = fix_line_breaks(ch["paragraphs"])
        fixed += cnt
        atomic_write(ws.source_dir / ws.meta["chapter_file"].format(n=i), "\n".join(paras), durable=False)
        if on_progress and (i % 50 == 0 or i == len(book["chapters"])):
            on_progress("write", i, len(book["chapters"]))
    if book.get("cover"):
        ext_c = Path(book["cover"][0]).suffix or ".jpg"
        (ws.root / f"cover{ext_c}").write_bytes(book["cover"][1])
    atomic_write(ws.meta_dir / "source_map.json",
                 json.dumps([{"n": i, "title": ch["title"]} for i, ch in enumerate(book["chapters"], 1)],
                            ensure_ascii=False, indent=1))
    chars = sum(len(p) for ch in book["chapters"] for p in ch["paragraphs"])
    target_lang_final = ws.meta["target_lang"]
    if final_root.exists():
        shutil.rmtree(final_root)  # пустая папка без глав (проверено выше)
    os.replace(root, final_root)
    ws = Workspace(final_root)
    return ws, {"chapters": len(book["chapters"]), "lang": book["lang"], "target_lang": target_lang_final,
                "title": book["title"],
                "author": book.get("author", ""), "characters": chars, "cover": bool(book.get("cover")),
                "split": _("по оглавлению") if book.get("toc_used") else _("по документам/заголовкам"),
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
        raise ValueError(_("Нет переведённых глав для сборки"))
    title = title or ws.meta.get("title_ru") or ws.meta.get("title") or ws.root.name
    author = author or ws.meta.get("author_ru") or ws.meta.get("author") or _("Неизвестный автор")
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
        raise ValueError(_("Формат: epub | fb2 | txt"))
    return {"path": str(out), "format": fmt, "chapters": len(items), "missing_chapters": missing[:50],
            "missing_total": len(missing), "bytes": out.stat().st_size, "cover": bool(_cover(ws))}


def _x(s: str) -> str:
    return html.escape(s, quote=True)


def _write_epub(ws: Workspace, items, out: Path, title: str, author: str):
    uid = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "polytome:" + ws.meta.get("title", "") + title))
    cover = _cover(ws)
    lang = ws.meta.get("target_lang", "ru")
    cover_word, toc_word = BOOK_WORDS.get(lang, BOOK_WORDS["en"])
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
            z.writestr("OEBPS/text/cover.xhtml", _xhtml(cover_word, f'<div style="text-align:center"><img '
                       f'src="../images/cover{cover.suffix}" alt="cover" style="max-width:100%;max-height:100%"/></div>', lang),
                       compress_type=zipfile.ZIP_DEFLATED)
            manifest.append('<item id="cover" href="text/cover.xhtml" media-type="application/xhtml+xml"/>')
            spine.append('<itemref idref="cover"/>')
        for i, (n, t, body) in enumerate(items, 1):
            fid = f"ch{n:05d}"
            content = f"<h2>{_x(t)}</h2>\n" + "\n".join(f"<p>{_x(p)}</p>" for p in body)
            z.writestr(f"OEBPS/text/{fid}.xhtml", _xhtml(t, content, lang), compress_type=zipfile.ZIP_DEFLATED)
            manifest.append(f'<item id="{fid}" href="text/{fid}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{fid}"/>')
            nav.append(f'<li><a href="text/{fid}.xhtml">{_x(t)}</a></li>')
            ncx.append(f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{_x(t)}</text></navLabel>'
                       f'<content src="text/{fid}.xhtml"/></navPoint>')
        z.writestr("OEBPS/nav.xhtml", _xhtml(toc_word, f'<nav epub:type="toc" id="toc"><h2>{toc_word}</h2><ol>'
                   + "".join(nav) + "</ol></nav>", lang), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", '<?xml version="1.0" encoding="UTF-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
                   f'version="2005-1"><head><meta name="dtb:uid" content="{uid}"/></head><docTitle><text>{_x(title)}'
                   f'</text></docTitle><navMap>{"".join(ncx)}</navMap></ncx>', compress_type=zipfile.ZIP_DEFLATED)
        meta_cover = '<meta name="cover" content="cover-img"/>' if cover else ""
        opf = (f'<?xml version="1.0" encoding="UTF-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
               f'unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
               f'<dc:identifier id="uid">{uid}</dc:identifier><dc:title>{_x(title)}</dc:title>'
               f'<dc:creator>{_x(author)}</dc:creator><dc:language>{_x(lang)}</dc:language>'
               f'<meta property="dcterms:modified">{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}</meta>'
               f'{meta_cover}</metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
               f'properties="nav"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
               f'<item id="css" href="style.css" media-type="text/css"/>{"".join(manifest)}</manifest>'
               f'<spine toc="ncx">{"".join(spine)}</spine></package>')
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
    shutil.move(str(tmp), str(out))


# Служебные слова внутри собранной книги — на языке перевода.
BOOK_WORDS = {"ru": ("Обложка", "Оглавление"), "uk": ("Обкладинка", "Зміст"), "en": ("Cover", "Contents"),  # i18n: skip
              "es": ("Portada", "Índice"), "pt": ("Capa", "Sumário"), "fr": ("Couverture", "Table des matières"),
              "de": ("Cover", "Inhalt"), "it": ("Copertina", "Indice"), "pl": ("Okładka", "Spis treści"),
              "tr": ("Kapak", "İçindekiler"), "vi": ("Bìa", "Mục lục"), "id": ("Sampul", "Daftar isi"),
              "ja": ("表紙", "目次"), "zh": ("封面", "目录"), "ko": ("표지", "목차")}


def _xhtml(title: str, body: str, lang: str = "ru") -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" '
            f'xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}"><head><meta charset="utf-8"/>'
            f'<title>{_x(title)}</title><link rel="stylesheet" type="text/css" href="../style.css"/></head>'
            f'<body>{body}</body></html>')


def _write_fb2(ws: Workspace, items, out: Path, title: str, author: str):
    cover = _cover(ws)
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">',
             "<description><title-info><genre>sf</genre>",
             f"<author><nickname>{_x(author)}</nickname></author>", f"<book-title>{_x(title)}</book-title>",
             f"<lang>{_x(ws.meta.get('target_lang', 'ru'))}</lang>", f"<src-lang>{_x(ws.meta.get('source_lang', 'zh'))}</src-lang>"]
    if cover:
        parts.append('<coverpage><image l:href="#cover.jpg"/></coverpage>')
    parts += ["</title-info>", "<document-info><author><nickname>Polytome</nickname></author>",
              "<program-used>Polytome</program-used>", f"<date>{time.strftime('%Y-%m-%d')}</date>",
              f"<id>{uuid.uuid5(uuid.NAMESPACE_URL, 'polytome-fb2:' + title)}</id><version>1.0</version>",
              "</document-info></description><body>", f"<title><p>{_x(title)}</p></title>"]
    for _n, t, body in items:
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
        out[n] = paras[0] if paras else _("Глава {0}").format(n)
    return out


__all__ = ["create_book", "export_book", "read_epub", "read_fb2", "read_txt", "detect_lang", "chapter_number"]
