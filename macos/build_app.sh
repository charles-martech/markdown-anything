#!/bin/bash
# Build "Document to Markdown.app" from the files in this repository.
#
#   ./macos/build_app.sh [destination]
#
# Destination defaults to /Applications, falling back to ~/Applications when
# that is not writable. The bundle is plain files: a small shell launcher, the
# Python server, the converter and an icon. Nothing is compiled or signed, and
# because it is built on the machine that runs it, macOS does not quarantine it.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Document to Markdown"
BUNDLE_ID="com.markdown.documenttomarkdown"
# One source of truth, read by the app at runtime so it can tell whether a
# release on GitHub is newer than itself.
VERSION="$(cat "$REPO/VERSION")"

DEST="${1:-/Applications}"
if [ ! -w "$DEST" ]; then
  DEST="$HOME/Applications"
  mkdir -p "$DEST"
fi
APP="$DEST/$APP_NAME.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <!-- No Dock tile: the launcher opens a browser page and exits straight away,
       so a tile would appear and vanish for no reason. -->
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

cp "$REPO/macos/icon.icns" "$APP/Contents/Resources/icon.icns"
mkdir -p "$APP/Contents/Resources/app" "$APP/Contents/Resources/scripts"
cp "$REPO/app/server.py" "$REPO/app/index.html" "$APP/Contents/Resources/app/"
cp "$REPO/scripts/doc2gfm.py" "$APP/Contents/Resources/scripts/"
# VERSION lets the running app compare itself against a release. BUNDLE_FORMAT
# is bumped only when the launcher, Info.plist or icon change — the parts an
# in-app update cannot replace — so an update carrying a higher number stops
# and asks for a reinstall instead of installing something half-usable.
cp "$REPO/VERSION" "$REPO/BUNDLE_FORMAT" "$APP/Contents/Resources/"

cat > "$APP/Contents/MacOS/launcher" <<'LAUNCHER'
#!/bin/bash
# Finds a Python 3 and starts the local server. Any failure becomes a dialog,
# because nobody double-clicking an icon is watching a terminal.
RESOURCES="$(cd "$(dirname "$0")/../Resources" && pwd)"

fail() {
  /usr/bin/osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with icon caution with title \"Document to Markdown\"" >/dev/null 2>&1
  exit 1
}

PY=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  [ -x "$candidate" ] && PY="$candidate" && break
done
[ -z "$PY" ] && PY="$(command -v python3 2>/dev/null)"

if [ -z "$PY" ]; then
  fail "Python 3 is needed and was not found.\n\nmacOS installs it with the Developer Tools: open Terminal, type xcode-select --install and accept the dialog, then open this app again."
fi

# An update installs a complete copy of the app's own files here, in the
# folder this app already owns, rather than rewriting the bundle in
# /Applications: no permissions to ask for, nothing to half-overwrite while it
# is running, and dragging that one folder to the trash puts the shipped
# version back. The installer clears it, so re-running the install line always
# wins over whatever was updated into place.
PAYLOAD="$HOME/Library/Application Support/Document to Markdown/current"
if [ -f "$PAYLOAD/app/server.py" ] && [ -f "$PAYLOAD/scripts/doc2gfm.py" ]; then
  RESOURCES="$PAYLOAD"
fi

cd "$RESOURCES" || fail "The app files could not be found. Reinstall the app."

# This starts the server detached and returns immediately, so macOS never
# leaves the app stuck in its launching state.
"$PY" app/server.py || fail "Document to Markdown could not start. Open Terminal and run:\n\n$PY \"$RESOURCES/app/server.py\" --serve\n\nto see what went wrong."
LAUNCHER
chmod +x "$APP/Contents/MacOS/launcher"

# Locally built files are not quarantined, but a copied repo can be.
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
touch "$APP"

echo "$APP"
