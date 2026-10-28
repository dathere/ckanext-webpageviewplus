"""Seed the data the browser tests use: a public dataset `wvp-browser` with
the qsv protocol fixture uploaded and one view per case. Idempotent: the
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

DATASET = "wvp-browser"
ORG = "wvp-browser-org"
FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "ckanext", "webpageviewplus", "tests", "fixtures", "qsv_protocol.html",
)
VIEWS = [
    ("Schematic auto", {"preset": "data-schematic", "height_mode": "auto"}),
    ("Interactive auto", {"preset": "interactive", "height_mode": "auto"}),
    ("Interactive fixed 300", {
        "preset": "interactive", "height_mode": "fixed", "height_value": "300",
    }),
    # No scripts run: the fixture stays as served, and nothing resizes it.
    ("Strict", {"preset": "strict"}),
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
            "name": DATASET, "title": "webpageviewplus browser tests",
            "owner_org": org["id"], "private": False,
        })
        with open(FIXTURE, "rb") as f:
            body = f.read()
        resource = act("resource_create")(ctx(), {
            "package_id": pkg["id"], "name": "qsv protocol fixture",
            "format": "HTML", "url": "qsv_protocol.html",
            "upload": FileStorage(
                io.BytesIO(body), filename="qsv_protocol.html",
                content_type="text/html",
            ),
        })
        for v in act("resource_view_list")(ctx(), {"id": resource["id"]}):
            act("resource_view_delete")(ctx(), {"id": v["id"]})
        for title, fields in VIEWS:
            act("resource_view_create")(ctx(), dict(
                resource_id=resource["id"], view_type="webpage_view_plus",
                title=title, **fields,
            ))
        print(resource["id"])


if __name__ == "__main__":
    main()
