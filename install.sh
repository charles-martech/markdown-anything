#!/bin/bash
# Install "Document to Markdown" and put it on the Desktop.
#
# From a copy of this repository:   ./install.sh
# From anywhere, in one line:
#   curl -fsSL https://raw.githubusercontent.com/carlos-vazquez-27/markdown-anything/main/install.sh | bash
#
# It copies the app into /Applications, adds a Desktop shortcut, and opens it.
# It never asks for an administrator password and installs nothing system-wide.
set -euo pipefail

REPO_URL="https://github.com/carlos-vazquez-27/markdown-anything"
say() { printf '%s\n' "$1"; }

if [ "$(uname -s)" != "Darwin" ]; then
  say "This installer builds a Mac app. On Linux or Windows, run the converter"
  say "directly:  python3 scripts/doc2gfm.py YOUR_FOLDER -o OUTPUT_FOLDER"
  exit 1
fi

# Work from a local checkout when there is one, otherwise fetch a copy.
SOURCE=""
if [ -f "${BASH_SOURCE[0]:-}" ]; then
  CANDIDATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [ -f "$CANDIDATE/macos/build_app.sh" ] && SOURCE="$CANDIDATE"
fi

TEMP=""
if [ -z "$SOURCE" ]; then
  say "Downloading Document to Markdown..."
  TEMP="$(mktemp -d)"
  if command -v git >/dev/null 2>&1; then
    git clone --depth 1 --quiet "$REPO_URL.git" "$TEMP/src"
  else
    curl -fsSL "$REPO_URL/archive/refs/heads/main.tar.gz" | tar -xz -C "$TEMP"
    mv "$TEMP"/*-main "$TEMP/src"
  fi
  SOURCE="$TEMP/src"
fi

say "Building the app..."
APP="$(bash "$SOURCE/macos/build_app.sh")"

# A Desktop shortcut, so nobody has to go looking for it.
DESKTOP="$HOME/Desktop"
if [ -d "$DESKTOP" ]; then
  rm -f "$DESKTOP/Document to Markdown"* 2>/dev/null || true
  /usr/bin/osascript >/dev/null 2>&1 <<OSA || ln -sfn "$APP" "$DESKTOP/Document to Markdown"
tell application "Finder"
  make alias file to POSIX file "$APP" at POSIX file "$DESKTOP"
  set name of result to "Document to Markdown"
end tell
OSA
fi

[ -n "$TEMP" ] && rm -rf "$TEMP"

say ""
say "Installed: $APP"
say "A shortcut called \"Document to Markdown\" is on your Desktop."
say "Opening it now. The first time, it will offer to set itself up."
open "$APP"
