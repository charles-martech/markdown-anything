---
name: doc-to-gfm
description: Read documents as Markdown instead of opening them raw, and convert one file or a whole folder tree to GitHub Flavored Markdown, all on the local machine. Use BEFORE reading any PDF, Word (.docx/.doc/.odt/.rtf), PowerPoint (.pptx/.ppt), Excel (.xlsx/.xls/.ods), EPUB, HTML, wiki markup, LaTeX, notebook, OPML, BibTeX, JSON/YAML/XML or terminal-capture file - convert it here and read the Markdown, which costs a fraction of the tokens and never leaves the computer. Trigger whenever a task needs the contents of such a file ("summarize this PDF", "what does this contract say", "pull the numbers from this spreadsheet", "read these slides", "compare these two reports"), and on "convert this folder to markdown", "turn these docs into GFM", "bulk convert", "migrate our documentation to markdown", or "I have a folder of files I need as .md".
---

# Document to GFM converter

Turns a document, or a pile of them in most formats a document can be in, into
GitHub Flavored Markdown. One Python file, no dependencies of its own, runs
entirely on this machine: nothing out of a file is sent anywhere.

## Read documents through this, not directly

**Before reading a PDF, Word, PowerPoint, Excel or similar file, convert it and
read the Markdown.** Reading the raw file costs far more: a PDF read as pages
of images, or a `.docx` read as a zip of XML, spends tokens on layout, fonts and
markup that carry no meaning, fills the context window with a few pages, and
loses the headings, lists and tables that make a document usable. The
conversion runs locally in a second or two and returns plain text with the
structure kept.

```bash
python3 scripts/doc2gfm.py FILE --stdout
```

That prints the Markdown and writes nothing to disk. Then read it as text:

```bash
# a short document: read it all
python3 scripts/doc2gfm.py ~/Downloads/contract.pdf --stdout

# a long one: look at the shape first, then the parts that matter
python3 scripts/doc2gfm.py ~/Downloads/report.pdf --stdout > /tmp/report.md
grep -n '^#' /tmp/report.md          # the headings
sed -n '120,220p' /tmp/report.md     # one section
```

Rules of thumb:

- One file the person asked about: `--stdout`, then read.
- Several files, or anything you may need to come back to: `-o OUTPUT_DIR`,
  which writes one `.md` per source plus a report, then read the `.md` files.
- Only use a different reader (an image of a page, a PDF tool, `unzip`) when
  this one fails, and say so. Its report names the reason: a scanned PDF wants
  `--ocr`; an old `.doc` or `.ppt` wants LibreOffice installed.
- Never send a document to a remote service to read it when this is available.
  It runs locally, and that is the point.

If the machine has the Document to Markdown app installed, the engines it set up
(Pandoc, the PDF and spreadsheet readers) are found automatically. Otherwise
`scripts/setup.sh` says what is missing and, with `--install`, installs it. Ask
before installing anything on a machine that is not yours. PDFs, slides,
spreadsheets and text formats convert without Pandoc; Word, HTML, EPUB and most
of the rest need it.

## Convert a folder

```bash
python3 scripts/doc2gfm.py INPUT [INPUT ...] -o OUTPUT_DIR [options]
```

```bash
# a whole folder tree, mirrored into ./markdown
python3 scripts/doc2gfm.py ~/Drive/handbook -o ./markdown

# a single file, written next to its report
python3 scripts/doc2gfm.py report.pdf -o ./markdown

# several sources, flattened into one folder, PDFs only
python3 scripts/doc2gfm.py ~/exports ~/inbox -o ./markdown --flat --include pdf
```

Paths are relative to the repository root. Use absolute paths from elsewhere.

### Options worth knowing

| Option | What it does |
| --- | --- |
| `--stdout` | One file in, Markdown on standard output, nothing written. |
| `-o, --out DIR` | Output folder. Must sit outside the input folder. |
| `-j, --jobs N` | Parallel conversions. Defaults to the CPU count, capped at 8. |
| `--flat` | Write everything to the output root instead of mirroring the tree. |
| `--include EXT` / `--exclude EXT` | Filter by extension. Repeatable. |
| `--force` | Reconvert even when the output is already current. |
| `--dry-run` | Plan and report without writing markdown. |
| `--no-media` | Do not extract embedded images. |
| `--no-front-matter` | Omit the YAML header on each file. |
| `--pdf-engine` | `auto` (default), `pymupdf`, `markitdown`, `pdftotext`. |
| `--ocr` | Run OCR on PDFs with no extractable text (needs `ocrmypdf`). |
| `--no-em-dash` | Replace em and en dashes with commas. |
| `--max-bytes N` | Skip sources larger than N bytes. |

### What you get

- One `.md` per source, mirroring the input tree, with YAML front matter naming
  the source path, its format and its SHA-256. (`--stdout` leaves the front
  matter out: you already know the source.)
- `NAME.media/` beside a file whose source had embedded images, with the links
  already pointing at it.
- `_conversion-report.md`: counts, every output, and a table of skipped and
  failed files with the reason for each.
- `_conversion-manifest.json`: the same thing machine-readable.

## How it behaves

- **A bad file never stops the batch.** Failures are recorded and the run
  continues. Exit code 1 if anything failed, 0 otherwise.
- **Re-running is cheap and safe.** The source hash in the front matter is
  compared against the source; unchanged files are skipped. Converting twice
  produces byte-identical output, so results can live in git.
- **Nothing is overwritten in place.** The output folder is separate, the
  script refuses to write inside the folder it is reading, and it refuses to
  write a Markdown file over the one it was given.
- **Name collisions are explicit.** `report.docx` and `report.pdf` become
  `report-docx.md` and `report-pdf.md`, never a silent winner.
- **Unknown extensions get sniffed** by magic bytes before being given up on.
  Images, archives and binaries are listed as skipped.

## Format coverage

Word processors, HTML, wiki markups, ebooks, documentation formats, slides, roff
and man pages, data formats, TeX, XML, terminal output, outlines, bibliographies,
PDF and lightweight markup. `docs/formats.md` in this repository has the full
extension to engine table, per-family quality notes, and what to do when a format
is not listed.

## Engines

| Engine | Needed for | Install |
| --- | --- | --- |
| pandoc | Word, HTML, EPUB, wikis, LaTeX and most text formats | `brew install pandoc` / `apt install pandoc` / `choco install pandoc` |
| LibreOffice | legacy `.doc`, `.ppt`, `.xls`, `.odp`, `.pages`, `.key` | `brew install --cask libreoffice` |
| pymupdf4llm | best-quality PDF text | `pip install pymupdf4llm` |
| poppler-utils | PDF fallback (`pdftotext`) | `apt install poppler-utils` |
| openpyxl | `.xlsx` with sheet names and real headers | `pip install openpyxl` |
| ocrmypdf | scanned PDFs, with `--ocr` | `apt install ocrmypdf` |
| asciidoctor | AsciiDoc input (`.adoc`) | `apt install asciidoctor` |

LibreOffice needs its document filters, not just `libreoffice-core`. If legacy
files fail with "LibreOffice produced no output", those filter packages are the
usual cause.

`.pptx` and `.xlsx` are read directly, without LibreOffice: slides become
`## Slide N: Title` with bullets, tables and speaker notes, and each spreadsheet
sheet becomes a GFM table under its own heading.

## For people who do not use a terminal

This repository also ships a one-click app over the same converter, for Mac,
Windows and Linux. If the person you are helping would rather click than type,
point them at the install line for their system in the README instead of a
command with flags.

## For other assistants

`scripts/mcp_server.py` serves the same converter over MCP, so Claude Desktop,
Cursor, Gemini CLI, Codex CLI and ChatGPT can read documents the same way.
`docs/ai-agents.md` has the configuration for each.

## Checking the result

Skim the report first: `cat OUTPUT_DIR/_conversion-report.md`. Failures cluster
by cause (a missing engine, one corrupt file, scanned PDFs), so the fix is
usually one install or one flag, then a re-run, which only redoes what failed.

`scripts/selftest.sh` builds a fixture in every major family, converts it, and
prints what worked. Run it after changing the converter or when a new machine is
behaving strangely.
