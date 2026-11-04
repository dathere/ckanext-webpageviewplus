"""Seed the data the browser tests use: a public dataset `wvp-browser` with
two uploaded fixtures (the qsv protocol, and the generic resize message) and
one view per case. View titles are unique across both. For the thumbnails
plugin, an image, a page whose view picks it, and links to two well-known
services (one with a logo, one without); nothing is fetched, as the
browser CI runs without thumbnail_fetch. With repo_view_plus, two
Repository views whose GitHub answers are written into CKAN's cache, so
GitHub is never asked. Idempotent: the
dataset is purged and recreated. Run inside the CKAN container, with
CKAN_INI pointing at the site's config:

    python browser_tests/seed.py

It calls actions with ignore_auth as the site user, so no API token is
needed.
"""
import hashlib
import io
import os
import time

from werkzeug.datastructures import FileStorage

from ckan.cli import load_config
from ckan.config.middleware import make_app
import ckan.plugins as plugins
import ckan.plugins.toolkit as tk
from PIL import Image, ImageDraw

from ckanext.webpageviewplus import services

DATASET = "wvp-browser"
ORG = "wvp-browser-org"
FIXTURES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "ckanext", "webpageviewplus", "tests", "fixtures",
)
VIEWS = [
    ("Schematic auto", {"preset": "data-schematic", "height_mode": "auto"}),
    ("Interactive auto", {"preset": "interactive", "height_mode": "auto"}),
    ("Interactive fixed 300", {
        "preset": "interactive", "height_mode": "fixed", "height_value": "300",
    }),
    ("Aspect 16/9", {
        "preset": "interactive", "height_mode": "aspect", "height_value": "16/9",
    }),
    ("Fixed 300 no bar", {
        "preset": "interactive", "height_mode": "fixed", "height_value": "300",
        "hide_controls": True,
    }),
    # Taller than half a phone's screen: gets the tap-to-interact shield.
    ("Interactive fixed 700", {
        "preset": "interactive", "height_mode": "fixed", "height_value": "700",
    }),
    # Counted when the site has view_counting on (CI does).
    ("Counted fixed 300", {
        "preset": "interactive", "height_mode": "fixed", "height_value": "300",
        "count_views": True,
    }),
    # No scripts run: the fixture stays as served, and nothing resizes it.
    ("Strict", {"preset": "strict"}),
    # Auto, but the page can't report (no scripts): it keeps the fallback.
    ("Strict auto", {"preset": "strict", "height_mode": "auto"}),
    # A third-party page behind the consent card. The tests check the card
    # and what's remembered, not the page, so example.com needn't load.
    ("Consent example.com", {
        "preset": "interactive", "page_url": "https://example.com/", "consent_gate": True,
    }),
]
# The page reports its height with {type: "webpageviewplus:resize"} only.
GENERIC_VIEWS = [
    ("Generic auto", {"preset": "interactive", "height_mode": "auto"}),
    ("Generic schematic auto", {"preset": "data-schematic", "height_mode": "auto"}),
]
RESOURCES = [
    ("qsv protocol fixture", "qsv_protocol.html", VIEWS),
    ("Resize message fixture", "resize_message.html", GENERIC_VIEWS),
]

# Resource name: link. Each gets one view, with what the view form suggests.
SERVICE_LINKS = [
    ("Vimeo link", "https://vimeo.com/76979871"),
    ("Power BI link", "https://app.powerbi.com/view?r=eyJrIjoiYjRlZjg5YzItOWM4Ni00MGNkLWI1OG"
     "YtZDA4MDAxNGYyNDQyIiwidCI6ImQ2NDk2NzM4LWY5MTItNGExZS04NDE1LTQwY2E2ZjRhOTRlZCJ9"),
]
PICKED_PAGE = b"<!doctype html><title>Picked thumbnail</title><p>Its view picks an image.</p>"

# repo_view_plus: a copy of each repository's page is written where CKAN
# keeps it, so the views never ask GitHub (ci.ini keeps copies for a year).
REPO = "https://github.com/wvp-browser/example"
MISSING_REPO = "https://github.com/wvp-browser/missing"
REPO_README = (
    '<div id="readme" data-path="README.md"><article>'
    '<h1>Example</h1><p>Jump to <a href="#usage">Usage</a>, or see '
    '<a href="https://example.com/">example.com</a>.</p>'
    + "".join("<p>Filler paragraph {}.</p>".format(i) for i in range(60))
    + '<h2><a id="user-content-usage" class="anchor" href="#usage">#</a>Usage</h2>'
    '<p>The end of the README.</p></article></div>'
)


def _png():
    image = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(image)
    for i, h in enumerate([120, 200, 160, 280, 240]):
        draw.rectangle([60 + i * 110, 330 - h, 140 + i * 110, 330], fill=(31, 119, 180))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _upload(body, filename, content_type):
    return FileStorage(io.BytesIO(body), filename=filename, content_type=content_type)


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
            "name": DATASET, "title": "webpageviewplus browser tests",
            "owner_org": org["id"], "private": False,
        })
        for name, filename, views in RESOURCES:
            with open(os.path.join(FIXTURES, filename), "rb") as f:
                body = f.read()
            resource = act("resource_create")(ctx(), {
                "package_id": pkg["id"], "name": name,
                "format": "HTML", "url": filename,
                "upload": FileStorage(
                    io.BytesIO(body), filename=filename, content_type="text/html",
                ),
            })
            for v in act("resource_view_list")(ctx(), {"id": resource["id"]}):
                act("resource_view_delete")(ctx(), {"id": v["id"]})
            for title, fields in views:
                act("resource_view_create")(ctx(), dict(
                    resource_id=resource["id"], view_type="webpage_view_plus",
                    title=title, **fields,
                ))
            print(resource["id"])

        def resource_with_view(resource, title, **fields):
            resource = act("resource_create")(ctx(), dict(package_id=pkg["id"], **resource))
            # Only this view, as a service's logo needs the resource's only view.
            for v in act("resource_view_list")(ctx(), {"id": resource["id"]}):
                act("resource_view_delete")(ctx(), {"id": v["id"]})
            act("resource_view_create")(ctx(), dict(
                resource_id=resource["id"], view_type="webpage_view_plus",
                title=title, **fields,
            ))

        image = act("resource_create")(ctx(), {
            "package_id": pkg["id"], "name": "Thumbnail image", "format": "PNG",
            "url": "thumbnail.png", "upload": _upload(_png(), "thumbnail.png", "image/png"),
        })
        resource_with_view(
            {"name": "Picked thumbnail page", "format": "HTML", "url": "picked.html",
             "upload": _upload(PICKED_PAGE, "picked.html", "text/html")},
            "Picked thumbnail", preset="interactive", thumbnail_resource=image["id"],
        )
        for name, url in SERVICE_LINKS:
            found = services.suggest(url)
            fields = {"page_url": found.embed_url, "preset": found.preset}
            if found.height_mode:
                fields.update(height_mode=found.height_mode, height_value=found.height_value)
            resource_with_view(
                {"name": name, "url": url}, "{} ({})".format(found.service, found.preset),
                **fields)

        if plugins.plugin_loaded("repo_view_plus"):
            _seed_repos(act, ctx, pkg)

        # pdf_view_plus: an uploaded PDF, shown by PDF.js from the content origin.
        if plugins.plugin_loaded("pdf_view_plus"):
            with open(os.path.join(FIXTURES, "document.pdf"), "rb") as f:
                body = f.read()
            resource = act("resource_create")(ctx(), {
                "package_id": pkg["id"], "name": "PDF fixture", "format": "PDF",
                "url": "document.pdf",
                "upload": _upload(body, "document.pdf", "application/pdf"),
            })
            for v in act("resource_view_list")(ctx(), {"id": resource["id"]}):
                act("resource_view_delete")(ctx(), {"id": v["id"]})
            act("resource_view_create")(ctx(), {
                "resource_id": resource["id"], "view_type": "pdf_view_plus",
                "title": "PDF fixture",
            })


def _seed_repos(act, ctx, pkg):
    from ckanext.webpageviewplus import repos

    folder = repos._folder()
    os.makedirs(folder, exist_ok=True)

    def base(url):
        return os.path.join(folder, hashlib.sha256(repos.parse(url).key.encode()).hexdigest())

    for suffix in (".json", ".failed", ".lock"):
        for url in (REPO, MISSING_REPO):
            if os.path.exists(base(url) + suffix):
                os.remove(base(url) + suffix)
    repos._write(base(REPO) + ".json", {
        "site": "GitHub", "full_name": "wvp-browser/example",
        "html_url": REPO, "description": "A repository the browser tests show",
        "language": "Python", "stars": 1234, "forks": 56,
        "pushed_at": "2026-11-01T12:00:00Z", "archived": False, "ref": "main",
        "entries": [
            {"name": "docs", "type": "dir", "url": REPO + "/tree/main/docs"},
            {"name": "README.md", "type": "file", "url": REPO + "/blob/main/README.md"},
        ],
        "truncated": False,
        "readme": repos.clean_readme(REPO_README, "wvp-browser", "example", "main",
                                     "README.md"),
    })
    # GitHub's 404, remembered for a year: the view shows its card.
    repos._write(base(MISSING_REPO) + ".failed",
                 {"kind": "missing", "until": time.time() + 365 * 86400})
    for name, url, title in (("Repository link", REPO, "Repository"),
                             ("Missing repository link", MISSING_REPO, "Missing repository")):
        resource = act("resource_create")(ctx(), {
            "package_id": pkg["id"], "name": name, "url": url})
        for v in act("resource_view_list")(ctx(), {"id": resource["id"]}):
            act("resource_view_delete")(ctx(), {"id": v["id"]})
        act("resource_view_create")(ctx(), {
            "resource_id": resource["id"], "view_type": repos.VIEW_TYPE, "title": title,
            "height_mode": "fixed", "height_value": "400",
        })


if __name__ == "__main__":
    main()
