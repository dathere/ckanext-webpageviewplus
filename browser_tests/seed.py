"""Seed the data the browser tests use: a public dataset `wvp-browser` with
two uploaded fixtures (the qsv protocol, and the generic resize message) and
one view per case. View titles are unique across both. For the thumbnails
plugin, an image, a page whose view picks it, and links to two well-known
services (one with a logo, one without); nothing is fetched, as the
browser CI runs without thumbnail_fetch. Idempotent: the
dataset is purged and recreated. Run inside the CKAN container, with
CKAN_INI pointing at the site's config:

    python browser_tests/seed.py

It calls actions with ignore_auth as the site user, so no API token is
needed.
"""
import io
import os

from werkzeug.datastructures import FileStorage

from ckan.cli import load_config
from ckan.config.middleware import make_app
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


if __name__ == "__main__":
    main()
