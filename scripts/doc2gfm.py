#!/usr/bin/env python3
"""Batch-convert documents of many formats into GitHub Flavored Markdown.

One file, a folder, or a whole tree. The directory layout is mirrored into the
output folder, every conversion is recorded in a manifest, and re-running only
touches files whose source changed. Nothing is ever written over the input.

Usage:
    python3 doc2gfm.py INPUT [INPUT ...] -o OUTDIR [options]
    python3 doc2gfm.py FILE --stdout

The second form converts one file and prints the Markdown, writing nothing to
disk. It is the shape an AI assistant wants: read a PDF or a Word file as
text, at a fraction of the tokens the raw file would cost, without leaving a
folder behind.

Run with --help for the full option list, or read docs/formats.md for the
routing table (which extension goes through which engine).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

VERSION = "1.1.0"

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"


def app_support_dir() -> Path:
    """Where the Document to Markdown app keeps the engines it installs.

    The app downloads Pandoc and the reader libraries into its own folder
    rather than onto the system, so a terminal, a Claude Code skill or an MCP
    server running this script directly would never see them. Looking here
    means anyone who has set the app up once has a working converter
    everywhere. app/server.py defines the same folder; the two must agree.
    """
    override = os.environ.get("DOC2MD_HOME")
    if override:
        return Path(override)
    if IS_MAC:
        return Path.home() / "Library/Application Support/Document to Markdown"
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        return Path(base) / "Document to Markdown"
    return Path.home() / ".local/share/document-to-markdown"


APP_SUPPORT = app_support_dir()
if (APP_SUPPORT / "python").is_dir():
    # The reader libraries the app installed: pymupdf4llm and openpyxl.
    sys.path.append(str(APP_SUPPORT / "python"))

# --------------------------------------------------------------------------
# Routing table: extension -> (route, argument)
#
# route "pandoc"      arg = pandoc input format name
# route "office"      arg = LibreOffice target filter; result is re-routed
# route "pptx"        arg unused; native slide extractor
# route "pdf"         arg unused; pdf text pipeline
# route "sheet"       arg unused; spreadsheet -> html -> gfm
# route "ansi"        arg unused; terminal capture -> fenced block
# route "text"        arg = fence language ("" copies through as markdown)
# --------------------------------------------------------------------------

EXT_ROUTES: dict[str, tuple[str, str]] = {}


def _add(route: str, arg: str, *exts: str) -> None:
    for ext in exts:
        EXT_ROUTES[ext] = (route, arg)


# Word processor formats
_add("pandoc", "docx", ".docx", ".docm")
_add("pandoc", "odt", ".odt")
_add("pandoc", "rtf", ".rtf")
_add("office", "docx", ".doc", ".dot", ".wpd", ".wps", ".sxw", ".stw", ".abw",
     ".lwp", ".hwp", ".uot", ".pages", ".fodt")
# HTML formats
_add("pandoc", "html", ".html", ".htm", ".xhtml", ".shtml")
# Wiki markup formats
_add("pandoc", "mediawiki", ".mediawiki", ".wiki")
_add("pandoc", "dokuwiki", ".dokuwiki")
_add("pandoc", "tikiwiki", ".tikiwiki")
_add("pandoc", "twiki", ".twiki")
_add("pandoc", "vimwiki", ".vimwiki")
_add("pandoc", "jira", ".jira")
_add("pandoc", "creole", ".creole")
_add("pandoc", "muse", ".muse")
# Ebooks
_add("pandoc", "epub", ".epub")
_add("pandoc", "fb2", ".fb2")
# Documentation formats
_add("pandoc", "docbook", ".docbook", ".dbk")
_add("pandoc", "jats", ".jats")
_add("pandoc", "texinfo", ".texi", ".texinfo")
_add("pandoc", "haddock", ".haddock")
_add("pandoc", "rst", ".rst", ".rest")
_add("pandoc", "textile", ".textile")
_add("asciidoc", "", ".adoc", ".asciidoc", ".asc")
_add("pandoc", "t2t", ".t2t")
_add("pandoc", "pod", ".pod")
_add("pandoc", "man", ".man")
# Roff
for _n in range(1, 10):
    _add("pandoc", "man", f".{_n}")
_add("pandoc", "man", ".roff", ".nroff", ".troff", ".groff")
# Slide show formats
_add("pptx", "", ".pptx", ".pptm")
_add("office", "pptx", ".ppt", ".odp", ".key", ".sxi", ".pot", ".fodp")
# Data formats
_add("pandoc", "csv", ".csv")
_add("pandoc", "tsv", ".tsv", ".tab")
_add("sheet", "", ".xlsx", ".xlsm", ".xls", ".ods", ".numbers", ".fods", ".dif")
_add("text", "json", ".json")
_add("text", "yaml", ".yaml", ".yml")
_add("text", "toml", ".toml")
_add("text", "ini", ".ini", ".cfg", ".conf")
# TeX formats
_add("pandoc", "latex", ".tex", ".latex", ".ltx", ".sty")
_add("pandoc", "typst", ".typ")
# XML formats
_add("pandoc", "opml", ".opml")
_add("text", "xml", ".xml", ".xsd", ".xsl", ".svg", ".rss", ".atom")
# Terminal output
_add("ansi", "", ".ansi", ".log", ".out", ".console", ".ttyrec")
_add("text", "", ".txt", ".text", ".nfo")
# Outline formats
_add("pandoc", "org", ".org")
# Custom / notebook / lightweight markup
_add("pandoc", "ipynb", ".ipynb")
_add("pandoc", "native", ".native")
_add("pandoc", "json", ".pandoc.json")
_add("pandoc", "markdown", ".md", ".markdown", ".mdown", ".mkd", ".mdwn", ".mdx")
_add("pandoc", "commonmark_x", ".commonmark")
_add("pandoc", "djot", ".dj")
# Bibliography formats
_add("biblio", "bibtex", ".bib")
_add("biblio", "biblatex", ".bibtex", ".biblatex")
_add("biblio", "csljson", ".csljson")
_add("biblio", "ris", ".ris")
_add("biblio", "endnotexml", ".enl")
# PDF
_add("pdf", "", ".pdf")

# Never converted: carried in the report as skipped.
BINARY_SKIP = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".ico",
    ".mp3", ".mp4", ".mov", ".avi", ".wav", ".m4a", ".webm", ".mkv",
    ".zip", ".gz", ".tar", ".bz2", ".xz", ".7z", ".rar", ".dmg", ".iso",
    ".ttf", ".otf", ".woff", ".woff2", ".eot", ".psd", ".ai", ".sketch",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".db", ".sqlite", ".pyc",
}

# A GUI launcher hands us a bare PATH, and LibreOffice never appears on any
# PATH at all: it lives inside its application bundle. Look there too, and in
# the app's own bin folder first, so a Pandoc the app installed is found
# before one that happens to be on PATH.
EXTRA_TOOL_DIRS = [str(APP_SUPPORT / "bin")]
if IS_WINDOWS:
    _program_files = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                      os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                      os.environ.get("LOCALAPPDATA", "")]
    for _base in [b for b in _program_files if b]:
        EXTRA_TOOL_DIRS += [
            str(Path(_base) / "Pandoc"),
            str(Path(_base) / "LibreOffice" / "program"),
            str(Path(_base) / "poppler" / "Library" / "bin"),
        ]
else:
    EXTRA_TOOL_DIRS += [
        "/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin",
        str(Path.home() / ".local/bin"),
        "/Applications/LibreOffice.app/Contents/MacOS",
        str(Path.home() / "Applications/LibreOffice.app/Contents/MacOS"),
        "/usr/lib/libreoffice/program", "/opt/libreoffice/program",
        "/snap/bin",
    ]

# Routes that cannot run without Pandoc. The others (PDF, slides,
# spreadsheets, text) read the file themselves, so a machine without Pandoc
# can still convert those, and is told per file about anything it cannot.
NEEDS_PANDOC = {"pandoc", "office", "asciidoc", "biblio"}

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\r(?!\n)")
EM_DASH_RE = re.compile(r"\s*(?:—|–)\s*")  # noqa: RUF001 - em and en dash


# --------------------------------------------------------------------------
# Job bookkeeping
# --------------------------------------------------------------------------

@dataclass
class Job:
    source: Path
    dest: Path
    route: str
    arg: str
    status: str = "pending"      # converted | skipped | unchanged | failed
    detail: str = ""
    bytes_in: int = 0
    bytes_out: int = 0
    sha256: str = ""
    warnings: list[str] = field(default_factory=list)


class ConversionError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_tool(binary: str) -> str | None:
    """Absolute path of a tool, searching PATH and the usual Mac locations."""
    # Our own folders come before PATH: a Pandoc the app installed is the one
    # the app tested, and beats whatever an old Homebrew left behind.
    names = [binary, binary + ".exe"] if IS_WINDOWS else [binary]
    for directory in EXTRA_TOOL_DIRS:
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return shutil.which(binary)


def have(binary: str) -> bool:
    return find_tool(binary) is not None


def run(cmd: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        tail = " / ".join(err[-3:]) if err else f"exit {proc.returncode}"
        raise ConversionError(f"{Path(cmd[0]).name}: {tail}")
    return proc


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def sniff_route(path: Path) -> tuple[str, str] | None:
    """Best-effort routing for files whose extension says nothing."""
    try:
        with path.open("rb") as handle:
            head = handle.read(4096)
    except OSError:
        return None
    if head.startswith(b"%PDF"):
        return ("pdf", "")
    if head.startswith(b"{\\rtf"):
        return ("pandoc", "rtf")
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile:
            return None
        if any(n.startswith("word/") for n in names):
            return ("pandoc", "docx")
        if any(n.startswith("ppt/") for n in names):
            return ("pptx", "")
        if any(n.startswith("xl/") for n in names):
            return ("sheet", "")
        mimetype = ""
        if "mimetype" in names:
            with zipfile.ZipFile(path) as zf:
                mimetype = zf.read("mimetype").decode("ascii", "replace")
        if "epub" in mimetype:
            return ("pandoc", "epub")
        if "opendocument.text" in mimetype:
            return ("pandoc", "odt")
        if "opendocument.presentation" in mimetype:
            return ("office", "pptx")
        if "opendocument.spreadsheet" in mimetype:
            return ("sheet", "")
        return None
    lowered = head.lstrip()[:512].lower()
    if lowered.startswith((b"<!doctype html", b"<html")):
        return ("pandoc", "html")
    if lowered.startswith(b"<?xml"):
        return ("text", "xml")
    if b"\x00" in head:
        return None
    return ("ansi", "")


def route_for(path: Path) -> tuple[str, str] | None:
    ext = path.suffix.lower()
    if ext in BINARY_SKIP:
        return None
    for compound in (".pandoc.json",):
        if path.name.lower().endswith(compound):
            return EXT_ROUTES[compound]
    if ext in EXT_ROUTES:
        return EXT_ROUTES[ext]
    return sniff_route(path)


# --------------------------------------------------------------------------
# Converters. Each returns the GFM body as a string.
# --------------------------------------------------------------------------

def pandoc_gfm(args: argparse.Namespace, src: Path, fmt: str,
               media_dir: Path | None, job: Job) -> str:
    cmd = [find_tool("pandoc") or "pandoc",
           "--from", fmt, "--to", "gfm", "--markdown-headings=atx",
           f"--wrap={args.wrap}"]
    if args.wrap == "auto":
        cmd.append(f"--columns={args.columns}")
    if args.standalone_toc:
        cmd.append("--toc")
    if media_dir is not None:
        cmd += ["--extract-media", str(media_dir)]
    cmd += ["--", str(src)]
    proc = run(cmd, timeout=args.timeout)
    warn = proc.stderr.decode("utf-8", "replace").strip()
    if warn:
        job.warnings.append(warn.splitlines()[0][:200])
    return proc.stdout.decode("utf-8", "replace")


def libreoffice_convert(args: argparse.Namespace, src: Path, target: str,
                        workdir: Path) -> Path:
    binary = find_tool("soffice") or find_tool("libreoffice")
    if binary is None:
        raise ConversionError(
            "LibreOffice not installed; needed for legacy Office formats "
            "(see scripts/setup.sh)")
    profile = workdir / "loprofile"
    out = workdir / "lo"
    out.mkdir(parents=True, exist_ok=True)
    # as_uri() rather than "file://" + path: a Windows path needs the
    # file:///C:/ form, and a space in any path needs escaping.
    run([binary, f"-env:UserInstallation={profile.as_uri()}", "--headless",
         "--norestore", "--convert-to", target, "--outdir", str(out), str(src)],
        timeout=args.timeout)
    produced = sorted(p for p in out.iterdir() if p.is_file())
    if not produced:
        raise ConversionError(f"LibreOffice produced no {target} output")
    return produced[0]


def convert_asciidoc(args: argparse.Namespace, src: Path, media_dir: Path | None,
                    job: Job, workdir: Path) -> str:
    """AsciiDoc: pandoc cannot read it, so asciidoctor renders DocBook first."""
    asciidoctor = find_tool("asciidoctor")
    if asciidoctor is None:
        raise ConversionError(
            "asciidoctor not installed; needed for AsciiDoc input "
            "(gem install asciidoctor, or apt install asciidoctor)")
    docbook = workdir / "asciidoc.xml"
    proc = run([asciidoctor, "--backend", "docbook", "--out-file", "-",
                str(src)], timeout=args.timeout)
    docbook.write_bytes(proc.stdout)
    job.warnings.append("rendered to DocBook by asciidoctor first")
    body = pandoc_gfm(args, docbook, "docbook", media_dir, job)
    # The document title lives in DocBook metadata, which the gfm writer drops.
    title = re.search(rb"<title>(.*?)</title>", proc.stdout, re.S)
    if title and not body.lstrip().startswith("#"):
        text = title.group(1).decode("utf-8", "replace").strip()
        if text:
            body = f"# {text}\n\n{body}"
    return body


def convert_office(args: argparse.Namespace, src: Path, target: str,
                   media_dir: Path | None, job: Job, workdir: Path) -> str:
    intermediate = libreoffice_convert(args, src, target, workdir)
    job.warnings.append(f"converted via LibreOffice to .{target} first")
    if target == "pptx":
        return convert_pptx(args, intermediate, media_dir, job)
    return pandoc_gfm(args, intermediate, target, media_dir, job)


# --- Slides ---------------------------------------------------------------

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"


ENTITY_RE = re.compile(rb"<!ENTITY", re.IGNORECASE)


def parse_xml(data: bytes) -> ET.Element:
    """Parse XML found inside a document, refusing one that declares entities.

    ElementTree expands entities a document defines about itself, so a few
    lines of XML can unpack into gigabytes of memory while being read. No real
    slide deck declares any, so a file that does is refused rather than read.
    """
    if ENTITY_RE.search(data[:65536]):
        raise ConversionError("the file declares XML entities and was not read")
    return ET.fromstring(data)  # noqa: S314 - entity declarations refused above


def _para_text(para: ET.Element) -> str:
    return "".join(t.text or "" for t in para.iter(f"{_A}t")).strip()


def _para_level(para: ET.Element) -> int:
    props = para.find(f"{_A}pPr")
    if props is None:
        return 0
    try:
        return int(props.get("lvl", "0") or 0)
    except ValueError:
        return 0


def _is_title(shape: ET.Element) -> bool:
    for placeholder in shape.iter(f"{_P}ph"):
        if (placeholder.get("type") or "") in ("title", "ctrTitle"):
            return True
    return False


def _table_rows(frame: ET.Element) -> list[list[str]]:
    rows = []
    for row in frame.iter(f"{_A}tr"):
        cells = []
        for cell in row.findall(f"{_A}tc"):
            texts = [_para_text(para) for para in cell.iter(f"{_A}p")]
            cells.append(" ".join(t for t in texts if t))
        rows.append(cells)
    return rows


def _gfm_table(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    escaped = [[c.replace("|", "\\|") for c in row] for row in rows]
    out = ["| " + " | ".join(escaped[0]) + " |",
           "| " + " | ".join("---" for _ in range(width)) + " |"]
    for row in escaped[1:]:
        out.append("| " + " | ".join(row) + " |")
    return out


def _slide_body(xml_bytes: bytes) -> tuple[str, list[str]]:
    """Return (title, body lines) for one slide, in shape order."""
    root = parse_xml(xml_bytes)
    tree = root.find(f".//{_P}cSld/{_P}spTree")
    title = ""
    lines: list[str] = []
    for shape in list(tree) if tree is not None else []:
        tag = shape.tag
        if tag == f"{_P}sp":
            paragraphs = [(_para_level(para), _para_text(para))
                          for para in shape.iter(f"{_A}p")]
            paragraphs = [(lvl, text) for lvl, text in paragraphs if text]
            if not paragraphs:
                continue
            if not title and _is_title(shape):
                title = paragraphs[0][1]
                paragraphs = paragraphs[1:]
            for level, text in paragraphs:
                lines.append(f"{'  ' * level}- {text}")
        elif tag == f"{_P}graphicFrame":
            rows = _table_rows(shape)
            if rows:
                lines.append("")
                lines += _gfm_table(rows)
                lines.append("")
        elif tag == f"{_P}pic":
            name = ""
            for prop in shape.iter(f"{_P}cNvPr"):
                name = prop.get("descr") or prop.get("name") or ""
                break
            lines.append(f"![{name}](#image-on-slide)")
    if not title and lines and lines[0].startswith("- "):
        title = lines.pop(0)[2:]
    return title, lines


def _notes_text(xml_bytes: bytes, slide_number: int) -> list[str]:
    root = parse_xml(xml_bytes)
    out = []
    for para in root.iter(f"{_A}p"):
        text = _para_text(para)
        if text and text != str(slide_number):
            out.append(text)
    return out


def convert_pptx(args: argparse.Namespace, src: Path, media_dir: Path | None,
                 job: Job) -> str:
    """Read .pptx/.pptm directly: title, body, tables and notes, slide by slide."""
    out: list[str] = []
    with zipfile.ZipFile(src) as zf:
        names = zf.namelist()
        slides = sorted(
            (n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.search(r"(\d+)", Path(n).name).group(1)))
        notes = {
            int(re.search(r"(\d+)", Path(n).name).group(1)): n
            for n in names
            if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n)
        }
        for index, name in enumerate(slides, start=1):
            title, lines = _slide_body(zf.read(name))
            heading = f"## Slide {index}"
            if title:
                heading += f": {title}"
            out += [heading, ""]
            if lines:
                out += lines
            else:
                out.append("*(no text on this slide)*")
            if index in notes:
                note_lines = _notes_text(zf.read(notes[index]), index)
                if note_lines:
                    out += ["", "> **Speaker notes**", ">"]
                    out += [f"> {line}" for line in note_lines]
            out.append("")
        if media_dir is not None:
            images = [n for n in names if n.startswith("ppt/media/")]
            if images:
                media_dir.mkdir(parents=True, exist_ok=True)
                for image in images:
                    (media_dir / Path(image).name).write_bytes(zf.read(image))
                job.warnings.append(
                    f"{len(images)} embedded image(s) extracted to "
                    f"{media_dir.name}/")
    return "\n".join(out).strip() + "\n"


# --- Spreadsheets ---------------------------------------------------------

def _trim(rows: list[list[str]]) -> list[list[str]]:
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    width = 0
    for row in rows:
        for index, cell in enumerate(row):
            if cell.strip():
                width = max(width, index + 1)
    return [row[:width] for row in rows]


def _sheet_via_openpyxl(src: Path, job: Job) -> str | None:
    """Preferred for .xlsx/.xlsm: keeps sheet names and real header rows."""
    if src.suffix.lower() not in (".xlsx", ".xlsm"):
        return None
    try:
        import openpyxl  # type: ignore
    except ImportError:
        return None
    book = openpyxl.load_workbook(src, data_only=True, read_only=True)
    out: list[str] = []
    for sheet in book.worksheets:
        rows = _trim([
            ["" if cell is None else str(cell).strip() for cell in row]
            for row in sheet.iter_rows(values_only=True)
        ])
        out.append(f"## {sheet.title}")
        out.append("")
        out += _gfm_table(rows) if rows else ["*(empty sheet)*"]
        out.append("")
    book.close()
    job.warnings.append("spreadsheet read with openpyxl")
    return "\n".join(out).strip() + "\n"


def _promote_blank_header(body: str) -> str:
    """LibreOffice HTML export has no <thead>, so pandoc writes an empty
    header row. Use the first data row as the header instead."""
    lines = body.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        is_blank_header = (
            line.startswith("|")
            and not line.strip("| \t")
            and index + 2 < len(lines)
            and set(lines[index + 1].replace(" ", "")) <= set("|-:")
            and lines[index + 2].startswith("|")
        )
        if is_blank_header:
            out.append(lines[index + 2])
            out.append(lines[index + 1])
            index += 3
            continue
        out.append(line)
        index += 1
    return "\n".join(out)


def convert_sheet(args: argparse.Namespace, src: Path, job: Job,
                  workdir: Path) -> str:
    """Every sheet becomes a GFM table; sheet names become headings."""
    native = _sheet_via_openpyxl(src, job)
    if native:
        return native
    html = libreoffice_convert(args, src, "html", workdir)
    body = pandoc_gfm(args, html, "html", None, job)
    return re.sub(r"\n{3,}", "\n\n", _promote_blank_header(body))


# --- PDF ------------------------------------------------------------------

def _pdf_via_pymupdf(src: Path) -> str | None:
    try:
        import pymupdf4llm  # type: ignore
    except ImportError:
        return None
    return pymupdf4llm.to_markdown(str(src))


def _pdf_via_markitdown(src: Path) -> str | None:
    try:
        from markitdown import MarkItDown  # type: ignore
    except ImportError:
        return None
    return MarkItDown().convert(str(src)).text_content


def _pdf_via_pdftotext(args: argparse.Namespace, src: Path, job: Job) -> str:
    pdftotext = find_tool("pdftotext")
    if pdftotext is None:
        raise ConversionError(
            "no PDF engine available; install poppler-utils, pymupdf4llm or "
            "markitdown (see scripts/setup.sh)")
    proc = run([pdftotext, "-layout", "-enc", "UTF-8", str(src), "-"],
               timeout=args.timeout)
    text = proc.stdout.decode("utf-8", "replace")
    pages = text.split("\f")
    chunks: list[str] = []
    for number, page in enumerate(pages, start=1):
        page = page.rstrip()
        if not page.strip():
            continue
        if args.pdf_page_marks:
            chunks.append(f"<!-- page {number} -->\n")
        paragraphs = [
            re.sub(r"[ \t]*\n[ \t]*", " ", block).strip()
            for block in re.split(r"\n[ \t]*\n", page)
        ]
        chunks.append("\n\n".join(p for p in paragraphs if p))
    return "\n\n".join(chunks).strip() + "\n"


def convert_pdf(args: argparse.Namespace, src: Path, job: Job,
                workdir: Path) -> str:
    order = (["pdftotext", "pymupdf", "markitdown"]
             if args.pdf_engine == "pdftotext" else
             ["pymupdf", "markitdown", "pdftotext"])
    if args.pdf_engine not in ("auto", "pdftotext"):
        order = [args.pdf_engine]
    body = ""
    errors: list[str] = []
    for engine in order:
        try:
            if engine == "pymupdf":
                body = _pdf_via_pymupdf(src) or ""
            elif engine == "markitdown":
                body = _pdf_via_markitdown(src) or ""
            else:
                body = _pdf_via_pdftotext(args, src, job)
        except ConversionError as exc:
            errors.append(str(exc))
            body = ""
        except Exception as exc:  # noqa: BLE001 - one engine failing is not the file failing
            # PyMuPDF raises its own exceptions on an encrypted or damaged
            # PDF. The next engine may still read it, so try it before giving
            # up on the file.
            errors.append(f"{engine}: {type(exc).__name__}: {exc}")
            body = ""
        if body.strip():
            job.warnings.append(f"pdf engine: {engine}")
            break
    if not body.strip():
        if args.ocr and find_tool("ocrmypdf"):
            ocred = workdir / "ocr.pdf"
            run([find_tool("ocrmypdf"), "--force-ocr", "--quiet",
                 str(src), str(ocred)],
                timeout=max(args.timeout, 1800))
            job.warnings.append("no embedded text; OCR applied")
            return _pdf_via_pdftotext(args, ocred, job)
        hint = ("no extractable text (scanned PDF?) - re-run with --ocr and "
                "ocrmypdf installed")
        raise ConversionError("; ".join(errors) or hint)
    return body.strip() + "\n"


# --- Bibliographies -------------------------------------------------------

def _csl_names(entry: dict, key: str) -> str:
    people = entry.get(key) or []
    names = []
    for person in people:
        if "literal" in person:
            names.append(person["literal"])
        else:
            names.append(" ".join(
                part for part in (person.get("given"), person.get("family"))
                if part))
    return ", ".join(n for n in names if n)


def convert_biblio(args: argparse.Namespace, src: Path, fmt: str,
                   job: Job) -> str:
    """Bibliography databases become a readable, sorted GFM reference list."""
    proc = run([find_tool("pandoc") or "pandoc",
                "--from", fmt, "--to", "csljson", "--", str(src)],
               timeout=args.timeout)
    entries = json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
    lines = ["# References", ""]
    for entry in sorted(entries, key=lambda e: str(e.get("id", ""))):
        title = entry.get("title") or entry.get("id") or "Untitled"
        authors = _csl_names(entry, "author") or _csl_names(entry, "editor")
        try:
            year = str(entry["issued"]["date-parts"][0][0])
        except (KeyError, IndexError, TypeError):
            year = ""
        container = entry.get("container-title") or entry.get("publisher") or ""
        doi = entry.get("DOI")
        link = f"https://doi.org/{doi}" if doi else (entry.get("URL") or "")
        bits = [f"**{title}**"]
        if authors:
            bits.append(authors)
        if year:
            bits.append(year)
        if container:
            bits.append(f"*{container}*")
        item = f"- `{entry.get('id', '')}` " + ". ".join(bits)
        if link:
            item += f". <{link}>"
        lines.append(item)
    if len(lines) == 2:
        raise ConversionError("bibliography contains no entries")
    return "\n".join(lines) + "\n"


# --- Terminal captures and plain text ------------------------------------

def _fenced(text: str, language: str) -> str:
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}{language}\n{text.rstrip()}\n{fence}\n"


def _clean_terminal(text: str, job: Job) -> str:
    if "\x1b" in text:
        job.warnings.append("ANSI escape sequences stripped")
        text = ANSI_RE.sub("", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def convert_ansi(src: Path, job: Job) -> str:
    """Terminal captures: strip control codes, keep the output verbatim."""
    return _fenced(_clean_terminal(read_text(src), job), "console")


def convert_text(src: Path, language: str, job: Job) -> str:
    """Structured data files get a fence; plain text passes through as prose."""
    text = _clean_terminal(read_text(src), job)
    if language:
        return _fenced(text, language)
    return text.rstrip() + "\n"


# --------------------------------------------------------------------------
# Per-file driver
# --------------------------------------------------------------------------

def front_matter(job: Job, args: argparse.Namespace) -> str:
    if not args.front_matter:
        return ""
    rel = os.path.relpath(job.source, args.front_matter_base)
    fields = {
        "source": rel.replace("\\", "/"),
        "source_format": job.source.suffix.lstrip(".").lower() or "unknown",
        "converted_by": f"doc2gfm {VERSION}",
        "source_sha256": job.sha256,
    }
    lines = ["---"]
    for key, value in fields.items():
        # A source path can hold quotes and backslashes; escaped, they stay
        # inside the string instead of ending it and breaking every reader.
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}: "{escaped}"')
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def normalize(body: str, args: argparse.Namespace) -> str:
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    if args.no_em_dash:
        body = EM_DASH_RE.sub(", ", body)
    return body.strip() + "\n"


def convert_body(job: Job, args: argparse.Namespace,
                 media_dir: Path | None) -> str:
    """Run the right engine for one job and return the finished Markdown.

    Raises ConversionError (or whatever the engine raised) rather than
    recording it: convert_one turns that into a report line for a batch, and
    --stdout turns it into an exit code.
    """
    src = job.source
    with tempfile.TemporaryDirectory(prefix="doc2gfm-") as tmp:
        workdir = Path(tmp)
        if job.route == "pandoc":
            body = pandoc_gfm(args, src, job.arg, media_dir, job)
        elif job.route == "office":
            body = convert_office(args, src, job.arg, media_dir, job, workdir)
        elif job.route == "asciidoc":
            body = convert_asciidoc(args, src, media_dir, job, workdir)
        elif job.route == "pptx":
            body = convert_pptx(args, src, media_dir, job)
        elif job.route == "sheet":
            body = convert_sheet(args, src, job, workdir)
        elif job.route == "biblio":
            body = convert_biblio(args, src, job.arg, job)
        elif job.route == "pdf":
            body = convert_pdf(args, src, job, workdir)
        elif job.route == "ansi":
            body = convert_ansi(src, job)
        elif job.route == "text":
            body = convert_text(src, job.arg, job)
        else:
            raise ConversionError(f"unknown route {job.route}")

    body = normalize(body, args)
    if media_dir is not None and media_dir.exists():
        # Pandoc writes the absolute media path into every image link. Make
        # them relative, whichever separator this platform used.
        for absolute in {str(media_dir), media_dir.as_posix()}:
            body = body.replace(absolute + "/", media_dir.name + "/")
            body = body.replace(absolute + "\\", media_dir.name + "/")
            body = body.replace(absolute, media_dir.name)
    if not body.strip():
        raise ConversionError("conversion produced an empty document")
    return body


def convert_one(job: Job, args: argparse.Namespace) -> Job:
    src = job.source
    try:
        job.bytes_in = src.stat().st_size
        if args.max_bytes and job.bytes_in > args.max_bytes:
            job.status = "skipped"
            job.detail = f"larger than --max-bytes ({job.bytes_in} bytes)"
            return job
        job.sha256 = sha256_of(src)
        if job.dest.exists() and not args.force:
            existing = read_text(job.dest)
            if job.sha256 and f'source_sha256: "{job.sha256}"' in existing:
                job.status = "unchanged"
                job.bytes_out = job.dest.stat().st_size
                return job
        if args.dry_run:
            job.status = "converted"
            job.detail = "dry run"
            return job

        media_dir = None
        if args.media:
            media_dir = job.dest.with_suffix("").with_name(
                job.dest.with_suffix("").name + ".media")

        body = convert_body(job, args, media_dir)

        job.dest.parent.mkdir(parents=True, exist_ok=True)
        job.dest.write_text(front_matter(job, args) + body, encoding="utf-8")
        job.bytes_out = job.dest.stat().st_size
        job.status = "converted"
    except ConversionError as exc:
        job.status = "failed"
        job.detail = str(exc)
    except subprocess.TimeoutExpired:
        job.status = "failed"
        job.detail = f"timed out after {args.timeout}s"
    except Exception as exc:  # noqa: BLE001 - one bad file never stops the batch
        job.status = "failed"
        job.detail = f"{type(exc).__name__}: {exc}"
    return job


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------

def iter_sources(inputs: list[Path], args: argparse.Namespace):
    for item in inputs:
        if item.is_file():
            yield item, item.parent
            continue
        if not item.is_dir():
            print(f"warning: no such file or directory: {item}", file=sys.stderr)
            continue
        walker = os.walk(item, followlinks=args.follow_symlinks)
        for root, dirs, files in walker:
            dirs[:] = sorted(
                d for d in dirs
                if not (d.startswith(".") and not args.hidden)
                and d not in {"node_modules", "__pycache__"})
            for name in sorted(files):
                if name.startswith(".") and not args.hidden:
                    continue
                yield Path(root) / name, item


def plan(inputs: list[Path], args: argparse.Namespace) -> tuple[list[Job], list[Job]]:
    include = {e if e.startswith(".") else "." + e
               for e in (args.include or [])}
    exclude = {e if e.startswith(".") else "." + e
               for e in (args.exclude or [])}
    jobs: list[Job] = []
    skipped: list[Job] = []
    taken: dict[Path, list[int]] = {}
    for src, base in iter_sources(inputs, args):
        ext = src.suffix.lower()
        if include and ext not in include:
            continue
        if ext in exclude:
            continue
        route = route_for(src)
        if route is None:
            skipped.append(Job(src, Path(), "", "", status="skipped",
                               detail="unsupported or binary file type"))
            continue
        rel = src.relative_to(base)
        dest_rel = Path(rel.name) if args.flat else rel
        dest = (args.out / dest_rel).with_suffix(".md")
        taken.setdefault(dest, []).append(len(jobs))
        jobs.append(Job(src, dest, route[0], route[1]))
    # Two sources that would claim the same .md (report.docx and report.pdf)
    # both take the source extension into their name, so neither result is
    # silently the winner and re-runs stay stable.
    for dest, indices in taken.items():
        if len(indices) < 2:
            continue
        for index in indices:
            job = jobs[index]
            suffix = job.source.suffix.lstrip(".").lower() or "file"
            job.dest = dest.with_name(f"{dest.stem}-{suffix}.md")
    return jobs, skipped


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def write_report(jobs: list[Job], args: argparse.Namespace) -> None:
    by = {k: [j for j in jobs if j.status == k]
          for k in ("converted", "unchanged", "skipped", "failed")}
    lines = [
        "# Conversion report",
        "",
        f"- Converted: {len(by['converted'])}",
        f"- Unchanged (source already converted): {len(by['unchanged'])}",
        f"- Skipped: {len(by['skipped'])}",
        f"- Failed: {len(by['failed'])}",
        "",
    ]
    if by["failed"]:
        lines += ["## Failed", "", "| Source | Reason |", "| --- | --- |"]
        for job in by["failed"]:
            lines.append(f"| `{job.source}` | {job.detail.replace('|', '/')} |")
        lines.append("")
    if by["skipped"]:
        lines += ["## Skipped", "", "| Source | Reason |", "| --- | --- |"]
        for job in by["skipped"]:
            lines.append(f"| `{job.source}` | {job.detail.replace('|', '/')} |")
        lines.append("")
    if by["converted"] or by["unchanged"]:
        lines += ["## Output", "",
                  "| Markdown | Source | Route | Notes |", "| --- | --- | --- | --- |"]
        for job in by["converted"] + by["unchanged"]:
            notes = "; ".join(job.warnings) or ("unchanged"
                                                if job.status == "unchanged" else "")
            lines.append(
                f"| `{job.dest}` | `{job.source}` | {job.route} | "
                f"{notes.replace('|', '/')} |")
        lines.append("")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines), encoding="utf-8")

    manifest = [
        {
            "source": str(job.source),
            "output": str(job.dest) if job.route else None,
            "route": job.route, "status": job.status, "detail": job.detail,
            "sha256": job.sha256, "bytes_in": job.bytes_in,
            "bytes_out": job.bytes_out, "warnings": job.warnings,
        }
        for job in jobs
    ]
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps({"version": VERSION, "files": manifest}, indent=2) + "\n",
        encoding="utf-8")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="doc2gfm",
        description="Convert documents in many formats to GitHub Flavored "
                    "Markdown, one file or a whole folder tree at a time.")
    p.add_argument("inputs", nargs="+", type=Path,
                   help="files and/or folders to convert")
    p.add_argument("-o", "--out", type=Path,
                   help="output folder (created if missing)")
    p.add_argument("--stdout", action="store_true",
                   help="convert one file and print the Markdown instead of "
                        "writing anything: no output folder, no report, no "
                        "media. What an AI assistant wants before reading a "
                        "document.")
    p.add_argument("-j", "--jobs", type=int, default=min(8, (os.cpu_count() or 4)),
                   help="parallel conversions (default: %(default)s)")
    p.add_argument("--force", action="store_true",
                   help="reconvert even when the output is already current")
    p.add_argument("--dry-run", action="store_true",
                   help="plan and report without writing markdown")
    p.add_argument("--flat", action="store_true",
                   help="write every file into the output root instead of "
                        "mirroring the input tree")
    p.add_argument("--include", action="append",
                   help="only these extensions (repeatable, e.g. --include pdf)")
    p.add_argument("--exclude", action="append",
                   help="skip these extensions (repeatable)")
    p.add_argument("--hidden", action="store_true",
                   help="also walk dot-files and dot-folders")
    p.add_argument("--follow-symlinks", action="store_true")
    p.add_argument("--media", dest="media", action="store_true", default=True,
                   help="extract embedded images next to the markdown (default)")
    p.add_argument("--no-media", dest="media", action="store_false")
    # None until resolved below: on by default for files written to disk,
    # off by default for --stdout, where the reader already knows the source.
    p.add_argument("--front-matter", dest="front_matter", action="store_true",
                   default=None, help="write YAML front matter (default when "
                                      "writing files)")
    p.add_argument("--no-front-matter", dest="front_matter", action="store_false")
    p.add_argument("--wrap", choices=("none", "auto", "preserve"), default="none")
    p.add_argument("--columns", type=int, default=88)
    p.add_argument("--toc", dest="standalone_toc", action="store_true",
                   help="prepend a table of contents where pandoc can build one")
    p.add_argument("--pdf-engine", choices=("auto", "pymupdf", "markitdown",
                                            "pdftotext"), default="auto")
    p.add_argument("--pdf-page-marks", action="store_true",
                   help="keep <!-- page N --> comments in PDF output")
    p.add_argument("--ocr", action="store_true",
                   help="run ocrmypdf on PDFs with no extractable text")
    p.add_argument("--no-em-dash", action="store_true",
                   help="replace em and en dashes with commas, for house styles "
                        "that ban them")
    p.add_argument("--max-bytes", type=int, default=0,
                   help="skip sources larger than this (0 = no limit)")
    p.add_argument("--timeout", type=int, default=600,
                   help="per-file timeout in seconds (default: %(default)s)")
    p.add_argument("--report", type=Path,
                   help="report path (default: OUT/_conversion-report.md)")
    p.add_argument("--manifest", type=Path,
                   help="manifest path (default: OUT/_conversion-manifest.json)")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"doc2gfm {VERSION}")
    args = p.parse_args(argv)
    if args.stdout:
        if len(args.inputs) != 1 or not args.inputs[0].is_file():
            p.error("--stdout converts exactly one file")
        if args.out is not None:
            p.error("--stdout writes nothing, so -o has no meaning with it")
        args.media = False       # nowhere to put images that is not a folder
    elif args.out is None:
        p.error("the following arguments are required: -o/--out "
                "(or --stdout to print one file)")
    if args.front_matter is None:
        args.front_matter = not args.stdout
    args.inputs = [i.resolve() for i in args.inputs]
    args.front_matter_base = os.path.commonpath(
        [str(i if i.is_dir() else i.parent) for i in args.inputs])
    if args.stdout:
        return args
    args.out = args.out.resolve()
    args.report = args.report or args.out / "_conversion-report.md"
    args.manifest = args.manifest or args.out / "_conversion-manifest.json"
    return args


def pandoc_missing_message() -> str:
    return ("pandoc is not installed. Run scripts/setup.sh, or open the "
            "Document to Markdown app once and let it set itself up.")


def convert_file_to_markdown(path: Path, *, ocr: bool = False,
                             with_front_matter: bool = False,
                             no_em_dash: bool = False,
                             timeout: int = 600) -> str:
    """Convert one file and return the Markdown, writing nothing to disk.

    This is what --stdout and the MCP server call. It raises ConversionError
    with a plain-language reason when the file cannot be read.
    """
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ConversionError(f"no such file: {source}")
    route = route_for(source)
    if route is None:
        raise ConversionError(
            f"{source.name} is not a document this converter reads "
            "(images, archives and programs are left alone)")
    if route[0] in NEEDS_PANDOC and not have("pandoc"):
        raise ConversionError(pandoc_missing_message())
    argv = [str(source), "--stdout", f"--timeout={timeout}"]
    if ocr:
        argv.append("--ocr")
    if no_em_dash:
        argv.append("--no-em-dash")
    # Said either way: parse_args turns front matter off for --stdout when
    # nothing is said, and the caller has said.
    argv.append("--front-matter" if with_front_matter else "--no-front-matter")
    args = parse_args(argv)
    job = Job(source, source.with_suffix(".md"), route[0], route[1])
    job.sha256 = sha256_of(source)
    try:
        body = convert_body(job, args, None)
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(f"timed out after {timeout}s") from exc
    return front_matter(job, args) + body


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    # A file name the terminal's encoding cannot show must not crash the run
    # that is converting it. Replace what cannot be printed and carry on.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    if args.stdout:
        return print_one(args)
    for item in args.inputs:
        if item.is_dir() and args.out.is_relative_to(item):
            print(f"error: output folder {args.out} sits inside input {item}; "
                  "choose a separate output folder.", file=sys.stderr)
            return 2

    jobs, skipped = plan(args.inputs, args)
    # A Markdown file chosen on its own, with the output folder set to the
    # folder it is in, would be written over itself. The folder check above
    # cannot see that case, because the input is a file, so it is checked
    # per job here.
    for job in jobs:
        if job.dest == job.source:
            print(f"error: {job.source} would be overwritten by its own "
                  "conversion; choose a different output folder.",
                  file=sys.stderr)
            return 2
    if not jobs:
        print("nothing to convert", file=sys.stderr)
        write_report(skipped, args)
        return 1
    if any(job.route in NEEDS_PANDOC for job in jobs) and not have("pandoc"):
        print(f"error: {pandoc_missing_message()}", file=sys.stderr)
        return 2

    done: list[Job] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(convert_one, job, args): job for job in jobs}
        for index, future in enumerate(
                concurrent.futures.as_completed(futures), start=1):
            job = future.result()
            done.append(job)
            if not args.quiet:
                mark = {"converted": "ok", "unchanged": "--", "skipped": "--",
                        "failed": "FAIL"}[job.status]
                suffix = f"  ({job.detail})" if job.detail else ""
                print(f"[{index}/{len(jobs)}] {mark} {job.source.name}"
                      f" -> {job.dest.name}{suffix}", flush=True)

    all_jobs = done + skipped
    write_report(all_jobs, args)
    failed = [j for j in all_jobs if j.status == "failed"]
    converted = [j for j in all_jobs if j.status == "converted"]
    if not args.quiet:
        print(f"\n{len(converted)} converted, "
              f"{len([j for j in all_jobs if j.status == 'unchanged'])} unchanged, "
              f"{len([j for j in all_jobs if j.status == 'skipped'])} skipped, "
              f"{len(failed)} failed")
        print(f"report:   {args.report}")
        print(f"manifest: {args.manifest}")
    return 1 if failed else 0


def print_one(args: argparse.Namespace) -> int:
    """--stdout: one file in, Markdown out, nothing written anywhere."""
    source = args.inputs[0]
    try:
        body = convert_file_to_markdown(
            source, ocr=args.ocr, with_front_matter=args.front_matter,
            no_em_dash=args.no_em_dash, timeout=args.timeout)
    except ConversionError as exc:
        print(f"error: {source.name}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - say what happened, in one line
        print(f"error: {source.name}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    # Bytes, not text: the reader on the other end of a pipe wants UTF-8
    # whatever the terminal's locale says, and a Windows console's code page
    # is the wrong thing to write a document through.
    sys.stdout.flush()
    sys.stdout.buffer.write(body.encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
