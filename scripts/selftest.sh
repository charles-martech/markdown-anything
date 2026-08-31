#!/usr/bin/env bash
# Build one fixture per format family, convert the folder, and report.
# Everything happens in a temp directory; nothing is written to the repo.
set -uo pipefail
cd "$(dirname "$0")"
SCRIPT="$PWD/doc2gfm.py"

command -v pandoc >/dev/null || { echo "pandoc is required; run ./setup.sh"; exit 2; }

work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
src="$work/in"; mkdir -p "$src/nested"

cat > "$src/seed.md" <<'MD'
# Quarterly Report

Intro with **bold**, *italic* and a [link](https://example.com).

| Region | Revenue |
| --- | --- |
| North | 120 |

- bullet one
- bullet two
MD

for target in docx odt epub html rst org mediawiki textile opml pptx ipynb; do
  pandoc "$src/seed.md" -o "$src/doc.$target" 2>/dev/null
done
pandoc "$src/seed.md" -o "$src/doc.tex" 2>/dev/null
pandoc "$src/seed.md" -o "$src/page.1" -t man 2>/dev/null
printf 'name,role\nAna,Editor\nLuis,Designer\n' > "$src/data.csv"
printf '{"project":"contentos"}\n' > "$src/config.json"
printf '\033[32mPASS\033[0m all checks\n' > "$src/run.log"
printf '@article{k2020, title={A Title}, author={Someone}, year={2020}}\n' > "$src/refs.bib"
printf 'Plain notes.\n' > "$src/notes.txt"
cp "$src/doc.html" "$src/nested/page.html"

if command -v soffice >/dev/null; then
  soffice -env:UserInstallation="file://$work/lo" --headless --norestore \
    --convert-to doc --outdir "$src" "$src/doc.docx" >/dev/null 2>&1
  soffice -env:UserInstallation="file://$work/lo" --headless --norestore \
    --convert-to xlsx --outdir "$src" "$src/data.csv" >/dev/null 2>&1
  soffice -env:UserInstallation="file://$work/lo" --headless --norestore \
    --convert-to pdf --outdir "$src" "$src/doc.docx" >/dev/null 2>&1
fi
command -v asciidoctor >/dev/null && printf '= Title\n\nAsciiDoc *body*.\n' > "$src/doc.adoc"

echo "fixtures: $(find "$src" -type f | wc -l)"
echo
python3 "$SCRIPT" "$src" -o "$work/out"
rc=$?
echo
echo "second run should convert nothing:"
python3 "$SCRIPT" "$src" -o "$work/out" -q
echo
echo "report:"
sed -n '1,8p' "$work/out/_conversion-report.md"
exit $rc
