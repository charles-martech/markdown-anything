# Security

## Reporting a problem

Please report anything that looks like a security problem **privately**, using
[GitHub's private reporting form][report], not a public issue. A first reply
usually takes a few days. If a report turns out to be real, the fix and a note
about it land together, and you get the credit unless you would rather not.

[report]: https://github.com/charles-martech/markdown-anything/security/advisories/new

Things worth reporting: a way for a web page, another program, or another user
on the same machine to drive the app; a document that makes the converter write
outside its output folder or run something; anything that sends a person's
files off their computer.

## What this app is, from a security point of view

It converts documents you already have, on your own machine, and nothing about
them leaves it: no file, no file name, no fragment of the contents. There is no
account, no server run by this project, and no telemetry of any kind. The
converter never opens a network connection at all.

Two things reach the network, and nothing else:

- **Setup** downloads Pandoc from its official GitHub release, and the reader
  libraries from PyPI, into the app's own folder. Downloads are only accepted
  over HTTPS from GitHub's own hosts, and an archive that tries to write
  outside its own folder is discarded.
- **Looking for a newer version** asks GitHub for the newest release tag and
  compares it with this app's version. That request tells GitHub the IP address
  it came from and nothing else: no document, no file name, no identifier of
  the machine or the person. It exists so that fixes — including security
  fixes — can reach people who already installed the app.

  **Whether this happens on its own is the person's choice, asked once.** On
  first run the app puts both options in front of them with the trade spelled
  out, with neither preselected: check once a day, or check only when the
  button is pressed. Until they answer, nothing is asked of the network. The
  answer is stored in `settings.json` in the app's own folder and can be
  changed at any time from the page.
- **Nothing else.** The converter itself never opens a network connection.

### Installing an update

An update is a release archive of this repository, and it is code that will run
on your machine, so it is treated as such:

- Only tagged releases, only over HTTPS, only from GitHub's own hosts.
- The tag has to be a version number and nothing else, because it is put into
  a download URL.
- It is unpacked a member at a time, each to a checked path, with links skipped.
- The archive must contain the app's own files, or it is discarded unopened.
- **The downloaded copy is started once, on its own, before it is trusted.** If
  it does not come up, it is thrown away and the version you have keeps running.
- Nothing installs without you pressing the button.

An update never touches `/Applications`, or the installed copy on Windows and
Linux. It lands in `current` inside the app's own folder, and the launcher
prefers it — so if an update ever misbehaves, deleting that one folder puts
back the version that came with the app, with no terminal and no reinstall.

The honest caveat: an app that can update itself is one that can be made to
install something bad if this repository is ever taken over. That is the same
trust the install line asks for, but it reaches people who installed long ago
rather than only new arrivals. It is why the update path accepts releases only,
never a branch, and never installs on its own.

### The local web page

The interface is a page served by a small HTTP server on your own machine.
It is protected in four ways:

- The socket is bound to `127.0.0.1`, so nothing on your network can reach it.
- Every request carries a random token minted when the server starts.
- Requests naming any host other than this one are refused, which is what stops
  a website whose domain points at `127.0.0.1` from counting as the same origin
  and reading that token out of the page.
- The token is written to `instance.json` with owner-only permissions, inside a
  folder with owner-only permissions, so other accounts on a shared machine
  cannot read it.

The server stops itself after thirty idle minutes, and the "Quit the app"
button stops it immediately.

On Windows, the file and folder dialogs are run through PowerShell. The
script is fixed text passed base64-encoded, and the prompt travels in an
environment variable, so nothing from the page is ever part of a command —
the same rule as the AppleScript on a Mac.

### The MCP server

`scripts/mcp_server.py` lets an AI assistant on this computer read a document
as Markdown. It talks to the assistant's client over a pipe, opens no socket,
and makes no network request. It converts whatever path the client names, as
the account running it: that is the feature, and it means an assistant you
have connected to it can read any document you can. Connect it to tools you
trust with your files, which is the same trust you give them by pasting text
into a chat.

### What it can do if someone does get in

Everything you could do from the page: read the folders you point it at, write
Markdown into an output folder, and start Pandoc or LibreOffice on your files.
It does not run with any privileges you do not have, and it never asks for an
administrator password.

## Documents are untrusted input

A document from someone else is data, not instructions, and the converter
treats it that way: file names are never handed to a shell, embedded images are
written by name only, and XML that declares its own entities is refused rather
than expanded. Converting a hostile file should waste your time at worst. If
you find one that does more than that, please report it.

## The install line

`curl … | bash` runs a script from this repository on your machine. If you
would rather read it first — and that is a reasonable thing to want:

```bash
curl -fsSLO https://raw.githubusercontent.com/charles-martech/markdown-anything/main/install.sh
less install.sh
bash install.sh
```

The installer builds the app from the files in the repository. It copies
nothing into system folders, asks for no password, and everything it creates is
in `/Applications`, your Desktop, and the app's own support folder. The Linux
path of the same script, and `windows/install.ps1`, do the same in
`~/.local/share/document-to-markdown` and `%LOCALAPPDATA%\Document to Markdown`
respectively, plus a menu entry or Start Menu shortcut. To read the Windows
one first:

```powershell
irm https://raw.githubusercontent.com/charles-martech/markdown-anything/main/windows/install.ps1 -OutFile install.ps1
notepad install.ps1
powershell -ExecutionPolicy Bypass -File install.ps1
```

## Supported versions

The latest commit on `main` is the supported version. Fixes go there.
