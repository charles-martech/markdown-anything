# Changelog

Newest first. Dates are when the change landed on `main`, which is also when
it reached anyone who runs the install line.

## Unreleased

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
