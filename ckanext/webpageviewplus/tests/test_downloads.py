import io

import pytest
from werkzeug.datastructures import FileStorage

from ckan.plugins.toolkit import url_for
from ckan.tests import factories

SANDBOX = "sandbox allow-scripts"


def _upload(content, filename, mimetype=None):
    dataset = factories.Dataset()
    fields = {}
    if mimetype:
        fields["mimetype"] = mimetype
    resource = factories.Resource(
        package_id=dataset["id"],
        url=filename,
        url_type="upload",
        upload=FileStorage(io.BytesIO(content), filename=filename),
        **fields,
    )
    return url_for(
        "{}_resource.download".format(dataset["type"]),
        id=dataset["id"],
        resource_id=resource["id"],
        filename=filename,
    )


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
class TestHardenedDownloads:
    def test_html_upload_is_sandboxed(self, app, local_storage):
        url = _upload(b"<script>parent.x</script>", "page.html", "text/html")
        response = app.get(url)
        assert response.status_code == 200
        csp = response.headers.getlist("Content-Security-Policy")
        assert any(p.startswith(SANDBOX) and "allow-same-origin" not in p for p in csp)
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        # Inline, so a view framing the download shows the page (CKAN 2.12's
        # storages send other types as attachments).
        assert "attachment" not in response.headers.get("Content-Disposition", "")

    def test_svg_upload_is_sandboxed(self, app, local_storage):
        url = _upload(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "a.svg", "image/svg+xml")
        response = app.get(url)
        assert any(p.startswith(SANDBOX) for p in response.headers.getlist("Content-Security-Policy"))

    def test_csv_upload_untouched(self, app, local_storage):
        url = _upload(b"a,b\n1,2\n", "data.csv", "text/csv")
        response = app.get(url)
        assert not response.headers.getlist("Content-Security-Policy")
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_pdf_upload_not_sandboxed(self, app, local_storage):
        # Chrome refuses its PDF viewer in a sandboxed document.
        url = _upload(b"%PDF-1.4\n", "report.pdf", "application/pdf")
        assert not app.get(url).headers.getlist("Content-Security-Policy")

    @pytest.mark.parametrize("mimetype", [
        "text/plain, text/html",  # browsers take the last type
        "text/csv, text/html",
        "unknown/unknown",  # sniffed
        "html",
        " ",
        "application/x-unlisted",
    ])
    def test_crafted_mimetype_is_sandboxed(self, app, local_storage, mimetype):
        url = _upload(b"<script>parent.x</script>", "notes.txt", mimetype)
        response = app.get(url)
        csp = response.headers.getlist("Content-Security-Policy")
        assert any(p.startswith(SANDBOX) for p in csp), mimetype
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    @pytest.mark.ckan_config("ckanext.webpageviewplus.harden_downloads", "false")
    def test_can_be_turned_off(self, app, local_storage):
        url = _upload(b"<p>x</p>", "page.html", "text/html")
        response = app.get(url)
        assert not response.headers.getlist("Content-Security-Policy")

    def test_other_pages_untouched(self, app):
        response = app.get(url_for("home.index"))
        assert not any(
            p.startswith("sandbox") for p in response.headers.getlist("Content-Security-Policy")
        )


@pytest.mark.parametrize(
    "mimetype,active",
    [
        ("text/html", True),
        ("application/atom+xml", True),
        ("application/rss+xml", True),
        ("Image/SVG+XML", True),
        ("text/xml", True),
        ("text/csv", False),
        ("application/json", False),
    ],
)
def test_active_types(mimetype, active):
    from ckanext.webpageviewplus.downloads import is_active_type

    assert is_active_type(mimetype) is active


@pytest.mark.parametrize(
    "content_type,passive",
    [
        ("text/csv; charset=utf-8", True),
        ("image/png", True),
        ("application/pdf", True),
        ("application/geo+json", True),
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", True),
        ("image/svg+xml", False),
        ("application/vnd.google-earth.kml+xml", False),
        ("text/html; charset=utf-8", False),
        ("text/plain, text/html", False),
        ("text/plain,text/html", False),
        ("unknown/unknown", False),
        ("*/*", False),
        ("html", False),
        ("", False),
        ("multipart/x-mixed-replace", False),
        ("text/xsl", False),
    ],
)
def test_passive_types(content_type, passive):
    from ckanext.webpageviewplus.downloads import is_passive_type

    assert is_passive_type(content_type) is passive


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("dataset_resource.download", True),
        ("custom_type_resource.download", True),
        ("resource.download", True),  # CKAN 2.10's second blueprint
        ("resource_proxy.proxy_view", True),
        ("dataset_resource.read", False),
    ],
)
def test_endpoints_covered(endpoint, expected):
    from ckanext.webpageviewplus.downloads import _is_ckan_served_resource

    assert _is_ckan_served_resource(endpoint) is expected
