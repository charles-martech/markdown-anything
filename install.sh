#!/bin/bash
# Install "Document to Markdown" and put it on the Desktop.
#
# From a copy of this repository:   ./install.sh
# From anywhere, in one line:
#   curl -fsSL https://raw.githubusercontent.com/charles-martech/markdown-anything/main/install.sh | bash
#
# On a Mac it builds the app into /Applications; on Linux it installs into
# ~/.local/share/document-to-markdown and adds an application menu entry. On
# both it adds a Desktop shortcut and opens the app. It never asks for an
# administrator password and installs nothing system-wide.
#
# Windows has its own installer, windows/install.ps1, because a Windows
# shortcut and a Windows Python are made from PowerShell, not from bash.
#
# MDA_REF=main ./install.sh installs a branch instead of the newest release,
# for trying something before it is released. MDA_NO_OPEN=1 installs without
# opening the app afterwards, which is what a test wants.
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

OS="$(uname -s)"
case "$OS" in
  Darwin|Linux) ;;
  MINGW*|MSYS*|CYGWIN*)
    say "On Windows, open PowerShell and paste:"
    say "  irm https://raw.githubusercontent.com/charles-martech/markdown-anything/main/windows/install.ps1 | iex"
    exit 1 ;;
  *)
    say "This installer knows macOS and Linux. On anything else, run the converter"
    say "directly:  python3 scripts/doc2gfm.py YOUR_FOLDER -o OUTPUT_FOLDER"
    exit 1 ;;
esac

# Work from a local checkout when there is one, otherwise fetch a copy.
SOURCE=""
if [ -f "${BASH_SOURCE[0]:-}" ]; then
  CANDIDATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [ -f "$CANDIDATE/app/server.py" ] && SOURCE="$CANDIDATE"
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

if [ "$OS" = "Darwin" ]; then
  SUPPORT="${DOC2MD_HOME:-$HOME/Library/Application Support/Document to Markdown}"
else
  SUPPORT="${DOC2MD_HOME:-$HOME/.local/share/document-to-markdown}"
fi

# An older copy may still be serving in the background. Ask it to stop, so the
# icon opens the version we are about to install rather than the old one.
INSTANCE="$SUPPORT/instance.json"
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
rm -rf "$SUPPORT/current"

if [ "$OS" = "Darwin" ]; then
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
  if [ -z "${MDA_NO_OPEN:-}" ]; then
    say "Opening it now. The first time, it will offer to set itself up."
    open "$APP"
  fi
  exit 0
fi

# ---------------------------------------------------------------- Linux ----
# The app's files go in a "bundle" folder inside the support folder: the same
# place an in-app update writes "current", so the launcher's preference for
# an update over the shipped copy works the same way it does on a Mac, and
# deleting the one folder removes every trace.
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  say "Python 3 is needed and was not found. Install it with your package"
  say "manager (for example: sudo apt install python3) and run this again."
  exit 1
fi

BUNDLE="$SUPPORT/bundle"
mkdir -p "$SUPPORT"
chmod 700 "$SUPPORT" 2>/dev/null || true
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/app" "$BUNDLE/scripts" "$BUNDLE/docs"
cp "$SOURCE/app/server.py" "$SOURCE/app/index.html" "$BUNDLE/app/"
cp "$SOURCE/scripts/doc2gfm.py" "$SOURCE/scripts/mcp_server.py" "$BUNDLE/scripts/"
cp "$SOURCE/VERSION" "$SOURCE/BUNDLE_FORMAT" "$BUNDLE/"
cp "$SOURCE/docs/icon.png" "$BUNDLE/docs/icon.png"

# The menu entry. Exec quoting follows the Desktop Entry specification: the
# path is in double quotes, and nothing in it is a field code.
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
ENTRY="$APPS/document-to-markdown.desktop"
cat > "$ENTRY" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=Document to Markdown
Comment=Turn documents into Markdown, on this computer
Exec=$PY "$BUNDLE/app/server.py"
Icon=$BUNDLE/docs/icon.png
Terminal=false
Categories=Office;Utility;
Keywords=markdown;pdf;docx;convert;
DESKTOP
chmod +x "$ENTRY"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS" >/dev/null 2>&1 || true
fi

# A Desktop shortcut too, where there is a Desktop. Newer desktops want a
# launcher marked as trusted before a double-click runs it; gio does that
# where it exists, and it is harmless where it does not.
DESKTOP="$HOME/Desktop"
if command -v xdg-user-dir >/dev/null 2>&1; then
  DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
fi
if [ -d "$DESKTOP" ]; then
  cp "$ENTRY" "$DESKTOP/document-to-markdown.desktop"
  chmod +x "$DESKTOP/document-to-markdown.desktop"
  if command -v gio >/dev/null 2>&1; then
    gio set "$DESKTOP/document-to-markdown.desktop" metadata::trusted true \
      >/dev/null 2>&1 || true
  fi
fi

[ -n "$TEMP" ] && rm -rf "$TEMP"

say ""
say "Installed: $BUNDLE"
say "\"Document to Markdown\" is in your applications menu and on your Desktop."
if [ -z "${MDA_NO_OPEN:-}" ]; then
  say "Opening it now. The first time, it will offer to set itself up."
  "$PY" "$BUNDLE/app/server.py"
fi
