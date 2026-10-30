"""What uploaded pages contact: CSP report-only on the content origin."""
import io
import json
from urllib.parse import urlsplit

import pytest
from werkzeug.datastructures import FileStorage

from ckan.plugins.toolkit import url_for
from ckan.tests import factories, helpers

from ckanext.webpageviewplus import connections

CONTENT = "https://usercontent.example.net"
SECRET = "s" * 32

pytestmark = [
    pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus"),
    pytest.mark.ckan_config("ckan.views.default_views", ""),
    pytest.mark.ckan_config("ckanext.webpageviewplus.content_origin", CONTENT),
    pytest.mark.ckan_config("ckanext.webpageviewplus.signing_secret", SECRET),
    pytest.mark.usefixtures("counts_table", "with_plugins"),
]


@pytest.fixture
def storage(local_storage):
    return local_storage


def _upload():
    dataset = factories.Dataset()
    return dataset, factories.Resource(
        package_id=dataset["id"], url="s.html", url_type="upload", format="HTML",
        upload=FileStorage(io.BytesIO(b"<!doctype html><p>hi"), filename="s.html"))


def _post(app, url, body, content_type="application/reports+json", **kw):
    parts = urlsplit(url)
    return app.post(parts.path, data=body, base_url="{}://{}".format(parts.scheme, parts.netloc),
                    headers={"Content-Type": content_type}, **kw)


def _reports(*urls):
    return json.dumps([{"type": "csp-violation", "body": {"blockedURL": u}} for u in urls])


def _hosts(resource):
    return [c["host"] for c in helpers.call_action(
        "webpageviewplus_connections_show", id=resource["id"])]


@pytest.mark.parametrize("body,hosts", [
    (_reports("https://tiles.example.com/1/2/3.png", "https://cdn.example.org/x.js",
              "https://tiles.example.com/4.png"), ["tiles.example.com", "cdn.example.org"]),
    (json.dumps({"csp-report": {"blocked-uri": "wss://live.example.com:8443/s"}}),
     ["live.example.com"]),
    (_reports("inline", "eval", "data", "https://usercontent.example.net/a.js"), []),
    (_reports("javascript:alert(1)", "https://%65vil.example/", "https://a@b.example/"), []),
    (b"not json", []),
    (_reports("https://[", "https://cdn.example.org/x.js", "http://[::1"), ["cdn.example.org"]),
    (_reports(*["https://h{}.example.com/".format(i) for i in range(50)]),
     ["h{}.example.com".format(i) for i in range(connections.MAX_HOSTS)]),
])
def test_hosts_in(body, hosts):
    if isinstance(body, str):
        body = body.encode()
    assert connections.hosts_in(body, "usercontent.example.net") == hosts


@pytest.mark.usefixtures("storage")
@pytest.mark.ckan_config("ckanext.webpageviewplus.connection_reports", "true")
class TestReports:
    def test_served_upload_reports_only(self, app):
        _, resource = _upload()
        url = "{}/webpageviewplus/content/{}/s.html".format(CONTENT, resource["id"])
        parts = urlsplit(url)
        response = app.get(parts.path, base_url=CONTENT)
        policy = response.headers["Content-Security-Policy-Report-Only"]
        assert policy.startswith("default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:;")
        assert "report-uri /webpageviewplus/report/{};".format(resource["id"]) in policy
        assert policy.endswith("report-to webpageviewplus")
        assert response.headers["Reporting-Endpoints"] == (
            'webpageviewplus="{}/webpageviewplus/report/{}"'.format(CONTENT, resource["id"]))
        # The enforced policy is unchanged.
        assert response.headers["Content-Security-Policy"].startswith("frame-ancestors")

    def test_reports_recorded_per_resource(self, app):
        _, resource = _upload()
        report = "{}/webpageviewplus/report/{}".format(CONTENT, resource["id"])
        assert _post(app, report, _reports("https://cdn.example.org/x.js")).status_code == 204
        assert _post(app, report, json.dumps({"csp-report": {
            "blocked-uri": "https://tiles.example.com/1.png"}}),
            content_type="application/csp-report").status_code == 204
        _post(app, report, _reports("https://cdn.example.org/y.js"))
        rows = helpers.call_action("webpageviewplus_connections_show", id=resource["id"])
        assert [(r["host"], r["reports"]) for r in rows] == [
            ("cdn.example.org", 2), ("tiles.example.com", 1)]

    def test_only_on_the_content_host(self, app):
        _, resource = _upload()
        path = "/webpageviewplus/report/{}".format(resource["id"])
        assert app.post(path, data=_reports("https://cdn.example.org/x.js"), status=404,
                        headers={"Content-Type": "application/reports+json"}).status_code == 404
        assert _hosts(resource) == []

    def test_malformed_report_is_a_quiet_204(self, app):
        """Same answer whether or not the resource exists, and the valid
        entries still count."""
        _, resource = _upload()
        body = _reports("https://[", "https://cdn.example.org/x.js")
        for rid in (resource["id"], "nope"):
            report = "{}/webpageviewplus/report/{}".format(CONTENT, rid)
            assert _post(app, report, body).status_code == 204
        assert _hosts(resource) == ["cdn.example.org"]

    def test_purge_keeps_recent_hosts(self, app):
        import datetime
        from ckan import model

        _, resource = _upload()
        old = datetime.date.today() - datetime.timedelta(days=400)
        with model.meta.engine.begin() as connection:
            connection.execute(connections.table.insert(), [
                {"resource_id": resource["id"], "host": "stale.example", "reports": 3,
                 "last_seen": old},
                {"resource_id": resource["id"], "host": "refreshed.example", "reports": 1,
                 "last_seen": old},
            ])
        connections.record(resource["id"], ["refreshed.example", "recent.example"])
        assert connections.purge(365) >= 1
        assert sorted(_hosts(resource)) == ["recent.example", "refreshed.example"]

    def test_unknown_resource_and_oversized_body(self, app):
        report = "{}/webpageviewplus/report/nope".format(CONTENT)
        assert _post(app, report, _reports("https://cdn.example.org/x.js")).status_code == 204
        _, resource = _upload()
        big = _reports("https://cdn.example.org/x.js") + " " * connections.MAX_BODY
        report = "{}/webpageviewplus/report/{}".format(CONTENT, resource["id"])
        assert _post(app, report, big).status_code == 204
        assert _hosts(resource) == []

    @pytest.mark.ckan_config("ckanext.webpageviewplus.show_connections", "true")
    def test_disclosure(self, app):
        dataset, resource = _upload()
        helpers.call_action(
            "resource_view_create", context={"user": factories.Sysadmin()["name"]},
            resource_id=resource["id"], view_type="webpage_view_plus", title="S",
            preset="data-schematic")
        page = url_for("dataset_resource.read", id=dataset["name"], resource_id=resource["id"])
        assert "This page connects to" not in app.get(page).body
        connections.record(resource["id"], ["tiles.example.com"])
        body = app.get(page).body
        assert "This page connects to" in body and "<li>tiles.example.com</li>" in body

    def test_disclosure_off_by_default(self, app):
        dataset, resource = _upload()
        helpers.call_action(
            "resource_view_create", context={"user": factories.Sysadmin()["name"]},
            resource_id=resource["id"], view_type="webpage_view_plus", title="S")
        connections.record(resource["id"], ["tiles.example.com"])
        page = url_for("dataset_resource.read", id=dataset["name"], resource_id=resource["id"])
        assert "This page connects to" not in app.get(page).body


@pytest.mark.usefixtures("storage")
def test_off_by_default(app):
    _, resource = _upload()
    response = app.get("/webpageviewplus/content/{}/s.html".format(resource["id"]),
                       base_url=CONTENT)
    assert "Content-Security-Policy-Report-Only" not in response.headers
    report = "{}/webpageviewplus/report/{}".format(CONTENT, resource["id"])
    assert _post(app, report, _reports("https://cdn.example.org/x.js")).status_code == 204
    assert _hosts(resource) == []


def test_connections_need_resource_update():
    from ckan import model
    from ckan.plugins.toolkit import NotAuthorized

    org = factories.Organization()
    resource = factories.Resource(package_id=factories.Dataset(owner_org=org["id"])["id"])
    for user in (factories.User()["name"], ""):
        with pytest.raises(NotAuthorized):
            helpers.call_auth("webpageviewplus_connections_show",
                              {"user": user, "model": model}, id=resource["id"])
