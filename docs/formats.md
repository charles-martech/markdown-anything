# Format routing

Which extension goes through which engine, what the output looks like, and what
to do when something is missing. `scripts/doc2gfm.py` decides by extension; a
file whose extension says nothing is sniffed by magic bytes (PDF header, ZIP
container contents, RTF header, HTML or XML prologue) before it is given up on.

Routes:

| Route | Engine | Notes |
| --- | --- | --- |
| `pandoc` | pandoc, direct | one process, highest fidelity |
| `office` | LibreOffice, then pandoc or the slide reader | legacy and proprietary formats |
| `pptx` | built-in slide reader (stdlib zipfile + ElementTree) | no LibreOffice needed |
| `sheet` | openpyxl if present, else LibreOffice HTML export | one GFM table per sheet |
| `pdf` | pymupdf4llm, markitdown, or pdftotext | first one that yields text wins |
| `asciidoc` | asciidoctor to DocBook, then pandoc | pandoc has no AsciiDoc reader |
| `biblio` | pandoc to CSL JSON, then a GFM reference list | sorted by citation key |
| `ansi` | built-in | control codes stripped, wrapped in a `console` fence |
| `text` | built-in | data files fenced by language, plain text passed through |

## Word processor formats

| Extension | Route |
| --- | --- |
| `.docx`, `.docm` | pandoc `docx` |
| `.odt`, `.fodt` | pandoc `odt` |
| `.rtf` | pandoc `rtf` |
| `.doc`, `.dot`, `.wpd`, `.wps`, `.sxw`, `.stw`, `.abw`, `.lwp`, `.hwp`, `.uot`, `.pages` | LibreOffice to `.docx`, then pandoc |

Tracked changes are flattened to their accepted state. Comments are dropped.
Footnotes survive as GFM footnotes.

## HTML formats

`.html`, `.htm`, `.xhtml`, `.shtml` through pandoc `html`. Scripts and styles are
dropped; tables, lists and links survive. A page saved from a browser with its
`_files` folder converts fine, and the asset folder is skipped as binary.

## Wiki markup formats

`.mediawiki` and `.wiki` (mediawiki), `.dokuwiki`, `.tikiwiki`, `.twiki`,
`.vimwiki`, `.jira`, `.creole`, `.muse`. All direct pandoc readers. Wiki
templates and macros have no markdown equivalent and come through as literal
text; grep the output for `{{` after converting a wiki export.

## Ebooks

`.epub` (chapters concatenated into one document, images extracted), `.fb2`.
Very large EPUBs are slow rather than fragile; raise `--timeout` if one trips it.
`.mobi` and `.azw3` are not supported: convert to EPUB with Calibre's
`ebook-convert` first.

## Documentation formats

`.docbook`/`.dbk`, `.jats`, `.texi`/`.texinfo`, `.haddock`, `.rst`/`.rest`,
`.textile`, `.t2t`, `.pod`, `.adoc`/`.asciidoc`/`.asc` (via asciidoctor).
Sphinx projects convert file by file; `toctree` directives and roles that only
exist inside Sphinx come through as literal text.

## Slide show formats

`.pptx`, `.pptm` are read directly. Each slide becomes `## Slide N: Title`,
followed by bullets at their real indent level, tables as GFM tables, images as
links into the `.media/` folder, and speaker notes as a blockquote. Slide order
is the file order, not the presentation order of a custom show.

`.ppt`, `.odp`, `.key`, `.sxi`, `.pot`, `.fodp` go through LibreOffice to `.pptx`
first, then the same reader. Text in grouped shapes and SmartArt may be missed:
if a deck matters, skim the output against the slides.

## Roff

`.1` through `.9`, `.man`, `.roff`, `.nroff`, `.troff`, `.groff` through pandoc
`man`. A gzipped man page (`.1.gz`) must be gunzipped first.

## Data formats

| Extension | Result |
| --- | --- |
| `.csv`, `.tsv`, `.tab` | GFM table via pandoc |
| `.xlsx`, `.xlsm` | one `## Sheet name` plus table per sheet (openpyxl) |
| `.xls`, `.ods`, `.fods`, `.numbers`, `.dif` | tables via LibreOffice HTML export |
| `.json`, `.yaml`, `.yml`, `.toml`, `.ini`, `.cfg`, `.conf` | fenced code block, language tagged |

Spreadsheet formulas are converted to their computed values. Charts, pivot
tables and conditional formatting are lost; a spreadsheet whose meaning lives in
its charts is not a markdown document.

## TeX formats

`.tex`, `.latex`, `.ltx`, `.sty` through pandoc `latex`; `.typ` through pandoc
`typst`. Custom macros pandoc does not know are dropped with a warning recorded
in the manifest. Math becomes `$...$` and `$$...$$`, which GitHub renders.

## XML formats

`.opml` through pandoc's outline reader. `.xml`, `.xsd`, `.xsl`, `.svg`, `.rss`,
`.atom` are fenced as `xml`, since arbitrary XML has no meaningful markdown
shape. A known XML vocabulary (DocBook, JATS) is routed to its own reader
instead; force one with `--include` and a rename if a file is misnamed.

## Terminal output

`.ansi`, `.log`, `.out`, `.console`, `.ttyrec`. SGR colour codes, OSC sequences
and carriage-return overwrites are stripped, and the result is wrapped in a
```` ```console ```` fence long enough to contain any backticks inside it.

## Outline formats

`.org` through pandoc `org` (headings, TODO keywords, tables and source blocks
survive; Babel results do not). `.opml` is listed under XML formats above.

## Custom and notebook formats

`.ipynb` (markdown and code cells, outputs where pandoc keeps them), `.native`,
`.pandoc.json`, plus every markdown flavour: `.md`, `.markdown`, `.mdown`,
`.mkd`, `.mdwn`, `.mdx`, `.commonmark`, `.dj` (djot). Converting markdown to
markdown is not a no-op: it normalizes an arbitrary flavour into GFM.

## Bibliography formats

`.bib`, `.bibtex`, `.biblatex`, `.csljson`, `.ris`, `.enl`. Each entry becomes a
list item under a `# References` heading: citation key, title, authors, year,
container, and a DOI or URL link. Entries are sorted by key, so the output is
stable across runs.

## PDF

`.pdf`, in engine order: pymupdf4llm (keeps headings and tables best), markitdown,
then `pdftotext -layout`. Choose one explicitly with `--pdf-engine`.

A PDF with no text layer fails with a note saying so. Re-run with `--ocr` and
`ocrmypdf` installed to OCR it. Multi-column academic layouts interleave columns
under `pdftotext`; pymupdf4llm handles them better. `--pdf-page-marks` keeps
`<!-- page N -->` comments, useful when the markdown has to be checked against
the original.

## Lightweight markup formats

Markdown flavours, djot, Muse, Textile, txt2tags, reStructuredText, Org and
AsciiDoc are all listed in the families above.

## Not converted

Images, audio, video, archives, fonts, binaries and databases are listed as
skipped in the report rather than failed. `.mobi`, `.azw3`, `.chm` and `.djvu`
need a conversion to EPUB or PDF first.

## Adding a format

Add one line to the routing table at the top of `scripts/doc2gfm.py`:

```python
_add("pandoc", "<pandoc input format>", ".ext")   # direct pandoc reader
_add("office", "docx", ".ext")                    # LibreOffice can open it
```

`pandoc --list-input-formats` prints the readers the installed pandoc has. For
anything else, write a converter function that returns GFM as a string and give
it a route name in `convert_one`.
