"""Vendor PDF.js's viewer for pdf_view_plus.

    python scripts/vendor_pdfjs.py

Downloads the pinned release, checks its SHA-256, and writes a trimmed copy
to ckanext/webpageviewplus/pdfjs/<version>/: no source maps, debugger,
sample PDF or scripting sandbox (pdf_view_plus turns PDF scripting off), and
only the languages in LOCALES. viewer.html also loads wvp-viewer.js and
wvp-viewer.css, copied from scripts/pdfjs/: they set the viewer's options
and hide its editing, download and print buttons.

To update: change VERSION and SHA256 (from the release's dist zip), run it,
delete the old version's folder, and change pdf.PDFJS_VERSION.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile

VERSION = "6.5.438"
SHA256 = "fcbe9ba21d9911843e3502adc178a7f549a524a4055f887bedc942e5490c6ba1"
URL = "https://github.com/mozilla/pdf.js/releases/download/v{0}/pdfjs-{0}-dist.zip"
# English and the ten languages CKAN portals most often default to
# (datashades.info census, 2026): Spanish, Brazilian Portuguese, Thai,
# German, Italian, French, Japanese, Indonesian, Ukrainian, Turkish.
LOCALES = ["en-US", "es-ES", "pt-BR", "th", "de", "it", "fr", "ja", "id", "uk", "tr"]

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "..", "ckanext", "webpageviewplus", "pdfjs", VERSION)

KEEP = (
    "LICENSE",
    "build/pdf.mjs",
    "build/pdf.worker.mjs",
    "web/viewer.html",
    "web/viewer.mjs",
    "web/viewer.css",
    "web/images/",
    "web/cmaps/",
    "web/standard_fonts/",
    "web/iccs/",
    "web/wasm/",
)
DROP = ("web/wasm/quickjs-eval.wasm", "web/wasm/quickjs-eval.js")

OURS = (
    '<link rel="stylesheet" href="../wvp-viewer.css" />\n'
    '<script src="../wvp-viewer.js"></script>\n'
)


def main() -> int:
    data = urllib.request.urlopen(URL.format(VERSION), timeout=120).read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        print("SHA-256 mismatch: got {}".format(digest), file=sys.stderr)
        return 1
    shutil.rmtree(TARGET, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if name.endswith("/") or name.endswith(".map") or name in DROP:
                continue
            locale = name.startswith("web/locale/")
            if locale:
                folder = name.split("/")[2]
                if folder not in LOCALES:
                    continue
            elif not any(name == k or (k.endswith("/") and name.startswith(k)) for k in KEEP):
                continue
            path = os.path.join(TARGET, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(z.read(name))
        paths = json.loads(z.read("web/locale/locale.json"))
    kept = {k: v for k, v in paths.items() if v.split("/")[0] in LOCALES}
    with open(os.path.join(TARGET, "web", "locale", "locale.json"), "w") as f:
        json.dump(kept, f, separators=(",", ":"), sort_keys=True)
    viewer = os.path.join(TARGET, "web", "viewer.html")
    with open(viewer, encoding="utf-8") as f:
        html = f.read()
    anchor = '<script src="../build/pdf.mjs" type="module"></script>'
    if html.count(anchor) != 1:
        print("viewer.html changed: can't find where to add wvp-viewer.js", file=sys.stderr)
        return 1
    with open(viewer, "w", encoding="utf-8") as f:
        f.write(html.replace(anchor, OURS + anchor))
    for name in ("wvp-viewer.js", "wvp-viewer.css"):
        shutil.copy(os.path.join(HERE, "pdfjs", name), os.path.join(TARGET, name))
    print("PDF.js {} in {} ({} languages)".format(VERSION, os.path.normpath(TARGET), len(kept)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
