import io
from urllib.parse import urlsplit

import pytest
from werkzeug.datastructures import FileStorage

from ckan.exceptions import CkanConfigurationException
from ckan.plugins.toolkit import url_for
from ckan.tests import factories, helpers

from ckanext.webpageviewplus import content, settings

CONTENT = "http://127.0.0.1"
SECRET = "k" * 40
HTML = b"<!doctype html><p>schematic qsv-schematic-source</p>"

pytestmark = [
    pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus"),
    pytest.mark.ckan_config("ckanext.webpageviewplus.content_origin", CONTENT),
    pytest.mark.ckan_config("ckanext.webpageviewplus.signing_secret", SECRET),
    pytest.mark.usefixtures("non_clean_db", "with_plugins"),
]


@pytest.fixture
def storage(tmp_path, ckan_config, monkeypatch):
    monkeypatch.setitem(ckan_config, "ckan.storage_path", str(tmp_path))


def _upload(dataset, body=HTML, filename="s.html", fmt="HTML"):
    return factories.Resource(
        package_id=dataset["id"],
        url=filename,
        url_type="upload",
        format=fmt,
        upload=FileStorage(io.BytesIO(body), filename=filename),
    )


def _private_dataset():
    org = factories.Organization()
    return factories.Dataset(owner_org=org["id"], private=True)


def _get(app, url, **kw):
    parts = urlsplit(url)
    path = parts.path + ("?" + parts.query if parts.query else "")
    return app.get(path, base_url="{}://{}".format(parts.scheme, parts.netloc), **kw)


def _content_url(resource, private=False):
    return content.content_url(resource, private=private)


@pytest.mark.usefixtures("storage")
class TestContentRoute:
    def test_public_upload_served_with_headers(self, app, ckan_config):
        resource = _upload(factories.Dataset())
        response = _get(app, _content_url(resource))
        assert response.status_code == 200
        assert response.body == HTML.decode()
        assert response.headers["Content-Type"].startswith("text/html")
        assert response.headers["Content-Security-Policy"].startswith("frame-ancestors ")
        site_host = urlsplit(ckan_config["ckan.site_url"]).hostname
        assert "{}:*".format(site_host) in response.headers["Content-Security-Policy"]
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Cache-Control"] == "public, no-cache"
        assert "Set-Cookie" not in response.headers
        assert "sandbox" not in response.headers["Content-Security-Policy"]

    def test_not_found_is_not_cached(self, app):
        response = app.get(
            "/webpageviewplus/content/nope/x.html", base_url=CONTENT, status=404
        )
        assert response.headers["Cache-Control"] == "no-store"

    def test_not_served_on_ckan_host(self, app):
        resource = _upload(factories.Dataset())
        path = urlsplit(_content_url(resource)).path
        assert app.get(path, status=404).status_code == 404

    def test_content_host_serves_nothing_else(self, app):
        response = app.get("/dataset/", base_url=CONTENT, status=404)
        assert response.status_code == 404
        assert "Set-Cookie" not in response.headers

    def test_non_html_upload_refused(self, app):
        resource = _upload(factories.Dataset(), b"a,b\n", "d.csv", "CSV")
        assert _get(app, _content_url(resource), status=404).status_code == 404

    def test_deleted_resource_refused(self, app):
        resource = _upload(factories.Dataset())
        url = _content_url(resource)
        helpers.call_action("resource_delete", id=resource["id"])
        assert _get(app, url, status=404).status_code == 404

    def test_private_needs_signature(self, app):
        resource = _upload(_private_dataset())
        assert _get(app, _content_url(resource), status=404).status_code == 404
        response = _get(app, _content_url(resource, private=True))
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, max-age=300"

    @pytest.mark.parametrize("sig", ["\u00e9", "zz", "A" * 64, ""])
    def test_malformed_signature_is_a_404(self, app, sig):
        resource = _upload(_private_dataset())
        url = _content_url(resource) + "?w={}&s={}".format(content._window(), sig)
        assert _get(app, url, status=404).status_code == 404

    def test_encoded_signature_param_is_still_private(self, app):
        resource = _upload(_private_dataset())
        url = _content_url(resource, private=True).replace("&s=", "&%73=")
        response = _get(app, url)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, max-age=300"

    def test_draft_dataset_gets_signed_url(self, app):
        dataset = factories.Dataset(state="draft")
        resource = _upload(dataset)
        assert not content.is_public(dataset["id"])
        url = _content_url(resource, private=True)
        assert _get(app, url).status_code == 200
        assert _get(app, _content_url(resource), status=404).status_code == 404

    def test_tampered_signature_refused(self, app):
        resource = _upload(_private_dataset())
        url = _content_url(resource, private=True)
        bad = url[:-4] + ("0000" if not url.endswith("0000") else "1111")
        assert _get(app, bad, status=404).status_code == 404

    def test_signature_for_another_resource_refused(self, app):
        dataset = _private_dataset()
        a, b = _upload(dataset), _upload(dataset, filename="t.html")
        query = urlsplit(_content_url(a, private=True)).query
        url = _content_url(b) + "?" + query
        assert _get(app, url, status=404).status_code == 404

    def test_previous_window_accepted_older_refused(self, app, monkeypatch):
        resource = _upload(_private_dataset())
        url = _content_url(resource, private=True)
        window = content._window()
        monkeypatch.setattr(content, "_window", lambda now=None: window + 1)
        assert _get(app, url).status_code == 200
        monkeypatch.setattr(content, "_window", lambda now=None: window + 2)
        assert _get(app, url, status=404).status_code == 404

    def test_dataset_made_private_stops_unsigned_url(self, app):
        org = factories.Organization()
        dataset = factories.Dataset(owner_org=org["id"])
        resource = _upload(dataset)
        url = _content_url(resource)
        assert _get(app, url).status_code == 200
        helpers.call_action("package_patch", id=dataset["id"], private=True)
        assert _get(app, url, status=404).status_code == 404


@pytest.mark.usefixtures("storage")
class TestDownloadRedirect:
    def test_html_download_redirects_to_content_origin(self, app):
        dataset = factories.Dataset()
        resource = _upload(dataset)
        url = url_for(
            "dataset_resource.download",
            id=dataset["id"],
            resource_id=resource["id"],
            filename="s.html",
        )
        response = app.get(url, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["Location"].startswith(CONTENT + "/webpageviewplus/content/")
        assert "?w=" not in response.headers["Location"]

    def test_private_download_redirect_is_signed(self, app):
        sysadmin = factories.SysadminWithToken()
        dataset = _private_dataset()
        resource = _upload(dataset)
        url = url_for(
            "dataset_resource.download",
            id=dataset["id"],
            resource_id=resource["id"],
            filename="s.html",
        )
        response = app.get(
            url, headers={"Authorization": sysadmin["token"]}, follow_redirects=False
        )
        assert response.status_code == 302
        assert "?w=" in response.headers["Location"]

    def test_private_download_still_needs_access(self, app):
        dataset = _private_dataset()
        resource = _upload(dataset)
        url = url_for(
            "dataset_resource.download",
            id=dataset["id"],
            resource_id=resource["id"],
            filename="s.html",
        )
        response = app.get(url, follow_redirects=False, status=403)
        assert "Location" not in response.headers


@pytest.mark.usefixtures("storage")
@pytest.mark.ckan_config("ckan.views.default_views", "")
def test_view_frames_content_url_with_data_schematic(app):
    dataset = factories.Dataset()
    resource = _upload(dataset)
    helpers.call_action(
        "resource_view_create",
        context={"user": factories.Sysadmin()["name"], "ignore_auth": True},
        resource_id=resource["id"],
        view_type="webpage_view_plus",
        title="Schematic",
        preset="data-schematic",
    )
    body = app.get(
        url_for("dataset_resource.read", id=dataset["name"], resource_id=resource["id"])
    ).body
    assert 'src="{}/webpageviewplus/content/{}/s.html"'.format(CONTENT, resource["id"]) in body
    assert "allow-same-origin" in body
    assert 'data-preset="data-schematic"' in body


@pytest.mark.parametrize(
    "origin,secret",
    [
        ("https://test.ckan.net", SECRET),  # CKAN's own host
        ("https://files.test.ckan.net", SECRET),  # a subdomain
        ("https://ckan.net", SECRET),  # a parent
        ("http://usercontent.example.org", SECRET),  # http, not loopback
        ("https://usercontent.example.org/path", SECRET),  # not an origin
        ("https://usercontent.example.org#x", SECRET),  # a fragment
        ("https://files.ckan.net", SECRET),  # a sibling: same site ckan.net
        ("https://usercontent.example.org", "short"),  # weak secret
    ],
)
def test_unsafe_content_origin_stops_startup(origin, secret, monkeypatch):
    monkeypatch.setattr(settings, "ckan_hosts", lambda: ["test.ckan.net"])
    monkeypatch.setattr(settings, "content_origin", lambda: origin)
    monkeypatch.setattr(settings, "signing_secret", lambda: secret)
    with pytest.raises(CkanConfigurationException):
        settings.check()


def test_good_content_origin_accepted(monkeypatch):
    monkeypatch.setattr(settings, "content_origin", lambda: "https://usercontent.example.org")
    settings.check()


@pytest.mark.usefixtures("storage")
def test_other_uploaders_stay_on_ckan_origin(app, monkeypatch):
    # e.g. ckanext-cloudstorage: not a filesystem ResourceUpload.
    monkeypatch.setattr(content, "get_resource_uploader", lambda resource: object())
    resource = _upload(factories.Dataset())
    assert not content.serves(resource)
    assert _get(app, _content_url(resource), status=404).status_code == 404


@pytest.mark.usefixtures("storage")
def test_private_view_src_is_signed_for_permitted_viewer(app):
    sysadmin = factories.SysadminWithToken()
    dataset = _private_dataset()
    resource = _upload(dataset)
    helpers.call_action(
        "resource_view_create",
        context={"user": sysadmin["name"], "ignore_auth": True},
        resource_id=resource["id"],
        view_type="webpage_view_plus",
        title="Private schematic",
    )
    body = app.get(
        url_for("dataset_resource.read", id=dataset["name"], resource_id=resource["id"]),
        headers={"Authorization": sysadmin["token"]},
    ).body
    assert "/webpageviewplus/content/{}/s.html?w=".format(resource["id"]) in body
