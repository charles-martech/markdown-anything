#!/usr/bin/env bash
# Check for the engines doc2gfm.py uses, and install the missing ones.
#   ./setup.sh          report what is present and what is missing
#   ./setup.sh --install install the missing pieces (asks for sudo on Linux)
set -uo pipefail

INSTALL=0
[[ "${1:-}" == "--install" ]] && INSTALL=1

have() { command -v "$1" >/dev/null 2>&1; }
pyhas() { python3 -c "import $1" >/dev/null 2>&1; }

missing_apt=()
missing_pip=()
status() { printf '%-14s %-9s %s\n' "$1" "$2" "$3"; }

echo "engine         state     purpose"
if have pandoc; then status pandoc ok "$(pandoc --version | head -1)"
else status pandoc MISSING "required: most formats"; missing_apt+=(pandoc); fi

if have soffice || have libreoffice; then
  # libreoffice-core alone loads nothing, so probe with a real conversion.
  LO=$(command -v soffice || command -v libreoffice)
  probe=$(mktemp -d)
  printf '<html><body><p>probe</p></body></html>' > "$probe/probe.html"
  "$LO" -env:UserInstallation="file://$probe/profile" --headless --norestore \
        --convert-to txt --outdir "$probe/out" "$probe/probe.html" >/dev/null 2>&1
  if [[ -f "$probe/out/probe.txt" ]]; then
    rm -rf "$probe"
    status libreoffice ok "legacy .doc/.ppt/.xls, .odp, .pages, .key"
  else
    rm -rf "$probe"
    status libreoffice PARTIAL "core installed without document filters"
    missing_apt+=(libreoffice-writer libreoffice-calc libreoffice-impress)
  fi
else
  status libreoffice MISSING "legacy .doc/.ppt/.xls, .odp, .pages, .key"
  missing_apt+=(libreoffice-writer libreoffice-calc libreoffice-impress)
fi

if pyhas pymupdf4llm; then status pymupdf4llm ok "best-quality PDF text"
else status pymupdf4llm MISSING "best-quality PDF text"; missing_pip+=(pymupdf4llm); fi

if have pdftotext; then status pdftotext ok "PDF fallback"
else status pdftotext MISSING "PDF fallback"; missing_apt+=(poppler-utils); fi

if pyhas openpyxl; then status openpyxl ok ".xlsx sheet names and headers"
else status openpyxl MISSING ".xlsx sheet names and headers"; missing_pip+=(openpyxl); fi

if have asciidoctor; then status asciidoctor ok "AsciiDoc input"
else status asciidoctor MISSING "AsciiDoc input (optional)"; missing_apt+=(asciidoctor); fi

if have ocrmypdf; then status ocrmypdf ok "--ocr for scanned PDFs"
else status ocrmypdf MISSING "--ocr for scanned PDFs (optional)"; missing_apt+=(ocrmypdf); fi

if ((${#missing_apt[@]} == 0 && ${#missing_pip[@]} == 0)); then
  echo; echo "everything doc2gfm can use is installed."
  exit 0
fi

echo
((${#missing_apt[@]})) && echo "system packages: ${missing_apt[*]}"
((${#missing_pip[@]})) && echo "python packages: ${missing_pip[*]}"

if ((INSTALL == 0)); then
  echo
  echo "re-run with --install to install these, or install them by hand."
  echo "macOS: brew install ${missing_apt[*]/libreoffice-writer/--cask libreoffice}"
  exit 0
fi

if ((${#missing_apt[@]})); then
  if have apt-get; then
    sudo apt-get update -q && sudo apt-get install -y "${missing_apt[@]}"
  elif have brew; then
    brew install "${missing_apt[@]}"
  else
    echo "no apt-get or brew here; install ${missing_apt[*]} by hand." >&2
  fi
fi
((${#missing_pip[@]})) && python3 -m pip install --quiet "${missing_pip[@]}"

echo
echo "done. Re-run ./setup.sh to confirm."
