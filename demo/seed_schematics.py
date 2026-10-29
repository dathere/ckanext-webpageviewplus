"""Demo dataset `wvp-schematics`: qsv Data Schematics and charts uploaded as
HTML, with views exercising presets and height modes.

Build the HTML on the host first (demo/build_schematics.sh), then run this
inside the CKAN container, where the repo is mounted:

    python /srv/app/src_extensions/ckanext-webpageviewplus/demo/seed_schematics.py

Idempotent: the dataset is purged and recreated. Actions run as the site user
with ignore_auth, so no API token is needed. Files that weren't built (the
release-qsv schematic is optional) are skipped.
"""
import io
import os
import sys

from werkzeug.datastructures import FileStorage

from ckan.cli import load_config
from ckan.config.middleware import make_app
import ckan.plugins.toolkit as tk

DATASET = "wvp-schematics"
ORG = "wvp-demo-org"
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "https://github.com/dathere/qsv/blob/master/"
HEAD = "a qsv that includes PR #4949, so it reports its height when framed"
REL = "a qsv release without PR #4949 (27.0.1 or older), so it doesn't report its height"

AUTO = {"preset": "data-schematic", "height_mode": "auto"}

# (file, resource name, how it was made, source CSV, built with, views)
# Each view: (title, fields, description). Descriptions are Markdown.
RESOURCES = [
    ("nyc311-smart.html", "NYC 311 requests: Data Schematic",
     "`qsv viz smart`", ".claude/skills/tests/fixtures/nyc311-100.csv", HEAD, [
         ("Data Schematic (self-sizing)", AUTO,
          "A hundred calls to New York's 311 line, and qsv picked a chart for every "
          "column on its own. Noise leads: from neighbours (8 calls) and from bars and "
          "shops (7), ahead of no heat or hot water (5). Queens calls the most. "
          "The frame grows to fit the whole dashboard: the schematic tells the page "
          "its height, so there's no scrollbar inside a scrollbar."),
         ("Interactive preset, self-sizing",
          {"preset": "interactive", "height_mode": "auto"},
          "The same dashboard in the `interactive` preset, which leaves out "
          "`allow-same-origin`. The page has no origin of its own, so it can't read "
          "cookies or storage, and its height messages arrive from origin `null`. The "
          "view still trusts them, because it checks which frame sent each message."),
     ]),
    ("boston311-smart.html", "Boston 311 requests: Data Schematic",
     "`qsv viz smart`", "resources/test/boston311-100.csv", HEAD, [
         ("Data Schematic (self-sizing)", AUTO,
          "A hundred Boston service requests. Parking enforcement is a fifth of them, "
          "and Dorchester files the most. Scroll the frame and you scroll the whole "
          "story: it's as tall as the dashboard needs."),
         ("Fixed 800 px",
          {"preset": "data-schematic", "height_mode": "fixed", "height_value": "800"},
          "The same schematic in a fixed 800 px window, for pages where a long view "
          "would push everything else down. The rest of the story is a scroll away, "
          "inside the frame."),
     ]),
    ("boston311-smart-release.html", "Boston 311 requests: Data Schematic (older qsv)",
     "`qsv viz smart`", "resources/test/boston311-100.csv", REL, [
         ("Data Schematic (auto, fallback height)", AUTO,
          "The same Boston data from a qsv that doesn't report its height. The view "
          "asks for auto height, waits, hears nothing, and keeps a sensible 600 px. "
          "Nothing is injected into the upload to measure it: older schematics just "
          "get the fallback."),
     ]),
    ("us-states-smart.html", "US states: Data Schematic",
     "`qsv viz smart`", "resources/test/us-states-lookup.csv", HEAD, [
         ("Data Schematic (self-sizing)", AUTO,
          "Fifty-odd rows, one per state: capitals, population, area and sales tax. A "
          "small table makes a short schematic, and the frame shrinks to match instead "
          "of leaving a blank 600 px box."),
     ]),
    ("adur-toilets-smart.html", "Adur public toilets: Data Schematic",
     "`qsv viz smart`", "resources/test/adur-public-toilets.csv", HEAD, [
         ("Data Schematic (self-sizing)", AUTO,
          "Fifteen public toilets in Adur, on England's south coast, described in 32 "
          "columns. Most are open 9 to 9 in summer and 9 to 5 in winter, and none has "
          "baby changing. A narrow table with a wide schema makes a long schematic."),
         ("80% of the window",
          {"preset": "data-schematic", "height_mode": "viewport", "height_value": "80"},
          "Sized to 80% of the browser window instead of to the page, so the view "
          "fills a laptop screen or a monitor alike. Resize the window and it follows."),
     ]),
    ("nyc311-smart-nocompress.html", "NYC 311 requests: Data Schematic (uncompressed)",
     "`QSV_VIZ_NO_COMPRESS=1 qsv viz smart`", ".claude/skills/tests/fixtures/nyc311-100.csv", HEAD, [
         ("Data Schematic (self-sizing)", AUTO,
          "The NYC dashboard again, as plain-text HTML for browsers older than 2023 "
          "(roughly twice the size). It looks the same: compressed or not, a "
          "schematic runs inside the sandbox."),
     ]),
    ("nyc311-pie.html", "NYC 311 requests by borough",
     "`qsv viz pie --x Borough`", ".claude/skills/tests/fixtures/nyc311-100.csv", HEAD, [
         ("Pie chart (4:3)",
          {"preset": "data-schematic", "height_mode": "aspect", "height_value": "4/3"},
          "Queens takes the biggest slice, with Brooklyn and Manhattan tied behind it. "
          "One chart, so the view keeps a 4:3 shape at any width instead of asking the "
          "page for its height."),
     ]),
    ("nyc311-map.html", "NYC 311 request map",
     "`qsv viz map --lat Latitude --lon Longitude`", ".claude/skills/tests/fixtures/nyc311-100.csv", HEAD, [
         ("Map (fixed 600 px)",
          {"preset": "data-schematic", "height_mode": "fixed", "height_value": "600"},
          "Where the calls came from. The points are in the upload; the basemap tiles "
          "load from the network when you view it, which the sandbox allows."),
     ]),
]


def main():
    app = make_app(load_config(os.environ["CKAN_INI"]))
    with app._wsgi_app.test_request_context():
        act = tk.get_action
        site_user = act("get_site_user")({"ignore_auth": True}, {})["name"]

        def ctx():
            return {"ignore_auth": True, "user": site_user}

        try:
            act("dataset_purge")(ctx(), {"id": DATASET})
        except tk.ObjectNotFound:
            pass
        try:
            org = act("organization_show")(ctx(), {"id": ORG})
        except tk.ObjectNotFound:
            org = act("organization_create")(ctx(), {"name": ORG})
        pkg = act("package_create")(ctx(), {
            "name": DATASET, "title": "qsv Data Schematics (webpageviewplus demo)",
            "notes": "Data Schematics and charts made with `qsv viz` from qsv's test "
                     "CSVs, uploaded as HTML and shown with the `data-schematic` "
                     "preset on the content origin.",
            "owner_org": org["id"], "private": False,
        })
        site = tk.config.get("ckan.site_url").rstrip("/")
        directory = os.path.join(HERE, "schematics")
        for filename, name, how, csv, build, views in RESOURCES:
            path = os.path.join(directory, filename)
            if not os.path.exists(path):
                print("skipped (not built): {}".format(filename))
                continue
            with open(path, "rb") as f:
                body = f.read()
            res = act("resource_create")(ctx(), {
                "package_id": pkg["id"], "name": name, "format": "HTML",
                "url": filename,
                "description": "{} on [{}]({}{}), built with {}.".format(
                    how, os.path.basename(csv), SRC, csv, build),
                "upload": FileStorage(io.BytesIO(body), filename=filename,
                                      content_type="text/html"),
            })
            for v in act("resource_view_list")(ctx(), {"id": res["id"]}):
                act("resource_view_delete")(ctx(), {"id": v["id"]})
            print(name)
            for title, fields, description in views:
                view = act("resource_view_create")(ctx(), dict(
                    resource_id=res["id"], view_type="webpage_view_plus",
                    title=title, description=description, **fields))
                print("  {}: {}/dataset/{}/resource/{}?view_id={}".format(
                    title, site, DATASET, res["id"], view["id"]))


if __name__ == "__main__":
    sys.exit(main())
