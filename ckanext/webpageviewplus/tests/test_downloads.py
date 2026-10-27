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
    def test_html_upload_is_sandboxed(self, app, tmp_path, ckan_config, monkeypatch):
        monkeypatch.setitem(ckan_config, "ckan.storage_path", str(tmp_path))
        url = _upload(b"<script>parent.x</script>", "page.html", "text/html")
        response = app.get(url)
        assert response.status_code == 200
        csp = response.headers.getlist("Content-Security-Policy")
        assert any(p.startswith(SANDBOX) and "allow-same-origin" not in p for p in csp)
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_svg_upload_is_sandboxed(self, app, tmp_path, ckan_config, monkeypatch):
        monkeypatch.setitem(ckan_config, "ckan.storage_path", str(tmp_path))
        url = _upload(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "a.svg", "image/svg+xml")
        response = app.get(url)
        assert any(p.startswith(SANDBOX) for p in response.headers.getlist("Content-Security-Policy"))

    def test_csv_upload_untouched(self, app, tmp_path, ckan_config, monkeypatch):
        monkeypatch.setitem(ckan_config, "ckan.storage_path", str(tmp_path))
        url = _upload(b"a,b\n1,2\n", "data.csv", "text/csv")
        response = app.get(url)
        assert not response.headers.getlist("Content-Security-Policy")

    @pytest.mark.ckan_config("ckanext.webpageviewplus.harden_downloads", "false")
    def test_can_be_turned_off(self, app, tmp_path, ckan_config, monkeypatch):
        monkeypatch.setitem(ckan_config, "ckan.storage_path", str(tmp_path))
        url = _upload(b"<p>x</p>", "page.html", "text/html")
        response = app.get(url)
        assert not response.headers.getlist("Content-Security-Policy")

    def test_other_pages_untouched(self, app):
        response = app.get(url_for("home.index"))
        assert not any(
            p.startswith("sandbox") for p in response.headers.getlist("Content-Security-Policy")
        )
