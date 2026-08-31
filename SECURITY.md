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
them leaves it. There is no account, no server, and no telemetry. Two things
do reach the network, both only when you ask:

- **Setup** downloads Pandoc from its official GitHub release, and the reader
  libraries from PyPI, into the app's own folder. Downloads are only accepted
  over HTTPS from GitHub's own hosts, and an archive that tries to write
  outside its own folder is discarded.
- **Nothing else.** The converter itself never opens a network connection.

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
in `/Applications`, your Desktop, and the app's own support folder.

## Supported versions

The latest commit on `main` is the supported version. Fixes go there.
