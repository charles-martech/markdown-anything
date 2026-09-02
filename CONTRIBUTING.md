# Contributing

The most useful thing anyone can send is a document that converted badly, with
the file attached. That is what makes a format fixable.

## Getting set up

You need Python 3.9 or newer and Pandoc. Nothing else is required, and the
project has no Python dependencies of its own.

```bash
git clone https://github.com/charles-martech/markdown-anything
cd markdown-anything
scripts/setup.sh              # reports what is installed and what is missing
scripts/setup.sh --install    # installs the missing pieces
```

## Running it

```bash
# the converter, directly
python3 scripts/doc2gfm.py ~/Documents/exports -o /tmp/markdown

# the app, without building a bundle: opens the page in your browser
python3 app/server.py

# the app, in a terminal you can watch and Ctrl+C
python3 app/server.py --no-browser
```

`DOC2MD_HOME=/tmp/mda python3 app/server.py --no-browser` puts everything the
app installs and remembers somewhere disposable, which is what you want while
working on the setup code.

## Before opening a pull request

```bash
python3 -m unittest discover -s tests   # fast, needs nothing installed
bash scripts/selftest.sh                # converts a fixture of every format
ruff check .
shellcheck install.sh macos/build_app.sh scripts/*.sh
```

CI runs all of these on Python 3.9, 3.11 and 3.13, builds the Mac app,
installs and starts the app on Linux and on Windows, and runs the unit tests
on Windows with no engine installed. It pins the ruff version, so
`pip install ruff==0.15.8` if a lint result here disagrees with the one on
your pull request.

The Windows installer is PowerShell (`windows/install.ps1`) because a Windows
shortcut and a Windows Python are made from PowerShell, not bash; CI parses
and runs it. Anything in `app/server.py` that touches the platform — dialogs,
revealing a file, where the support folder is, how Pandoc is fetched — has a
branch per system, and a change to one is worth trying on the other two or
saying in the pull request that you could not.

## Adding a format

The routing table is the block of `_add(...)` calls at the top of
`scripts/doc2gfm.py`. A format Pandoc already reads is one line:

```python
_add("pandoc", "rst", ".rst", ".rest")
```

A format that needs its own reader gets a `convert_*` function and a branch in
`convert_one`. Add it to `docs/formats.md` and to the fixtures in
`scripts/selftest.sh` in the same change, so it stays working.

## Versions

`VERSION` at the root is the app's version, read at runtime so a running app can
compare itself with a release. `BUNDLE_FORMAT` beside it covers the parts an
in-app update cannot replace — on a Mac the launcher, `Info.plist` and the
icon; on Windows and Linux the shortcut and menu entry. Change any of those and
bump it, or people will get new code running under an old launcher.
`scripts/doc2gfm.py` has its own `VERSION`, which belongs to the converter's
output, not to the app.

## The AI side

`.claude/skills/doc-to-gfm/SKILL.md` is what Claude Code reads;
`scripts/mcp_server.py` is what every other assistant talks to; both sit on
`convert_file_to_markdown()` in the converter. A change to what the converter
produces reaches all three. The MCP server is standard library only and speaks
JSON-RPC one line at a time; `tests/test_app.py` drives it through a session,
and `docs/ai-agents.md` shows how to do the same by hand.

## House style

- Plain language everywhere a person will read it: the interface, the report,
  error messages, the README. "That file is password protected", not
  "ERR_ENCRYPTED_DOCUMENT".
- Comments explain why, not what. If a line looks odd and is deliberate, say
  what would go wrong without it.
- Standard library only in `app/` and `scripts/`. The app is meant to run on
  a machine with nothing installed, and every dependency is something that can
  fail to install on someone's machine at the worst moment.
- One bad file never stops a run. Anything that can fail per-file gets
  recorded in the report and the batch carries on.

## Things to be careful with

The app runs a local web server that can start processes and read folders, so
a few rules hold there:

- Never build a shell or AppleScript command out of text from the page. Pass
  values as arguments.
- Never let anything downloaded decide where it is written.
- Treat a document as data. It came from the internet as often as not.

New tests for any of the above belong in `tests/test_app.py`.

## Reporting a security problem

Privately, please: see [SECURITY.md](SECURITY.md).
