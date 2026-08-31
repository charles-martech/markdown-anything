# Changelog

Newest first. Dates are when the change landed on `main`, which is also when
it reached anyone who runs the install line.

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
