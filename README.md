<div align="center">

<img src="docs/icon.png" width="128" alt="">

# Document to Markdown

**Drop a folder in. Get Markdown out.**
No terminal, no Python, no configuration.

</div>

Turn a folder full of documents into Markdown files, all at once. Word, PDF,
PowerPoint, Excel, web pages, ebooks, wiki exports, LaTeX, man pages and about
sixty other formats. Point it at a folder and every file inside becomes a `.md`
file, with the folder structure kept and a report of anything it could not read.

It runs entirely on your computer. Nothing is uploaded anywhere.

## Install on a Mac

Paste this into Terminal once, press return:

```bash
curl -fsSL https://raw.githubusercontent.com/carlos-vazquez-27/markdown-anything/main/install.sh | bash
```

That puts **Document to Markdown** in your Applications and on your Desktop, and
opens it. From then on it is a normal app: double-click the icon.

The first time you open it, it offers to set itself up. Click the button and it
downloads what it needs into its own folder. No administrator password, no
Homebrew, nothing added to the rest of your Mac. Deleting the app and its folder
in `~/Library/Application Support/Document to Markdown` removes every trace.

## Using it

1. **Choose folder.** It counts what is inside so you can see you picked right.
2. **Check where it goes.** A new folder next to the original, filled in for you.
   Your files are never changed or moved.
3. **Convert.** Watch the progress, then click through to the results.

Options are all optional and written in plain language. The defaults are right
for almost everyone.

## What it converts

| Family | Formats |
| --- | --- |
| Word processors | `.docx` `.doc` `.odt` `.rtf` `.pages` `.wpd` and more |
| PDF | `.pdf`, including scans when OCR is available |
| Slides | `.pptx` `.ppt` `.odp` `.key` |
| Spreadsheets | `.xlsx` `.xls` `.ods` `.csv` `.tsv` |
| Web and ebooks | `.html` `.epub` `.fb2` |
| Wikis | MediaWiki, DokuWiki, TikiWiki, TWiki, VimWiki, Jira, Creole, Muse |
| Documentation | reStructuredText, AsciiDoc, DocBook, JATS, Texinfo, Org, POD |
| Typesetting | LaTeX, Typst, man and roff pages |
| Data and outlines | JSON, YAML, TOML, XML, OPML, notebooks |
| Bibliographies | BibTeX, BibLaTeX, RIS, CSL JSON, EndNote |
| Terminal output | `.log` `.ansi` and other captures, colour codes stripped |

[`docs/formats.md`](docs/formats.md) has the complete list, which engine reads
each format, and what to expect from the result.

Slides keep their titles, bullet levels, tables and speaker notes. Spreadsheets
become one table per sheet, under the sheet's own name. Embedded images are
saved next to each Markdown file with the links already pointing at them.

## Things it does that matter on a real folder

- **One bad file never stops the run.** It is recorded in the report and the
  rest keeps converting.
- **Running it again is cheap.** Files whose source has not changed are left
  alone, so a second pass over a big folder takes seconds.
- **Your originals are never touched.** Output goes to a separate folder, and it
  refuses to write inside the folder it is reading.
- **Two files with the same name stay separate.** `report.docx` and `report.pdf`
  become `report-docx.md` and `report-pdf.md`, never one silently overwriting
  the other.
- **Failures are explained in plain words**, not engine error codes.

## Without the app

The converter is a single Python file with no dependencies of its own:

```bash
python3 scripts/doc2gfm.py ~/Documents/exports -o ~/Documents/markdown
```

Useful flags: `--flat`, `--include pdf`, `--force`, `--no-media`, `--ocr`,
`--dry-run`. Run `scripts/setup.sh` to check which engines are installed, and
`scripts/selftest.sh` to convert a fixture of every major format and prove the
install works.

Linux and Windows (WSL) work the same way; only the one-click app is Mac-only so
far. A Windows launcher is the most useful thing anyone could contribute.

## What it uses

[Pandoc](https://pandoc.org) does most of the reading. LibreOffice, if you have
it, handles older Office formats and Apple's Pages and Keynote. PDFs are read by
[PyMuPDF](https://pymupdf.readthedocs.io) with `pdftotext` as a fallback.
PowerPoint and Excel files are read directly, so those two need nothing extra.

The app installs Pandoc and the Python readers for you, into its own folder.
LibreOffice is a large signed installer, so it points you at the official
download instead of fetching it silently.

## Also a Claude Code skill

`.claude/skills/doc-to-gfm/` makes this available to
[Claude Code](https://claude.com/claude-code) users: clone the repo and ask
"convert this folder to markdown", and the agent runs the converter with the
right flags. Copy that folder to `~/.claude/skills/` to have it everywhere.

## If something goes wrong

**The icon does nothing.** The app keeps running in the background after you
close its tab, and clicking the icon reopens that same page. If nothing opens
at all, the server has stopped: open the app again, and if it still does
nothing, look at `~/Library/Application Support/Document to Markdown/log.txt`.

**It says a tool is missing that you know you installed.** An app opened from
Finder does not see the same `PATH` as your Terminal, so the app looks in the
usual places itself: Homebrew, MacPorts, `/usr/local/bin`, and inside
`LibreOffice.app`. Anything installed somewhere unusual can be pointed at by
launching the app from a terminal instead:
`open -a "Document to Markdown"` inherits nothing, but
`python3 "/Applications/Document to Markdown.app/Contents/Resources/app/server.py"`
inherits your shell environment.

**Stopping it completely.** Use the "Quit the app" button at the bottom of the
page. It also stops itself after thirty idle minutes.

**Removing it.** Delete the app from Applications, the Desktop shortcut, and
`~/Library/Application Support/Document to Markdown`. Nothing else was touched.

## Contributing

Bug reports about a format that converted badly are the most useful thing, and
attaching the file that failed is what makes them fixable. The converter's
routing table is one dictionary at the top of `scripts/doc2gfm.py`; adding a
format is usually one line.

MIT licensed.
