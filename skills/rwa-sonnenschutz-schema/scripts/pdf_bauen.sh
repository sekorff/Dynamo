#!/usr/bin/env bash
# Baut das mehrseitige PDF (A3 quer): Seite 1 = SVG-Schema, danach die HTML-Seiten.
# Aufruf: pdf_bauen.sh <schema.svg> <seiten_2-3.html> <ausgabe.pdf>
# Braucht node mit playwright (Chromium) und xmllint.
set -euo pipefail
svg="$(realpath "$1")"; html="$(realpath "$2")"; out="$3"
tmp="$(mktemp -d)"
xmllint --noout "$svg"
python3 - "$svg" "$html" "$tmp/alle.html" <<'PY'
import sys
svg, html, out = sys.argv[1:]
s = open(html, encoding="utf-8").read()
assert "<!--PAGE1-->" in s, "Platzhalter <!--PAGE1--> fehlt in der HTML-Vorlage"
open(out, "w", encoding="utf-8").write(
    s.replace("<!--PAGE1-->", f'<div class="page1"><img src="file://{svg}"></div>'))
PY
cat > "$tmp/pdf.js" <<'JS'
const {chromium} = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage();
  await p.goto('file://' + process.argv[2]);
  await p.pdf({path: process.argv[3], format: 'A3', landscape: true, printBackground: true, preferCSSPageSize: true});
  await b.close();
})();
JS
node "$tmp/pdf.js" "$tmp/alle.html" "$out"
pdfinfo "$out" | grep Pages
