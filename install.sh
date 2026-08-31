#!/bin/bash
# Install "Document to Markdown" and put it on the Desktop.
#
# From a copy of this repository:   ./install.sh
# From anywhere, in one line:
#   curl -fsSL https://raw.githubusercontent.com/charles-martech/markdown-anything/main/install.sh | bash
#
# It installs the newest release, copies the app into /Applications, adds a
# Desktop shortcut, and opens it. It never asks for an administrator password
# and installs nothing system-wide.
#
# MDA_REF=main ./install.sh installs a branch instead of the newest release,
# for trying something before it is released.
set -euo pipefail

REPO_URL="https://github.com/charles-martech/markdown-anything"
say() { printf '%s\n' "$1"; }

# The newest release tag. git ls-remote is asked first because, unlike the
# API, it has no rate limit to run into. If neither answers — offline, or a
# repository with no releases yet — fall back to the default branch and say so,
# rather than refusing to install anything.
latest_release() {
  local tag=""
  if command -v git >/dev/null 2>&1; then
    tag="$(git ls-remote --tags --refs --sort=-v:refname "$REPO_URL.git" 'v*' \
           2>/dev/null | head -1 | sed 's|.*refs/tags/||')"
  fi
  if [ -z "$tag" ]; then
    tag="$(curl -fsSL --max-time 20 \
           "https://api.github.com/repos/charles-martech/markdown-anything/releases/latest" \
           2>/dev/null | grep -m1 '"tag_name"' \
           | sed -E 's/.*"tag_name"[^"]*"([^"]+)".*/\1/')"
  fi
  printf '%s' "$tag"
}

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
  REF="${MDA_REF:-}"
  if [ -z "$REF" ]; then
    REF="$(latest_release)"
    if [ -z "$REF" ]; then
      REF="main"
      say "Could not reach GitHub to ask for the newest release."
      say "Installing the latest code from the main branch instead."
    fi
  fi
  say "Downloading Document to Markdown $REF..."
  TEMP="$(mktemp -d)"
  if command -v git >/dev/null 2>&1; then
    git clone --depth 1 --branch "$REF" --quiet "$REPO_URL.git" "$TEMP/src"
  else
    case "$REF" in
      v*) URL="$REPO_URL/archive/refs/tags/$REF.tar.gz" ;;
      *)  URL="$REPO_URL/archive/refs/heads/$REF.tar.gz" ;;
    esac
    curl -fsSL "$URL" | tar -xz -C "$TEMP"
    mv "$TEMP"/markdown-anything-* "$TEMP/src"
  fi
  SOURCE="$TEMP/src"
fi

# An older copy may still be serving in the background. Ask it to stop, so the
# icon opens the version we are about to install rather than the old one.
INSTANCE="$HOME/Library/Application Support/Document to Markdown/instance.json"
if [ -f "$INSTANCE" ] && command -v python3 >/dev/null 2>&1; then
  python3 - "$INSTANCE" <<'STOP' >/dev/null 2>&1 || true
import json, sys, urllib.request
saved = json.load(open(sys.argv[1]))
url = f"http://127.0.0.1:{saved['port']}/api/quit?token={saved['token']}"
urllib.request.urlopen(urllib.request.Request(
    url, data=b"{}", headers={"Content-Type": "application/json"}), timeout=2)
STOP
  say "Stopped the copy that was already running."
fi

# An in-app update leaves a newer copy of the app's files in the support
# folder, and the launcher prefers it. Clearing it means running this installer
# always gets you the version it just downloaded, not one updated into place
# earlier.
if [ -n "${HOME:-}" ]; then
  rm -rf "$HOME/Library/Application Support/Document to Markdown/current"
fi

say "Building the app..."
APP="$(bash "$SOURCE/macos/build_app.sh")"

# A Desktop shortcut, so nobody has to go looking for it.
DESKTOP="$HOME/Desktop"
if [ -d "$DESKTOP" ]; then
  # Only our own shortcut, by its exact name. A glob here would take
  # someone's "Document to Markdown notes.docx" with it.
  rm -rf "$DESKTOP/Document to Markdown" "$DESKTOP/Document to Markdown.app" \
    2>/dev/null || true
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
