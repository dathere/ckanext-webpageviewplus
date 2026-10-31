#!/usr/bin/env bash
# Build the HTML that demo/seed_schematics.py uploads: qsv Data Schematics and
# charts made from qsv's test CSVs, and a static PNG preview of each. Runs on
# the host (where qsv is), writing to demo/schematics/ (gitignored), which the
# dev container sees through the repo's bind mount.
#
#   QSV=~/GitHub/qsv/target/debug/qsv QSV_RELEASE=/usr/local/bin/qsv demo/build_schematics.sh
#
# QSV should include qsv PR #4949 (Data Schematics report their height when
# framed). QSV_RELEASE, optional, is a qsv without it (27.0.1 or older): its
# schematic shows the fallback height side by side.
set -euo pipefail

QSV=${QSV:-qsv}
QSV_RELEASE=${QSV_RELEASE:-}
RAW=https://raw.githubusercontent.com/dathere/qsv/master
OUT="$(cd "$(dirname "$0")" && pwd)/schematics"
export QSV_PROGRESSBAR=0

mkdir -p "$OUT/csv"
for path in .claude/skills/tests/fixtures/nyc311-100.csv resources/test/boston311-100.csv \
            resources/test/us-states-lookup.csv resources/test/adur-public-toilets.csv; do
    f=$(basename "$path")
    [ -s "$OUT/csv/$f" ] || curl -fsSL "$RAW/$path" -o "$OUT/csv/$f"
done

# qsv's own gallery schematic (--smarter, a curated dictionary, a zip-code map):
# 50,000 Allegheny County dog licences.
curl -fsSL "$RAW/examples/viz/smart_allegheny_dogs.html" -o "$OUT/allegheny-dogs-smart.html"

viz() { "$QSV" viz "$@" 2>/dev/null; }
viz smart "$OUT/csv/nyc311-100.csv" -o "$OUT/nyc311-smart.html"
viz smart "$OUT/csv/boston311-100.csv" -o "$OUT/boston311-smart.html"
viz smart "$OUT/csv/us-states-lookup.csv" -o "$OUT/us-states-smart.html"
viz smart "$OUT/csv/adur-public-toilets.csv" -o "$OUT/adur-toilets-smart.html"
QSV_VIZ_NO_COMPRESS=1 viz smart "$OUT/csv/nyc311-100.csv" -o "$OUT/nyc311-smart-nocompress.html"
viz pie "$OUT/csv/nyc311-100.csv" --x Borough \
    --title "NYC 311 requests by borough" -o "$OUT/nyc311-pie.html"
viz map "$OUT/csv/nyc311-100.csv" --lat Latitude --lon Longitude \
    --title "NYC 311 requests (100-row sample)" -o "$OUT/nyc311-map.html"
if [ -n "$QSV_RELEASE" ]; then
    "$QSV_RELEASE" viz smart "$OUT/csv/boston311-100.csv" \
        -o "$OUT/boston311-smart-release.html" 2>/dev/null
fi
ls -la "$OUT"/*.html

# Static previews, 16:9, which seed_schematics.py uploads and picks as each view's
# thumbnail in the resource list. They need a qsv built with the viz_static feature,
# plus Chrome and a matching chromedriver (see `qsv viz --help`); without them,
# they're skipped. A schematic's preview shows its first four panels.
if "$QSV" --version 2>/dev/null | grep -q viz_static; then
    png() {
        "$QSV" viz "$@" --width 1280 --height 720 2>/dev/null \
            || echo "static export failed: $*" >&2
    }
    SMART=(--max-charts 4 --grid-cols 4)
    dogs="$OUT/csv/allegheny_dog_licenses.csv"
    [ -s "$dogs" ] || curl -fsSL "$RAW/examples/viz/allegheny_dog_licenses.csv" -o "$dogs"
    png smart "$dogs" "${SMART[@]}" -o "$OUT/allegheny-dogs-smart.png"
    png smart "$OUT/csv/nyc311-100.csv" "${SMART[@]}" -o "$OUT/nyc311-smart.png"
    png smart "$OUT/csv/boston311-100.csv" "${SMART[@]}" -o "$OUT/boston311-smart.png"
    png smart "$OUT/csv/us-states-lookup.csv" "${SMART[@]}" -o "$OUT/us-states-smart.png"
    png smart "$OUT/csv/adur-public-toilets.csv" "${SMART[@]}" -o "$OUT/adur-toilets-smart.png"
    png pie "$OUT/csv/nyc311-100.csv" --x Borough \
        --title "NYC 311 requests by borough" -o "$OUT/nyc311-pie.png"
    png map "$OUT/csv/nyc311-100.csv" --lat Latitude --lon Longitude \
        --title "NYC 311 requests (100-row sample)" -o "$OUT/nyc311-map.png"
    ls -la "$OUT"/*.png 2>/dev/null || echo "no static previews were made" >&2
else
    echo "no static previews: $QSV lacks the viz_static feature" >&2
fi
