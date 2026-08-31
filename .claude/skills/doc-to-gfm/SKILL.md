---
name: doc-to-gfm
description: Convert documents of any format into GitHub Flavored Markdown, one file or a whole folder tree at once. Use whenever someone wants files turned into markdown or .md - Word (.docx/.doc/.odt/.rtf), PDF, slides (.pptx/.ppt/.odp), spreadsheets (.xlsx/.ods/.csv), HTML, EPUB, wiki markup (MediaWiki, DokuWiki, Jira, Creole), LaTeX, reStructuredText, Org, AsciiDoc, DocBook, JATS, man/roff pages, notebooks, OPML outlines, BibTeX and other bibliographies, JSON/YAML/XML data, or terminal output. Trigger on phrases like "convert this folder to markdown", "turn these docs into GFM", "bulk convert", "migrate our documentation to markdown", "extract the text of this PDF as markdown", or "I have a folder of files I need as .md".
---

# Document to GFM converter

Turns a pile of files, in most formats a document can be in, into GitHub
Flavored Markdown. Built for folders: point it at a directory and it walks the
tree, mirrors the layout into an output folder, converts everything it
recognizes, and writes a report naming what failed and why. Nothing is loaded
one file at a time by hand, and nothing is ever written over the input.

## Use it

```bash
python3 scripts/doc2gfm.py INPUT [INPUT ...] -o OUTPUT_DIR [options]
```

```bash
# a whole folder tree, mirrored into ./markdown
python3 scripts/doc2gfm.py ~/Drive/handbook -o ./markdown

# a single file
python3 scripts/doc2gfm.py report.pdf -o ./markdown

# several sources, flattened into one folder, PDFs only
python3 scripts/doc2gfm.py ~/exports ~/inbox -o ./markdown --flat --include pdf
```

Paths are relative to the repository root. Use absolute paths from elsewhere.

### First run in a fresh environment

`scripts/setup.sh` reports which engines are installed and installs the missing
ones (pandoc is required; the rest widen format coverage). Ask before installing
anything on a machine that is not yours.

### Options worth knowing

| Option | What it does |
| --- | --- |
| `-o, --out DIR` | Output folder. Required. Must sit outside the input folder. |
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
  the source path, its format and its SHA-256.
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
- **Nothing is overwritten in place.** The output folder is separate, and the
  script refuses to write inside the folder it is reading.
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
| pandoc | required, handles most formats | `brew install pandoc` / `apt install pandoc` |
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

This repository also ships a one-click Mac app over the same converter. If the
person you are helping would rather click than type, point them at the install
line in the README instead of a command with flags.

## Checking the result

Skim the report first: `cat OUTPUT_DIR/_conversion-report.md`. Failures cluster
by cause (a missing engine, one corrupt file, scanned PDFs), so the fix is
usually one install or one flag, then a re-run, which only redoes what failed.

`scripts/selftest.sh` builds a fixture in every major family, converts it, and
prints what worked. Run it after changing the converter or when a new machine is
behaving strangely.
