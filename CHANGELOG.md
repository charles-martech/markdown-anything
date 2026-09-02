# Changelog

Newest first. Dates are when the change landed on `main`, which is also when
it reached anyone who runs the install line.

## 1.2.4 — 2026-09-02

### Fixed

- **The app offered a PDF reader that some Macs cannot install.** The reader
  that keeps the text inside diagrams and tables needs Python 3.10, and a Mac
  running the system Python 3.9 cannot have it, so both the conversion report
  and the app's offer were pointing at something out of reach. The report now
  says the Python version is the limit, and the offer promises only what
  installing it would really win there: diagrams saved as pictures.

## 1.2.3 — 2026-09-02

### Changed

- **A diagram picture now leads the page it came from rather than trailing it.**
  Where the reader scrapes the diagram's own text the picture still replaces it,
  which is where the diagram actually sits. Where it does not, which is every
  page read by `pdftotext`, the picture used to follow the page's text — and a
  page's last line is usually the heading the next section opens with, so the
  picture read as illustrating that rather than the page it was taken from.

## 1.2.2 — 2026-09-02

### Fixed

- **Diagrams were never saved on an older PDF reader.** A Mac on the system
  Python 3.9 can install no newer than pymupdf 1.26, and that version returns
  the outline of every glyph from `get_drawings`, so a page of prose arrived as
  several hundred letter-sized strokes and counted as a diagram. Every page in
  a document qualified, the share cap added in 1.2.1 then correctly refused to
  save a picture of all of them, and the result was no diagrams at all. A shape
  must now be at least 20pt on both sides to count, which a diagram's node is
  and a letter is not. On the reported document both pymupdf 1.26.5 and 1.28.2
  now pick out the same three pages.

- **The report told people to install a reader they already had.** When
  pymupdf4llm read a PDF but lost text, and the fuller `pdftotext` reading was
  used instead, the run was then treated as though pymupdf4llm had been missing
  all along and the report ended with "install pymupdf4llm" — directly
  contradicting the line above it, which had just said what pymupdf4llm read.
  That advice now appears only when pymupdf4llm really is absent, and mentions
  diagram pictures only when pictures were asked for.

## 1.2.1 — 2026-09-02

### Changed

- **The keyboard now shows where it is.** Nothing styled `:focus`, so on
  controls carrying their own borders the browser's ring was easy to lose.
  Links, buttons, checkboxes, text fields and the options summary all draw the
  same accent outline when reached by keyboard. Buttons also press in on click,
  and that and the progress bar hold still under `prefers-reduced-motion`.
- **The footer is a landmark again.** It sat inside `<main>`, where a screen
  reader does not expose it as one, although it holds controls for the app
  rather than anything `<main>` describes. It is now a sibling of `<main>` and
  keeps its width and centring from the stylesheet rather than inline styles.

### Fixed

- **A PDF could come out with most of its text missing.** How much text
  pymupdf4llm finds inside vector art depends on its version, and an older one
  drops it silently: a page laid out as a drawn table arrives nearly empty, and
  nothing in the output says so. On one 11-page document an older reader
  produced 3.5 KB where `pdftotext` produced 17.7 KB. Where `pdftotext` is
  available its reading is now compared against pymupdf4llm's, and the fuller
  one is used, with a note in the report saying what each found. Diagrams are
  still saved either way.
- **A page could carry the same picture twice.** A page drawn entirely as
  vector art has no text for the picture to stand beside, and the picture was
  both substituted and appended; a page holding two scraped-text blocks had the
  same page picture put in place of each. A page now carries exactly one
  picture, and a second scraped block is left alone rather than deleted.
- **Every page of a document could be saved as a picture.** How finely PyMuPDF
  splits a drawing into shapes varies by version, and on some of them a page of
  prose over a ruled table counts as a diagram. When more than three quarters
  of a document's pages qualify, the measure is no longer telling them apart,
  and none are saved rather than all of them.

- **An updated app went on behaving like the old one, without saying so.** The
  update replaces the app's files under a server that is already running, and
  tells the person once, in the update log, to quit and open it again. The
  converter is started from those files by path, so it is the new one straight
  away, while the app around it keeps the old behaviour: a conversion run in
  between writes `_conversion-report.md` and `_conversion-manifest.json` into
  the converted folder, as 1.1.1 did, and offers none of what 1.2.0 added. The
  page now compares the version it is running against the version installed on
  disk and, when they differ, carries a notice saying to quit and open the app
  again and that conversions behave like the old version until then.

## 1.2.0 — 2026-09-02

### Added

- **Diagrams in PDFs survive the conversion.** A flowchart, org chart or
  architecture drawing in a PDF is vector art rather than an embedded image, so
  there was nothing to extract and its labels came through in drawing order as
  scrambled fragments, sometimes reversed. Pages that hold a diagram are now
  saved as pictures into the same `.media` folder as embedded images and linked
  from the Markdown, standing in for the scrambled text where the reader
  produced any. `--pdf-picture-dpi` sets the resolution; `--no-media` turns it
  off along with embedded images.
- `--no-sidecars` on the converter, which writes neither the report nor the
  manifest.

### Changed

- **The two `_conversion-*` files no longer land in the converted folder.** The
  app keeps the report and the manifest in its own folder, so a conversion
  leaves nothing behind but the Markdown, the pictures, and no clean-up. The
  **See the full report** button and the link to a single converted file work
  exactly as before. Run from a terminal, the converter still writes both next
  to the output unless told otherwise.
- **Every option says whether it starts on or off, and why.** The panel is no
  longer headed "the defaults are fine": each option gives the reason for its
  default, so leaving one alone is a decision rather than a guess. "Save
  embedded images" is now "Save pictures and diagrams", which is what it does.
- **The PDF reader the app offers to install is the one that reads diagrams.**
  Having `pdftotext` alone counted as ready, which left people with the weakest
  PDF text and no diagrams and never offered them better. The app now offers to
  add pymupdf4llm, which also gets headings and tables right.

## 1.1.1 — 2026-09-02

### Added

- A line at the bottom of the page to star the project on GitHub, share it
  on X or LinkedIn, copy the link, or report a problem. They are plain links
  that open in the browser only when clicked; the page loads nothing from
  anywhere to show them.

### Fixed

- **A page left open went dead without saying so.** The app stops itself
  after thirty idle minutes, and a reinstall stops it too, but the page in the
  browser did not know: pressing anything afterwards, such as **Check for
  updates**, left "Asking GitHub what the newest version is…" on screen and a
  connection error in the console. Now the page pings the app every five
  minutes while it is visible, so a page someone is looking at keeps the app
  alive, and the moment a request finds nothing behind the page it says the
  app has stopped and how to open it again.

## 1.1.0 — 2026-09-02

### Added

- **Convert one file, or a few, not only a folder.** A **Choose files…**
  button beside **Choose a folder…**, with several files allowed at once, and
  the typed path accepts a file too. The Markdown goes to the same folder a
  conversion of the file's whole folder would use, so doing one file today and
  the folder next month lands in one tree with nothing done twice. When one
  file was converted, the result offers that file rather than the folder
  around it.
- **Windows and Linux.** The app now runs on all three: a PowerShell install
  line for Windows (`windows/install.ps1`), and the existing install line now
  installs on Linux too, with a menu entry and a Desktop shortcut. Native file
  and folder dialogs on each, "show in File Explorer" on Windows, Pandoc
  fetched for the right system, and the in-app update working the same way
  everywhere. CI installs and starts the app on Windows and Linux as well as
  building it on a Mac.
- **AI assistants read documents through it.** `--stdout` converts one file
  and prints the Markdown, writing nothing: the shape an assistant wants
  before reading a PDF, at a fraction of the tokens the raw file costs, with
  the document never leaving the machine. The Claude Code skill now says so
  in its description, so it triggers on "summarize this PDF" and not only on
  "convert this folder".
- **An MCP server**, `scripts/mcp_server.py`, standard library only, so Claude
  Desktop, Cursor, Gemini CLI, Codex CLI, ChatGPT and any other client that
  speaks MCP can read documents the same way. Long documents come back in
  pieces. `docs/ai-agents.md` has the two-line setup for each tool.
- The converter run from a terminal, a skill or the MCP server finds the
  engines the app installed for itself, so setting the app up once is enough
  everywhere.

### Changed

- PDFs, slides, spreadsheets and plain-text formats no longer need Pandoc at
  all. A machine without it converts those and is told, per file, about the
  rest.
- A folder preview stops counting at fifty thousand files instead of walking
  a home folder to the end, and skips dot-folders, `node_modules` and
  `__pycache__` the way the converter does.
- The suggested output folder follows each new choice until the person has
  set it themselves, instead of sticking with the first.

### Fixed

- A PDF that PyMuPDF could not open (encrypted, damaged) failed outright
  instead of being tried by the next reader. It is now.
- A Markdown file chosen on its own, with the output set to its own folder,
  would have been written over itself. It is refused.
- Image links in Pandoc output are made relative whichever path separator
  the platform used; a LibreOffice profile path with a space or a drive
  letter is passed as a proper URI.
- File names the terminal's encoding cannot show no longer crash a run, and
  the converter's output to the app is read as UTF-8 regardless of locale.
- The converter's sniffing of a file with no extension left a file handle
  open.

## 1.0.4 — 2026-08-31

### Added

- **The app can update itself.** When a newer release exists it says what
  changed and offers to install it. Before installing, the downloaded copy is
  started once on its own; if it does not come up, it is discarded and the
  working version is left alone.
- **Whether it looks for updates on its own is asked once, and is the person's
  to answer.** Both options are put in front of them with the trade written out
  and neither preselected: check once a day and get fixes without thinking
  about it, or check only on the button and send nothing unasked. The answer
  can be changed at any time from the page, and nothing is asked of the network
  until it has been given.
- An update installs into the app's own support folder rather than rewriting
  the bundle in `/Applications`, so it needs no permissions, cannot half-replace
  a running app, and can be undone by deleting one folder.
- The app knows its own version, from a `VERSION` file at the root of the
  repository that the build stamps into the bundle.
- `BUNDLE_FORMAT` marks the parts of the app an update cannot replace. A release
  needing a newer one refuses to install and asks for the install line instead.
- `--selftest` starts the server, answers one request, and exits. It is what an
  update is put through before it is trusted.

### Changed

- **The installer installs the newest release, not the tip of `main`.** Merging
  is no longer publishing; tagging is. `MDA_REF=main ./install.sh` still
  installs a branch for testing.
- The app now makes one more kind of network request: asking GitHub for the
  newest release tag. It sends an IP address and nothing else — no file, no file
  name, nothing out of a document — and happens either once a day or only on the
  button, as the person chose.
- The page now says plainly what stays on the machine and what does not, rather
  than leaving it to the README.

## 1.0.3 — 2026-08-31

### Safer

- The local server now refuses requests naming any host but its own. Without
  this, a website whose domain points at `127.0.0.1` counted as the same origin
  as the app, could read its token out of the page, and could then drive the
  converter. This was the most serious problem in the app.
- The token file and the app's support folder are created readable only by
  their owner, so another account on a shared machine cannot pick the token up
  off the disk.
- Text from the page is passed to AppleScript as an argument instead of being
  pasted into the script, so a folder prompt can no longer carry AppleScript
  for the machine to run.
- "Show me the files" reveals a path in Finder rather than opening it, so it can
  never launch an application bundle.
- Downloads are accepted only over HTTPS from GitHub's own hosts, and an
  archive holding a member that points outside its own folder is discarded
  instead of unpacked.
- The reader libraries are installed with version bounds, so a new major
  release cannot arrive on someone's machine without a deliberate change here.
- A document that declares its own XML entities is refused rather than expanded,
  which is a few lines of XML that would otherwise eat all your memory.
- Requests with an oversized or malformed body, and nonsense query values, are
  answered rather than turned into a crash.
- Downloaded archives are unpacked a member at a time, each to a path checked
  to be inside the folder, and links in an archive are skipped rather than
  written. Checking every member and then calling `extractall` is not the same
  thing: a symlink written early can move where a later member lands.

### Fixed

- **The app could hang on startup, showing nothing.** Python's HTTP server
  reverse-resolves its own address while binding, so on a Mac whose resolver is
  slow or unreachable, clicking the icon did nothing at all for half a minute
  before the page appeared. Nothing about starting up goes near a name server
  now. This is the likeliest cause of "the icon does nothing" in the README.
- The installer removed anything on the Desktop whose name started with
  "Document to Markdown". It now removes only its own shortcut, by exact name.
- A quote or a backslash in a file name no longer breaks the YAML front matter
  of the Markdown written for it.
- The converter's docstring pointed at `references/formats.md`, which does not
  exist. It is `docs/formats.md`.

### Added

- A test suite (`python3 -m unittest discover -s tests`) covering the server's
  front door, archive unpacking, and the converter's handling of odd input.
- CI on every push and pull request: tests on Python 3.9, 3.11 and 3.13, lint,
  shellcheck, and a Mac job that builds the app and starts the server.
- `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue and pull
  request templates, CodeQL, Dependabot, and `docs/maintenance.md`.

## 1.0.2

- The install line points at the account's current name.
- Tools already installed are found rather than downloaded again, and the icon
  no longer hangs while the server starts.

## 1.0.1

- First public version: the converter, the Mac app, and the Claude Code skill.
