"""Demo dataset `wvp-features`: one resource per feature of webpage_view_plus,
each view described as a short story of what to look for. Run inside the
CKAN container, where the repo is mounted:

    python /srv/app/src_extensions/ckanext-webpageviewplus/demo/seed_features.py

Idempotent: the dataset is purged and recreated. Actions run as the site user
with ignore_auth, so no API token is needed. Some features depend on site
settings (a content origin, frameability_check); their descriptions say so.
"""
import io
import os
import sys

from werkzeug.datastructures import FileStorage

from ckan.cli import load_config
from ckan.config.middleware import make_app
import ckan.plugins.toolkit as tk

DATASET = "wvp-features"
ORG = "wvp-demo-org"
FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "ckanext", "webpageviewplus", "tests", "fixtures", "qsv_protocol.html",
)

NOTES = """\
A tour of webpage_view_plus, one resource per feature. Open each resource and
read the view's description: it says what to look for.

Also try:

- The page in another language: put `/de`, `/es`, `/fr`, `/it`, `/ja` or
  `/pt_BR` before `/dataset` in the address.
- A phone, or your browser's device mode: tall views get "Tap to interact" so
  the page still scrolls, and Fullscreen becomes Expand on iPhone.
- `ckan webpageviewplus check-views --dataset wvp-features` in the container:
  it reports the page that refuses framing and the one that's gone.
"""

# (resource name, resource url or None for the upload, views)
# Each view: (title, fields, description). Descriptions are Markdown.
RESOURCES = [
    ("Uploaded HTML on the content origin", None, [
        ("Data Schematic preset, self-sizing",
         {"preset": "data-schematic", "height_mode": "auto"},
         "An uploaded HTML page, the kind that core CKAN would serve from its own "
         "address, where its scripts could act with your login. Here it's served from "
         "the content origin instead. The page prints where it thinks it lives and "
         "tries to reach this page: it reports its own origin, not CKAN's, and fails. "
         "It also reports its height, so the frame fits it exactly. Without "
         "`content_origin` set, it still runs, sandboxed with no origin at all."),
        ("Strict preset: no scripts",
         {"preset": "strict"},
         "The same upload with scripts switched off entirely. What's left is the "
         "page's static HTML: no origin printed, no resizing, nothing that runs. "
         "`strict` suits documents that don't need JavaScript."),
    ]),
    ("Wikipedia: Open data", "https://en.wikipedia.org/wiki/Open_data", [
        ("Strict preset, 70% of the window",
         {"preset": "strict", "height_mode": "viewport", "height_value": "70"},
         "A third-party page that reads fine without JavaScript, so it gets the "
         "tightest sandbox: no scripts, no forms, no popups. The view takes 70% of the "
         "window. The bar below it opens the article in a new tab, or reloads it."),
        ("Same, without the control bar",
         {"preset": "strict", "height_mode": "viewport", "height_value": "70",
          "hide_controls": True},
         "The bar is hidden for this view. On a phone, or at 400% zoom, Open in new "
         "tab still appears, so nobody is left stuck inside a small frame."),
    ]),
    ("Datawrapper chart", "https://www.datawrapper.de/_/XSj9X/", [
        ("Interactive preset, fixed 500 px",
         {"page_url": "https://datawrapper.dwcdn.net/XSj9X/", "preset": "interactive",
          "height_mode": "fixed", "height_value": "500"},
         "A chart from Datawrapper. Its scripts run so the tooltips work, but the "
         "frame has no origin of its own: it can't read cookies, storage, or anything "
         "on this page. The resource links to the chart's page; the view frames its "
         "embed address, which is what the view form offers when you paste the link."),
    ]),
    ("YouTube video", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", [
        ("External preset, 16:9",
         {"page_url": "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
          "preset": "external", "height_mode": "aspect", "height_value": "16/9"},
         "You know the song. It is never going to give you up, and it is never going "
         "to run as CKAN either. "
         "YouTube needs its own origin to play, so this view uses `external`: no "
         "sandbox. That's safe only because uploads live on a separate content origin; "
         "without one, the view falls back to `interactive` and the player shows an "
         "error. It frames the privacy-enhanced youtube-nocookie.com player and keeps "
         "a 16:9 shape at any width."),
        ("Ask before loading",
         {"page_url": "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
          "preset": "external", "height_mode": "aspect", "height_value": "16/9",
          "consent_gate": True},
         "The same video behind a consent card: nothing loads from YouTube, and it "
         "learns nothing about you, until you choose Load. Sites can require this for "
         "every third-party page, and it appears on its own for visitors whose browser "
         "sends Global Privacy Control."),
    ]),
    ("Try the view form", "https://vimeo.com/76979871", [
        ("Paste a link to see a suggestion",
         {"preset": "interactive"},
         "This view frames a Vimeo page as pasted, which doesn't play: Vimeo's player "
         "lives at another address. Log in, open Manage, then Views, edit this view "
         "and paste https://vimeo.com/76979871 into the page URL. The form recognises "
         "Vimeo and offers its player address, the preset it needs and a 16:9 height: "
         "choose Use them and save."),
    ]),
    ("A page that refuses to be framed", "https://github.com/ckan/ckan", [
        ("Interactive preset",
         {"preset": "interactive"},
         "GitHub tells browsers never to show it inside a frame. Core CKAN would show "
         "a blank box here. With `frameability_check` on, saving the view checks the "
         "page once, and visitors see a card explaining why it can't be shown, with "
         "Open in new tab, instead of a blank frame. (With the setting off, as on a "
         "fresh dev site, you see the blank frame; turn it on and save the view.)"),
    ]),
    ("A page that's gone", "https://example.com/this-page-is-gone", [
        ("Interactive preset",
         {"preset": "interactive"},
         "Links rot. This one returns 404. A frame can't tell you that, but "
         "`ckan webpageviewplus check-views` can: run it from cron and it lists every "
         "view whose page is gone, unreachable or refuses framing, core "
         "`webpage_view` views included, and exits with status 1 when it finds any."),
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
            "name": DATASET, "title": "webpageviewplus feature tour",
            "notes": NOTES, "owner_org": org["id"], "private": False,
        })
        site = tk.config.get("ckan.site_url").rstrip("/")
        for name, url, views in RESOURCES:
            data = {"package_id": pkg["id"], "name": name, "format": "HTML"}
            if url:
                data["url"] = url
            else:
                with open(FIXTURE, "rb") as f:
                    body = f.read()
                data["url"] = "qsv_protocol.html"
                data["upload"] = FileStorage(
                    io.BytesIO(body), filename="qsv_protocol.html",
                    content_type="text/html")
            res = act("resource_create")(ctx(), data)
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
