import os
import time

import pytest
import requests
import responses

from ckan.plugins import toolkit
from ckan.plugins.toolkit import url_for
from ckan.tests import factories, helpers

from ckanext.webpageviewplus import frameability as fa

CKAN = ("https", ["ckan.example.org"], 443)


@pytest.mark.parametrize(
    "xfo,csp,expected",
    [
        (None, None, fa.FRAMEABLE),
        ("DENY", None, fa.BLOCKED),
        ("SAMEORIGIN", None, fa.BLOCKED),
        ("ALLOW-FROM https://ckan.example.org", None, fa.FRAMEABLE),  # ignored by browsers
        (None, "frame-ancestors 'none'", fa.BLOCKED),
        (None, "frame-ancestors 'self'", fa.BLOCKED),
        (None, "frame-ancestors *", fa.FRAMEABLE),
        (None, "frame-ancestors https:", fa.FRAMEABLE),
        (None, "frame-ancestors https://ckan.example.org", fa.FRAMEABLE),
        (None, "frame-ancestors https://*.example.org", fa.FRAMEABLE),
        (None, "frame-ancestors ckan.example.org", fa.FRAMEABLE),
        (None, "frame-ancestors https://other.example.net", fa.BLOCKED),
        (None, "frame-ancestors https://ckan.example.org:8443", fa.BLOCKED),  # wrong port
        (None, "frame-ancestors https://ckan.example.org:443", fa.FRAMEABLE),
        (None, "frame-ancestors http://ckan.example.org", fa.FRAMEABLE),  # upgrade to https
        (None, "frame-ancestors *; frame-ancestors 'none'", fa.FRAMEABLE),  # first one wins
        (None, "default-src 'self'", fa.FRAMEABLE),  # no frame-ancestors
        ("DENY", "frame-ancestors *", fa.FRAMEABLE),  # frame-ancestors wins
        (None, "frame-ancestors *, frame-ancestors 'none'", fa.BLOCKED),  # every policy applies
        ("deny, sameorigin", None, fa.BLOCKED),
    ],
)
def test_verdict(xfo, csp, expected):
    headers = {"X-Frame-Options": xfo} if xfo else {}
    assert fa.verdict(headers, [csp] if csp else [], CKAN)[0] == expected


def test_ckan_on_a_nondefault_port():
    ckan = ("https", ["ckan.example.org"], 8443)
    assert fa.verdict({}, ["frame-ancestors https://ckan.example.org:9000"], ckan)[0] == fa.BLOCKED
    assert fa.verdict({}, ["frame-ancestors https://ckan.example.org:8443"], ckan)[0] == fa.FRAMEABLE
    assert fa.verdict({}, ["frame-ancestors https://ckan.example.org"], ckan)[0] == fa.BLOCKED
    assert fa.verdict({}, ["frame-ancestors https://ckan.example.org:*"], ckan)[0] == fa.FRAMEABLE


def test_schemeless_source_takes_the_pages_scheme():
    http_ckan = ("http", ["ckan.example.org"], 80)
    csp = ["frame-ancestors ckan.example.org"]
    assert fa.verdict({}, csp, http_ckan, page_scheme="https")[0] == fa.BLOCKED
    assert fa.verdict({}, csp, http_ckan, page_scheme="http")[0] == fa.FRAMEABLE


def test_identity_is_site_url_only():
    assert fa.ckan_identity("https://ckan.example.org") == ("https", ["ckan.example.org"], 443)
    assert fa.ckan_identity("http://localhost:5001") == ("http", ["localhost"], 5001)


@responses.activate
def test_malformed_redirect_is_unknown(dns):
    dns["pages.example.com"] = ["93.184.216.34"]
    responses.get("https://pages.example.com/a", status=302, headers={"Location": "http://["})
    assert fa.check("https://pages.example.com/a", CKAN) == (fa.UNKNOWN, "")


@pytest.fixture
def dns(monkeypatch):
    table = {}
    monkeypatch.setattr(fa, "_resolve", lambda host, port: table.get(host, []))
    fa._cache.clear()
    return table


def test_form_check_has_a_deadline(monkeypatch):
    # Without one, a server dripping its headers holds the worker for as
    # long as it likes: the per-read timeout never fires.
    seen = []

    def probe(url, ckan, deadline=None):
        seen.append(deadline)
        return fa.Probe(fa.UNKNOWN)

    monkeypatch.setattr(fa, "probe", probe)
    fa._cache.clear()
    before = time.monotonic()
    fa.check("https://pages.example.com/slow", CKAN)
    assert seen and seen[0] is not None
    assert before < seen[0] <= time.monotonic() + fa.TIMEOUT


@pytest.mark.parametrize(
    "addresses,public",
    [
        (["93.184.216.34"], True),
        (["10.0.0.5"], False),
        (["127.0.0.1"], False),
        (["169.254.169.254"], False),  # cloud metadata
        (["93.184.216.34", "192.168.1.1"], False),  # any private address fails
        (["::1"], False),
        ([], False),
    ],
)
def test_public_guard(dns, addresses, public):
    dns["pages.example.com"] = addresses
    assert fa._public("https://pages.example.com/a.html") is public


@responses.activate
def test_private_host_never_fetched(dns):
    dns["internal.example.com"] = ["10.0.0.5"]
    assert fa.check("https://internal.example.com/", CKAN) == (fa.UNKNOWN, "")
    assert len(responses.calls) == 0


@responses.activate
def test_redirect_to_private_host_stops(dns):
    dns["pages.example.com"] = ["93.184.216.34"]
    dns["internal.example.com"] = ["10.0.0.5"]
    responses.get(
        "https://pages.example.com/a",
        status=302,
        headers={"Location": "https://internal.example.com/admin"},
    )
    assert fa.check("https://pages.example.com/a", CKAN)[0] == fa.UNKNOWN
    assert len(responses.calls) == 1


@responses.activate
def test_blocked_page(dns):
    dns["pages.example.com"] = ["93.184.216.34"]
    responses.get("https://pages.example.com/a", headers={"X-Frame-Options": "DENY"})
    assert fa.check("https://pages.example.com/a", CKAN) == (fa.BLOCKED, "X-Frame-Options: DENY")


@responses.activate
def test_network_errors_are_unknown(dns):
    dns["pages.example.com"] = ["93.184.216.34"]
    responses.get("https://pages.example.com/a", body=requests.ConnectionError("refused"))
    assert fa.check("https://pages.example.com/a", CKAN) == (fa.UNKNOWN, "")


@responses.activate
def test_too_many_redirects_is_unknown(dns):
    dns["pages.example.com"] = ["93.184.216.34"]
    responses.get(
        "https://pages.example.com/a", status=302, headers={"Location": "/a"}
    )
    assert fa.check("https://pages.example.com/a", CKAN)[0] == fa.UNKNOWN
    assert len(responses.calls) == fa.MAX_REDIRECTS + 1


@responses.activate
@pytest.mark.parametrize(
    "status,problem", [(200, ""), (404, "gone"), (410, "gone"), (500, "http-error"), (403, "http-error")]
)
def test_probe_reports_status(dns, status, problem):
    dns["pages.example.com"] = ["93.184.216.34"]
    responses.get("https://pages.example.com/a", status=status,
                  headers={"X-Frame-Options": "DENY"})
    result = fa.probe("https://pages.example.com/a", CKAN)
    assert (result.status, result.problem) == (status, problem)
    assert result.verdict == fa.BLOCKED


@responses.activate
def test_probe_private_and_unreachable(dns):
    dns["internal.example.com"] = ["10.0.0.5"]
    dns["pages.example.com"] = ["93.184.216.34"]
    responses.get("https://pages.example.com/a", body=requests.ConnectionError("refused"))
    assert fa.probe("https://internal.example.com/", CKAN).problem == "private"
    assert fa.probe("https://pages.example.com/a", CKAN).problem == "unreachable"


@responses.activate
def test_probe_deadline_passed_fetches_nothing(dns):
    dns["pages.example.com"] = ["93.184.216.34"]
    responses.get("https://pages.example.com/a")
    result = fa.probe("https://pages.example.com/a", CKAN, deadline=time.monotonic() - 1)
    assert (result.verdict, result.problem) == (fa.UNKNOWN, "unreachable")
    assert len(responses.calls) == 0


def test_probe_timeout_bounded_by_deadline(dns, monkeypatch):
    dns["pages.example.com"] = ["93.184.216.34"]
    seen = {}

    def get(url, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        raise requests.Timeout()

    monkeypatch.setattr(fa, "_http_get", get)
    fa.probe("https://pages.example.com/a", CKAN, deadline=time.monotonic() + 2)
    assert 0 < seen["timeout"] <= 2


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
class TestAction:
    def _call(self, user, **data):
        return helpers.call_action(
            "webpageviewplus_frameability",
            context={"user": user["name"] if user else "", "ignore_auth": False},
            **data,
        )

    def test_needs_permission_on_the_resource(self):
        # In an organization: CKAN lets any logged-in user edit unowned datasets.
        dataset = factories.Dataset(owner_org=factories.Organization()["id"])
        resource = factories.Resource(
            package_id=dataset["id"], url="https://pages.example.com/a.html"
        )
        with pytest.raises(toolkit.NotAuthorized):
            self._call(factories.User(), resource_id=resource["id"], url="https://x.example/")
        with pytest.raises(toolkit.NotAuthorized):
            self._call(None, resource_id=resource["id"], url="https://x.example/")

    def test_off_by_default(self):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        result = self._call(
            factories.Sysadmin(), resource_id=resource["id"], url="https://x.example/"
        )
        assert result == {"verdict": "skipped", "reason": "disabled"}

    @pytest.mark.ckan_config("ckanext.webpageviewplus.frameability_check", "true")
    def test_checks_when_enabled(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            fa, "check", lambda url, ckan: calls.append(url) or (fa.BLOCKED, "X-Frame-Options: DENY")
        )
        resource = factories.Resource(url="https://pages.example.com/a.html")
        result = self._call(
            factories.Sysadmin(), resource_id=resource["id"], url="https://x.example/"
        )
        assert result == {"verdict": "blocked", "reason": "X-Frame-Options: DENY"}
        assert calls == ["https://x.example/"]

    @pytest.mark.ckan_config("ckanext.webpageviewplus.frameability_check", "true")
    def test_ckan_pages_not_fetched(self, monkeypatch, ckan_config):
        monkeypatch.setattr(fa, "check", lambda url, ckan: pytest.fail("fetched"))
        resource = factories.Resource(url="https://pages.example.com/a.html")
        result = self._call(
            factories.Sysadmin(),
            resource_id=resource["id"],
            url=ckan_config["ckan.site_url"] + "/about",
        )
        assert result["verdict"] == "skipped"


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
@pytest.mark.parametrize("enabled", ["true", "false"])
def test_form_has_status_region_only_when_enabled(app, ckan_config, monkeypatch, enabled):
    monkeypatch.setitem(ckan_config, "ckanext.webpageviewplus.frameability_check", enabled)
    sysadmin = factories.SysadminWithToken()
    dataset = factories.Dataset()
    resource = factories.Resource(package_id=dataset["id"], url="https://pages.example.com/a.html")
    body = app.get(
        url_for("dataset_resource.edit_view", id=dataset["name"], resource_id=resource["id"],
                view_type="webpage_view_plus"),
        headers={"Authorization": sysadmin["token"]},
    ).body
    present = 'data-webpageviewplus-frameability' in body
    assert present is (enabled == "true")
    if present:
        assert 'role="status"' in body
        assert "/api/3/action/webpageviewplus_frameability" in body


@responses.activate
def test_unresolved_host_is_unreachable_not_private(dns):
    result = fa.probe("https://gone.example.com/", CKAN)
    assert result.problem == "unreachable"
    assert len(responses.calls) == 0
    # The form's check still gives one answer for every failure.
    assert fa.check("https://gone.example.com/", CKAN) == (fa.UNKNOWN, "")


def test_dns_bounded_by_deadline(monkeypatch):
    def slow(host, port):
        time.sleep(3)
        return ["93.184.216.34"]

    monkeypatch.setattr(fa, "_resolve", slow)
    start = time.monotonic()
    result = fa.probe("https://pages.example.com/a", CKAN, deadline=start + 0.5)
    assert result.problem == "unreachable"
    assert time.monotonic() - start < 2


@responses.activate
def test_probe_records_redirect_chain(dns):
    dns["pages.example.com"] = ["93.184.216.34"]
    responses.get("https://pages.example.com/a", status=301,
                  headers={"Location": "http://pages.example.com/b"})
    responses.get("http://pages.example.com/b")
    result = fa.probe("https://pages.example.com/a", CKAN)
    assert result.urls == ("https://pages.example.com/a", "http://pages.example.com/b")



@pytest.fixture
def local_server(monkeypatch):
    """Start a server on 127.0.0.1 whose GET is ``handle(request_handler)``,
    with the address checks passing it (as a public host would)."""
    import http.server
    import threading

    servers = []

    def start(handle):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                handle(self)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return "http://127.0.0.1:{}/x".format(server.server_port)

    monkeypatch.setattr(fa, "_address_kind", lambda url, deadline=None: "public")
    monkeypatch.setattr(fa, "_is_public_address", lambda address: True)
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    yield start
    for server in servers:
        server.shutdown()


def _drip(data):
    def handle(request):
        try:
            for byte in data:
                request.wfile.write(bytes([byte]))
                request.wfile.flush()
                time.sleep(0.2)
        except OSError:
            pass
    return handle


def test_dripped_body_cut_at_the_deadline(local_server):
    url = local_server(_drip(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n" + b"x" * 100))
    start = time.monotonic()
    assert fa.fetch_body(url, start + 1, 1000, "test") is None
    assert time.monotonic() - start < 2.5


def test_dripped_headers_cut_at_the_deadline(local_server):
    url = local_server(_drip(b"HTTP/1.1 200 OK\r\nX-Slow: " + b"y" * 100 + b"\r\n\r\n"))
    start = time.monotonic()
    assert fa.probe(url, CKAN, deadline=start + 1).problem == "unreachable"
    assert time.monotonic() - start < 2.5


def test_dripped_https_body_cut_at_the_deadline(monkeypatch):
    """TLS wraps (and detaches) the socket the connection opened, so the
    cut must still reach the live connection."""
    import http.server
    import ssl
    import threading

    # A self-signed certificate for 127.0.0.1 valid until 2126, for tests
    # only (not every CKAN image has the openssl command to make one).
    fixtures = os.path.join(os.path.dirname(__file__), "fixtures")
    cert = os.path.join(fixtures, "test_tls_cert.pem")
    key = os.path.join(fixtures, "test_tls_key.pem")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.2)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(fa, "_address_kind", lambda url, deadline=None: "public")
        monkeypatch.setattr(fa, "_is_public_address", lambda address: True)
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(cert))
        for name in ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
            monkeypatch.delenv(name, raising=False)
        url = "https://127.0.0.1:{}/x".format(server.server_port)
        start = time.monotonic()
        assert fa.fetch_body(url, start + 1, 1000, "test") is None
        assert time.monotonic() - start < 2.5
        # Not a TLS failure: with time enough, the same fetch succeeds.
        monkeypatch.setattr(Handler, "do_GET", lambda self: (
            self.send_response(200), self.send_header("Content-Length", "2"),
            self.end_headers(), self.wfile.write(b"ok")))
        assert fa.fetch_body(url, time.monotonic() + 5, 1000, "test").data == b"ok"
    finally:
        server.shutdown()


def test_connection_opened_after_the_deadline_refused():
    import socket

    fetch = fa._Fetch()
    fetch.cut()
    a, b = socket.socketpair()
    try:
        with pytest.raises(OSError):
            fetch.record(a)
        assert a.fileno() == -1  # closed
    finally:
        b.close()
        fetch.close()


def test_no_cookies_kept_between_fetches(local_server):
    seen = []

    def handle(request):
        seen.append(request.headers.get("Cookie"))
        request.send_response(200)
        request.send_header("Set-Cookie", "track=1; Path=/")
        request.send_header("Content-Length", "2")
        request.end_headers()
        request.wfile.write(b"ok")

    url = local_server(handle)
    for _ in range(2):
        assert fa.fetch_body(url, time.monotonic() + 5, 100, "test").data == b"ok"
    assert seen == [None, None]


def test_rebinding_refused_at_connect(monkeypatch):
    """A host that passed the check but resolves to a private address when
    CKAN connects (DNS rebinding) gets nothing sent to it."""
    import http.server
    import threading

    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"internal")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(fa, "_address_kind", lambda url, deadline=None: "public")
        for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
            monkeypatch.delenv(name, raising=False)
        url = "http://127.0.0.1:{}/x".format(server.server_port)
        assert fa.fetch_body(url, time.monotonic() + 5, 1000, "test") is None
        assert fa.probe(url, CKAN).problem == "unreachable"
        assert hits == []
    finally:
        server.shutdown()
