import io
import os
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from werkzeug.datastructures import FileStorage

from ckan.exceptions import CkanConfigurationException
from ckan.tests import factories, helpers

from ckanext.webpageviewplus import content, frameability, pdf, plugin

CONTENT = "http://127.0.0.1"
SECRET = "k" * 40
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "document.pdf")
LINK = "https://files.example.org/r/report.pdf"

with open(FIXTURE, "rb") as f:
    PDF = f.read()

pytestmark = [
    pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus pdf_view_plus"),
    pytest.mark.ckan_config("ckanext.webpageviewplus.content_origin", CONTENT),
    pytest.mark.ckan_config("ckanext.webpageviewplus.signing_secret", SECRET),
    pytest.mark.usefixtures("non_clean_db", "with_plugins"),
]


@pytest.fixture(params=["local", "memory"])
def storage(request):
    return request.getfixturevalue("{}_storage".format(request.param))


@pytest.fixture
def fetches(monkeypatch, tmp_path, ckan_config):
    """Linked PDFs: answers from a dict of URL -> bytes instead of the
    network, counting the fetches, with the copies kept in tmp_path."""
    monkeypatch.setitem(ckan_config, "ckan.storage_path", str(tmp_path))
    bodies = {LINK: PDF}
    calls = []

    def fetch_body(url, deadline, max_bytes, user_agent, truncate=False):
        calls.append(url)
        data = bodies.get(url)
        if data is None or len(data) > max_bytes:
            return None
        return frameability.Body(url, "application/pdf", None, data)

    monkeypatch.setattr(frameability, "fetch_body", fetch_body)
    return bodies, calls


def _upload(dataset, body=PDF):
    return factories.Resource(
        package_id=dataset["id"], url="document.pdf", url_type="upload", format="PDF",
        upload=FileStorage(io.BytesIO(body), filename="document.pdf"),
    )


def _view(resource, **fields):
    return helpers.call_action(
        "resource_view_create", resource_id=resource["id"], view_type=pdf.VIEW_TYPE,
        title="A PDF", **fields)


def _private_dataset():
    org = factories.Organization()
    return factories.Dataset(owner_org=org["id"], private=True)


def _file_url(view, resource, private=False):
    found = pdf.source(view, resource)
    return CONTENT + pdf.file_path(view["id"], found[0], found[1], resource, private)


def _get(app, url, **kw):
    parts = urlsplit(url)
    path = parts.path + ("?" + parts.query if parts.query else "")
    return app.get(path, base_url="{}://{}".format(parts.scheme, parts.netloc), **kw)


class TestHelpers:
    @pytest.mark.parametrize("data, expected", [
        (PDF, True),
        (b"\n" * 100 + b"%PDF-1.7", True),
        (b"x" * 1100 + b"%PDF-1.7", False),
        (b"<!doctype html>", False),
        (b"", False),
    ])
    def test_looks_like_pdf(self, data, expected):
        assert pdf.looks_like_pdf(data) is expected

    @pytest.mark.parametrize("ckan_lang, expected", [
        ("en", "en-us"),
        ("de", "de"),
        ("pt_BR", "pt-br"),
        ("pt_PT", "pt-br"),  # only Brazilian Portuguese is shipped
        ("es", "es-es"),
        ("es_AR", "es-es"),
        ("uk_UA", "uk"),
        ("th", "th"),
        ("pl", "en-us"),  # not shipped: English
        ("zh_Hans_CN", "en-us"),
    ])
    def test_pdfjs_lang(self, ckan_lang, expected):
        assert pdf.pdfjs_lang(ckan_lang) == expected

    def test_source(self):
        upload = {"url_type": "upload", "url": "a.pdf", "id": "x", "package_id": "p"}
        link = {"url": LINK}
        assert pdf.source({"pdf_url": "https://b.org/b.pdf"}, upload) == ("link", "https://b.org/b.pdf")
        assert pdf.source({}, link) == ("link", LINK)
        assert pdf.source({"pdf_url": "javascript:alert(1)"}, link) is None
        assert pdf.source({}, {"url": "ftp://x.org/a.pdf"}) is None

    def test_cache_control(self):
        assert content.cache_control("200 OK", "immutable") == "public, max-age=31536000, immutable"
        assert content.cache_control("200 OK", "max-age=60") == "public, max-age=60"
        assert content.cache_control("404 NOT FOUND", "immutable") == "no-store"


@pytest.mark.usefixtures("storage")
class TestUploads:
    def test_public_upload(self, app):
        resource = _upload(factories.Dataset())
        view = _view(resource)
        response = _get(app, _file_url(view, resource))
        assert response.status_code == 200
        assert response.data == PDF
        assert response.headers["Content-Type"] == "application/pdf"
        assert response.headers["Content-Security-Policy"].startswith("sandbox; default-src 'none'; frame-ancestors ")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Cache-Control"] == "public, no-cache"
        assert "Set-Cookie" not in response.headers

    def test_private_upload_needs_signature(self, app):
        resource = _upload(_private_dataset())
        view = _view(resource)
        assert _get(app, _file_url(view, resource), status=404).status_code == 404
        response = _get(app, _file_url(view, resource, private=True))
        assert response.status_code == 200
        assert response.headers["Cache-Control"].startswith("private, max-age=")

    def test_private_viewer_url_round_trip(self, app):
        # What a reader of a private dataset gets: the viewer URL the page
        # renders, whose file= is the signed PDF URL PDF.js then fetches.
        resource = _upload(_private_dataset())
        view = _view(resource)
        url = pdf.viewer_url(view["id"], "upload", "", resource, True, "de")
        query = parse_qs(urlsplit(url).query)
        assert query["wvp_lang"] == ["de"]
        file = query["file"][0]
        assert "?w=" in file and "&s=" in file
        assert _get(app, CONTENT + file).status_code == 200

    def test_content_signature_does_not_open_a_view(self, app):
        # A resource's content signature is for its HTML, not for a view's PDF.
        resource = _upload(_private_dataset())
        view = _view(resource)
        w = content._window()
        url = CONTENT + "/webpageviewplus/pdf/{}/document.pdf?w={}&s={}".format(
            view["id"], w, content._signature(resource["id"], w))
        assert _get(app, url, status=404).status_code == 404

    def test_not_on_ckan_host(self, app):
        resource = _upload(factories.Dataset())
        view = _view(resource)
        path = urlsplit(_file_url(view, resource)).path
        assert app.get(path, status=404).status_code == 404

    def test_other_view_types_refused(self, app):
        resource = _upload(factories.Dataset())
        view = helpers.call_action(
            "resource_view_create", resource_id=resource["id"],
            view_type="webpage_view_plus", title="page")
        url = CONTENT + "/webpageviewplus/pdf/{}/document.pdf".format(view["id"])
        assert _get(app, url, status=404).status_code == 404


class TestLinks:
    def test_linked_pdf_fetched_once(self, app, fetches):
        _, calls = fetches
        resource = factories.Resource(package_id=factories.Dataset()["id"], url=LINK, format="PDF")
        view = _view(resource)
        for _ in range(2):
            response = _get(app, _file_url(view, resource))
            assert response.status_code == 200
            assert response.headers["Content-Type"] == "application/pdf"
            assert response.headers["Cache-Control"] == "public, max-age=3600"
        assert calls == [LINK]

    def test_pdf_url_beats_resource_url(self, app, fetches):
        bodies, calls = fetches
        bodies["https://b.org/other.pdf"] = PDF
        resource = _upload(factories.Dataset())
        view = _view(resource, pdf_url="https://b.org/other.pdf")
        assert _get(app, _file_url(view, resource)).status_code == 200
        assert calls == ["https://b.org/other.pdf"]

    @pytest.mark.ckan_config("ckanext.webpageviewplus.pdf_cache_seconds", "0")
    def test_cache_seconds_zero_fetches_every_time(self, app, fetches):
        _, calls = fetches
        resource = factories.Resource(package_id=factories.Dataset()["id"], url=LINK, format="PDF")
        view = _view(resource)
        _get(app, _file_url(view, resource))
        _get(app, _file_url(view, resource))
        assert calls == [LINK, LINK]

    def test_not_a_pdf_refused(self, app, fetches, events_log):
        bodies, _ = fetches
        bodies[LINK] = b"<!doctype html><script>alert(1)</script>"
        resource = factories.Resource(package_id=factories.Dataset()["id"], url=LINK, format="PDF")
        view = _view(resource)
        assert _get(app, _file_url(view, resource), status=404).status_code == 404
        assert "pdf_fetch_failed" in [e["event"] for e in events_log()]

    def test_no_storage_path(self, app, fetches, monkeypatch, ckan_config):
        _, calls = fetches
        monkeypatch.setitem(ckan_config, "ckan.storage_path", "")
        resource = factories.Resource(package_id=factories.Dataset()["id"], url=LINK, format="PDF")
        view = _view(resource)
        assert _get(app, _file_url(view, resource), status=404).status_code == 404
        assert calls == []

    @pytest.mark.ckan_config("ckanext.webpageviewplus.pdf_max_size", "1")
    def test_too_big_refused(self, app, fetches):
        bodies, _ = fetches
        bodies[LINK] = PDF + b"0" * (1024 * 1024)
        resource = factories.Resource(package_id=factories.Dataset()["id"], url=LINK, format="PDF")
        view = _view(resource)
        assert _get(app, _file_url(view, resource), status=404).status_code == 404

    def test_private_dataset_link_is_signed_and_private(self, app, fetches):
        resource = factories.Resource(package_id=_private_dataset()["id"], url=LINK, format="PDF")
        view = _view(resource)
        assert _get(app, _file_url(view, resource), status=404).status_code == 404
        response = _get(app, _file_url(view, resource, private=True))
        assert response.headers["Cache-Control"].startswith("private, max-age=")

    @pytest.mark.ckan_config("ckanext.webpageviewplus.allowed_hosts", "b.org")
    def test_allowed_hosts(self, app, fetches):
        _, calls = fetches
        resource = factories.Resource(package_id=factories.Dataset()["id"], url=LINK, format="PDF")
        view = _view(resource)
        assert _get(app, _file_url(view, resource), status=404).status_code == 404
        assert calls == []
        with pytest.raises(helpers.logic.ValidationError):
            _view(resource, pdf_url=LINK)


class TestViewer:
    def _asset(self, path):
        return CONTENT + "/webpageviewplus/pdfjs/{}/{}".format(pdf.PDFJS_VERSION, path)

    def test_viewer_page(self, app):
        response = _get(app, self._asset("web/viewer.html"))
        assert response.status_code == 200
        csp = response.headers["Content-Security-Policy"]
        assert "connect-src 'self'" in csp and "frame-ancestors " in csp
        assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        assert "wvp-viewer.js" in response.body

    @pytest.mark.parametrize("path, mimetype", [
        ("build/pdf.worker.mjs", "text/javascript"),
        ("web/wasm/openjpeg.wasm", "application/wasm"),
        ("web/locale/locale.json", "application/json"),
        ("wvp-viewer.js", "text/javascript"),
    ])
    def test_assets_typed(self, app, path, mimetype):
        response = _get(app, self._asset(path))
        assert response.headers["Content-Type"].startswith(mimetype)

    @pytest.mark.parametrize("path", [
        "web/../../../pdf.py", "web/wasm/LICENSE_QCMS", "web/nope.html",
    ])
    def test_refused(self, app, path):
        assert _get(app, self._asset(path), status=404).status_code == 404

    def test_other_version_refused(self, app):
        url = CONTENT + "/webpageviewplus/pdfjs/1.0.0/web/viewer.html"
        assert _get(app, url, status=404).status_code == 404

    def test_not_on_ckan_host(self, app):
        path = "/webpageviewplus/pdfjs/{}/web/viewer.html".format(pdf.PDFJS_VERSION)
        assert app.get(path, status=404).status_code == 404

    def test_scripting_off_in_options(self):
        with open(os.path.join(pdf.PDFJS_DIR, "wvp-viewer.js")) as f:
            script = f.read()
        assert 'options.set("enableScripting", false)' in script
        assert not os.path.exists(os.path.join(pdf.PDFJS_DIR, "build", "pdf.sandbox.mjs"))


@pytest.mark.usefixtures("local_storage")
class TestView:
    def test_rendered_with_viewer_frame(self, app):
        dataset = factories.Dataset()
        resource = _upload(dataset)
        view = _view(resource)
        body = app.get("/dataset/{}/resource/{}?view_id={}".format(
            dataset["name"], resource["id"], view["id"])).body
        assert 'sandbox="allow-scripts allow-same-origin' in body
        src = CONTENT + "/webpageviewplus/pdfjs/{}/web/viewer.html?file=".format(pdf.PDFJS_VERSION)
        assert src in body
        assert 'data-reload-url="/webpageviewplus/pdf-view/{}"'.format(view["id"]) in body

    def test_open_viewer_redirects_to_fresh_url(self, app):
        resource = _upload(factories.Dataset())
        view = _view(resource)
        response = app.get("/webpageviewplus/pdf-view/{}".format(view["id"]), follow_redirects=False)
        assert response.status_code == 302
        location = response.headers["Location"]
        assert location.startswith(CONTENT + "/webpageviewplus/pdfjs/")
        file = unquote(parse_qs(urlsplit(location).query)["file"][0])
        assert file == "/webpageviewplus/pdf/{}/document.pdf".format(view["id"])

    def test_open_viewer_private_needs_access(self, app):
        resource = _upload(_private_dataset())
        view = _view(resource)
        assert app.get("/webpageviewplus/pdf-view/{}".format(view["id"]), status=404).status_code == 404

    def test_open_viewer_not_on_content_host(self, app):
        resource = _upload(factories.Dataset())
        view = _view(resource)
        url = CONTENT + "/webpageviewplus/pdf-view/{}".format(view["id"])
        assert _get(app, url, status=404).status_code == 404

    def test_height_modes(self):
        resource = _upload(factories.Dataset())
        with pytest.raises(helpers.logic.ValidationError):
            _view(resource, height_mode="auto")
        with pytest.raises(helpers.logic.ValidationError):
            _view(resource, height_mode="viewport", height_value="150")
        assert _view(resource, height_mode="viewport", height_value="70")


def test_not_offered_without_content_origin(monkeypatch):
    monkeypatch.setattr(plugin.settings, "isolating_content_origin", lambda: None)
    resource = {"format": "PDF", "url": "https://a.org/b.pdf"}
    assert not plugin.PdfViewPlusPlugin().can_view({"resource": resource})


@pytest.mark.usefixtures("local_storage")
def test_offered_for_pdfs():
    view_plugin = plugin.PdfViewPlusPlugin()
    assert view_plugin.can_view({"resource": {"format": "PDF", "url": "x"}})
    assert view_plugin.can_view({"resource": {"format": "", "url": "https://a.org/b.PDF"}})
    assert not view_plugin.can_view({"resource": {"format": "CSV", "url": "https://a.org/b.csv"}})


def test_not_offered_for_unreadable_uploads(monkeypatch):
    monkeypatch.setattr(content, "_readable_uploader", lambda resource: None)
    upload = {"format": "PDF", "url": "a.pdf", "url_type": "upload", "id": "x"}
    assert not plugin.PdfViewPlusPlugin().can_view({"resource": upload})


def test_needs_main_plugin(monkeypatch):
    # Patches plugin.py's own name only: patching ckan.plugins.plugin_loaded
    # would also fool with_plugins' teardown, which then unloads nothing
    # and leaves this module's plugins loaded for later tests (CKAN 2.10).
    monkeypatch.setattr(plugin, "plugins", SimpleNamespace(
        plugin_loaded=lambda name: False, SingletonPlugin=plugin.plugins.SingletonPlugin))
    with pytest.raises(CkanConfigurationException):
        plugin.PdfViewPlusPlugin().configure({})
