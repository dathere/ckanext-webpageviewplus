"""Screenshots of pages that refuse framing (screenshots.py)."""
import io
import json
import logging
import time

import pytest
import sqlalchemy as sa
from PIL import Image

from ckan.cli.cli import ckan
from ckan.exceptions import CkanConfigurationException
from ckan.plugins.toolkit import url_for
from ckan.tests import factories, helpers

from ckanext.webpageviewplus import frameability, health, plugin, screenshots, settings

PAGE = "https://pages.example.com/report.html"
OTHER = "https://pages.example.com/other.html"
ACCOUNT = "0123456789abcdef0123456789abcdef"
PLUGINS = "webpage_view_plus webpage_view_plus_thumbnails"


def _jpeg(size=(1350, 940), color=(30, 90, 200)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


def _image(answer_body):
    return screenshots.Answer(200, {"content-type": "image/jpeg"}, answer_body)


class FakeRedis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, seconds, value):
        self.data[key] = str(value).encode()


class Backend:
    """Stands in for screenshots._send: records each request and answers
    from ``answers``, a function of (method, url, kwargs)."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.answers(method, url, kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    def urls(self):
        return [c[1] for c in self.calls]

    def form_urls(self):
        return [c[2]["files"]["url"][1] for c in self.calls if "files" in c[2]]


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(screenshots, "_redis", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def page_still_blocked(monkeypatch):
    # A hosted capture re-checks the page first; tests answer for it.
    checked = []

    def check_view(view_id, url, deadline=None):
        checked.append(url)
        return frameability.Probe(frameability.BLOCKED, "x-frame-options", status=200)
    monkeypatch.setattr(health, "check_view", check_view)
    return checked


@pytest.fixture(autouse=True)
def no_save_checks(monkeypatch):
    # Saving a view with frameability_check on probes its page; tests store
    # the check themselves.
    monkeypatch.setattr(health, "after_save", lambda view: None)


def _view(resource_id, **fields):
    return helpers.call_action(
        "resource_view_create",
        context={"user": factories.Sysadmin()["name"], "ignore_auth": True},
        resource_id=resource_id, view_type="webpage_view_plus", title="Report", **fields)


def _blocked(view_id, url=PAGE):
    health.store(view_id, url, frameability.Probe(frameability.BLOCKED, "x-frame-options"))


def _setup(private=False, url=PAGE, **view_fields):
    org = factories.Organization()
    dataset = factories.Dataset(owner_org=org["id"], private=private)
    resource = factories.Resource(package_id=dataset["id"], url=url, format="HTML")
    view = _view(resource["id"], **view_fields)
    _blocked(view["id"], url)
    return dataset, resource, view


def _gotenberg(page=lambda: _image(_jpeg()), canary=403):
    def answers(method, url, kwargs):
        target = kwargs["files"]["url"][1]
        if target in screenshots.CANARIES:
            return screenshots.Answer(canary, {"content-type": "text/plain"}, b"")
        return page()
    return Backend(answers)


GOTENBERG = [
    pytest.mark.ckan_config("ckan.plugins", PLUGINS),
    pytest.mark.ckan_config("ckanext.webpageviewplus.frameability_check", "true"),
    pytest.mark.ckan_config("ckanext.webpageviewplus.screenshot_backend", "gotenberg"),
    pytest.mark.ckan_config("ckanext.webpageviewplus.gotenberg_url", "http://gotenberg:3000"),
    pytest.mark.usefixtures("non_clean_db", "with_plugins", "counts_table", "redis"),
]


def _marks(marks):
    def wrap(cls):
        for mark in reversed(marks):
            cls = mark(cls)
        return cls
    return wrap


# --- Settings -------------------------------------------------------------


@pytest.mark.parametrize("values, key", [
    ({"screenshot_backend": "pagespeed"}, "screenshot_backend"),
    ({"screenshot_backend": "gotenberg", "gotenberg_url": "http://g:3000",
      "frameability_check": "false"}, "screenshot_backend"),
    ({"screenshot_backend": "cloudflare", "cloudflare_api_token": "t"}, "cloudflare_account_id"),
    ({"screenshot_backend": "cloudflare", "cloudflare_account_id": ACCOUNT},
     "cloudflare_api_token"),
    ({"screenshot_backend": "gotenberg", "gotenberg_url": "http://user:pw@g:3000"},
     "gotenberg_url"),
    ({"screenshot_backend": "gotenberg", "gotenberg_url": "file:///tmp"}, "gotenberg_url"),
    ({"screenshot_seconds": "60"}, "screenshot_seconds"),
])
def test_settings_refused(monkeypatch, values, key):
    found = dict({"frameability_check": "true"}, **values)
    monkeypatch.setattr(settings, "_get", lambda name: found.get(name))
    with pytest.raises(CkanConfigurationException, match=key):
        settings._check_screenshots()


def test_settings_accepted(monkeypatch):
    found = {"frameability_check": "true", "screenshot_backend": "cloudflare",
             "cloudflare_account_id": ACCOUNT, "cloudflare_api_token": "t"}
    monkeypatch.setattr(settings, "_get", lambda name: found.get(name))
    settings._check_screenshots()
    found = {"screenshot_backend": "none"}
    settings._check_screenshots()


# --- Which views ----------------------------------------------------------


@_marks(GOTENBERG)
class TestTarget:
    def test_blocked_page_qualifies(self):
        _, _, view = _setup()
        assert screenshots._view_target(_model_view(view)) == PAGE

    @pytest.mark.parametrize("fields", [{"no_auto_thumbnail": True}])
    def test_opted_out(self, fields):
        _, _, view = _setup(**fields)
        assert screenshots._view_target(_model_view(view)) is None

    @pytest.mark.usefixtures("local_storage")
    def test_picked_thumbnail(self):
        from ckanext.webpageviewplus.tests.test_thumbnails import _image as uploaded_image
        dataset, resource, view = _setup()
        image = uploaded_image(dataset)
        view = helpers.call_action(
            "resource_view_update", context={"user": factories.Sysadmin()["name"],
                                             "ignore_auth": True},
            **dict(view, thumbnail_resource=image["id"]))
        assert screenshots._view_target(_model_view(view)) is None

    def test_frameable_page_doesnt(self):
        _, _, view = _setup()
        health.store(view["id"], PAGE, frameability.Probe(frameability.FRAMEABLE))
        assert screenshots._view_target(_model_view(view)) is None

    def test_check_for_another_url_doesnt_count(self):
        # The stored check is for PAGE; once the view frames OTHER, which
        # hasn't been checked, it doesn't qualify.
        _, _, view = _setup()
        view = _update(view, page_url=OTHER)
        assert screenshots._view_target(_model_view(view)) is None

    def test_private_dataset_with_gotenberg(self):
        _, _, view = _setup(private=True)
        assert screenshots._view_target(_model_view(view)) == PAGE


def _model_view(view):
    from ckan import model
    return model.ResourceView.get(view["id"])


def _update(view, **fields):
    return helpers.call_action(
        "resource_view_update", context={"user": factories.Sysadmin()["name"],
                                         "ignore_auth": True}, **dict(view, **fields))


# --- Gotenberg --------------------------------------------------------------


@_marks(GOTENBERG)
class TestGotenberg:
    def test_takes_and_stores(self, monkeypatch):
        backend = _gotenberg(page=lambda: _image(_jpeg((1350, 3000))))
        monkeypatch.setattr(screenshots, "_send", backend)
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "taken"
        # The canaries first, then the page, to gotenberg_url only.
        assert backend.form_urls() == list(screenshots.CANARIES) + [PAGE]
        assert set(backend.urls()) == {"http://gotenberg:3000/forms/chromium/screenshot/url"}
        assert all(not kw["guarded"] for _, _, kw in backend.calls)
        form = backend.calls[-1][2]["files"]
        assert form["clip"][1] == "true" and form["format"][1] == "jpeg"
        row = screenshots.stored(view["id"])
        assert row["source"] == "screenshot:" + PAGE and row["backend"] == "gotenberg"
        assert row["last_error"] == ""
        # A full-page answer is cut to the top, in the viewport's shape.
        assert Image.open(io.BytesIO(row["image"])).size == (1350, 940)
        assert max(Image.open(io.BytesIO(row["thumb"])).size) <= 320

    def test_no_recheck(self, monkeypatch, page_still_blocked):
        # Gotenberg fails 4xx and 5xx pages itself (409).
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "taken"
        assert page_still_blocked == []

    def test_canaries_never_read(self, monkeypatch):
        backend = _gotenberg()
        monkeypatch.setattr(screenshots, "_send", backend)
        assert screenshots.check_gotenberg()
        assert [kw["max_bytes"] for _, _, kw in backend.calls] == [0, 0]

    @pytest.mark.parametrize("canary", [400, 404, 200, 503])
    def test_refused_unless_it_refuses_private_addresses(self, monkeypatch, canary, events_log):
        backend = _gotenberg(canary=canary)
        monkeypatch.setattr(screenshots, "_send", backend)
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "unsafe_backend"
        assert PAGE not in backend.form_urls()
        assert screenshots.stored(view["id"])["last_error"] == "unsafe_backend"
        assert "screenshot_backend_unsafe" in [e["event"] for e in events_log()]

    def test_check_remembered_then_rechecked(self, monkeypatch, redis):
        backend = _gotenberg()
        monkeypatch.setattr(screenshots, "_send", backend)
        assert screenshots.gotenberg_safe()
        assert screenshots.gotenberg_safe()
        assert len(backend.calls) == 2  # checked once
        redis.data.clear()  # expired
        assert screenshots.gotenberg_safe()
        assert len(backend.calls) == 4

    def test_no_redis_never_passes(self, monkeypatch):
        backend = _gotenberg()
        monkeypatch.setattr(screenshots, "_send", backend)
        monkeypatch.setattr(screenshots, "_redis", lambda: (_ for _ in ()).throw(OSError()))
        assert not screenshots.gotenberg_safe()

    @pytest.mark.parametrize("status, kind", [(409, "page_failed"), (403, "page_failed"),
                                              (503, "page_failed"), (401, "backend_error"),
                                              (429, "rate_limited")])
    def test_failures(self, monkeypatch, status, kind):
        monkeypatch.setattr(screenshots, "_send", _gotenberg(
            page=lambda: screenshots.Answer(status, {"content-type": "text/plain"}, b"")))
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == kind

    def test_not_an_image(self, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg(
            page=lambda: screenshots.Answer(200, {"content-type": "image/jpeg"}, b"<svg/>")))
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "no_image"

    def test_failure_keeps_the_last_screenshot_of_the_same_page(self, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        _, _, view = _setup()
        screenshots.capture(view["id"])
        before = screenshots.stored(view["id"])
        monkeypatch.setattr(screenshots, "_send", _gotenberg(
            page=lambda: screenshots.Answer(409, {}, b"")))
        assert screenshots.capture(view["id"]) == "page_failed"
        after = screenshots.stored(view["id"])
        assert after["digest"] == before["digest"] and after["last_error"] == "page_failed"
        assert after["attempted_at"] > before["attempted_at"]

    def test_failure_for_another_page_drops_the_old_screenshot(self, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        _, _, view = _setup()
        screenshots.capture(view["id"])
        view = _update(view, page_url=OTHER)
        _blocked(view["id"], OTHER)
        monkeypatch.setattr(screenshots, "_send", _gotenberg(
            page=lambda: screenshots.Answer(409, {}, b"")))
        assert screenshots.capture(view["id"]) == "page_failed"
        row = screenshots.stored(view["id"])
        assert row["source"] == "screenshot:" + OTHER and row["image"] is None


# --- Hosted backends ---------------------------------------------------------


CLOUDFLARE = GOTENBERG[:2] + [
    pytest.mark.ckan_config("ckanext.webpageviewplus.screenshot_backend", "cloudflare"),
    pytest.mark.ckan_config("ckanext.webpageviewplus.cloudflare_account_id", ACCOUNT),
    pytest.mark.ckan_config("ckanext.webpageviewplus.cloudflare_api_token", "secret-token"),
    GOTENBERG[-1],
]


@_marks(CLOUDFLARE)
class TestCloudflare:
    def test_request(self, monkeypatch):
        backend = Backend(lambda *a: _image(_jpeg()))
        monkeypatch.setattr(screenshots, "_send", backend)
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "taken"
        (method, url, kwargs), = backend.calls
        assert method == "POST" and kwargs["guarded"]
        assert url == ("https://api.cloudflare.com/client/v4/accounts/" + ACCOUNT
                       + "/browser-run/screenshot")
        # The token in a header, never in the URL or the body.
        assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
        assert "secret-token" not in url and "secret-token" not in json.dumps(kwargs["json"])
        assert kwargs["json"]["url"] == PAGE
        assert kwargs["json"]["viewport"]["width"] == 1350
        assert kwargs["json"]["screenshotOptions"]["type"] == "jpeg"
        # Cloudflare's body refuses unknown keys: cacheTTL is a query parameter.
        assert kwargs["params"] == {"cacheTTL": "0"} and "cacheTTL" not in kwargs["json"]

    @pytest.mark.parametrize("probe, result", [
        (frameability.Probe(frameability.BLOCKED, "x-frame-options", "gone", 404), "page_failed"),
        (frameability.Probe(frameability.UNKNOWN, "", "http-error", 500), "page_failed"),
        (frameability.Probe(frameability.FRAMEABLE, status=200), "skipped"),
    ])
    def test_page_rechecked_first(self, monkeypatch, probe, result):
        # Cloudflare answers an error page with a screenshot of it: the
        # page is checked first, and nothing is sent unless it still
        # refuses framing without an error.
        backend = Backend(lambda *a: pytest.fail("sent a page that failed its check"))
        monkeypatch.setattr(screenshots, "_send", backend)
        monkeypatch.setattr(health, "check_view", lambda *a, **kw: probe)
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == result
        assert backend.calls == []

    def test_daily_limit_remembered_until_tomorrow(self, monkeypatch):
        backend = Backend(lambda *a: screenshots.Answer(
            429, {}, b'{"errors":[{"message":"Browser time limit exceeded for today"}]}'))
        monkeypatch.setattr(screenshots, "_send", backend)
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "rate_limited"
        assert screenshots.limited_until("cloudflare") == pytest.approx(
            screenshots._next_utc_day(), abs=2)
        # Not asked again until then.
        assert screenshots.capture(view["id"]) == "rate_limited"
        assert len(backend.calls) == 1

    @pytest.mark.parametrize("status, kind", [(400, "backend_error"), (401, "backend_error"),
                                              (500, "backend_error"), (422, "page_failed")])
    def test_failures(self, monkeypatch, status, kind):
        monkeypatch.setattr(screenshots, "_send",
                            Backend(lambda *a: screenshots.Answer(status, {}, b"{}")))
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == kind


MICROLINK = GOTENBERG[:2] + [
    pytest.mark.ckan_config("ckanext.webpageviewplus.screenshot_backend", "microlink"),
    GOTENBERG[-1],
]


@_marks(MICROLINK)
class TestHostedPrivacy:
    def test_private_dataset_never_sent_to_a_hosted_backend(self, monkeypatch):
        backend = Backend(lambda *a: pytest.fail("sent a private dataset's page"))
        monkeypatch.setattr(screenshots, "_send", backend)
        _, _, view = _setup(private=True)
        assert screenshots._view_target(_model_view(view)) is None
        assert screenshots.capture(view["id"]) == "skipped"
        assert backend.calls == []
        assert screenshots.stored(view["id"]) is None

    def test_not_public_to_anonymous_never_sent(self, monkeypatch):
        # Public by its flag, but not to an anonymous visitor (a draft, a
        # permission-label plugin): content.is_public decides.
        from ckanext.webpageviewplus import content
        backend = Backend(lambda *a: pytest.fail("sent a dataset anonymous can't see"))
        monkeypatch.setattr(screenshots, "_send", backend)
        monkeypatch.setattr(content, "is_public", lambda package_id: False)
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "skipped"
        assert backend.calls == []

    @pytest.mark.parametrize("body", [b"[]", b'"x"', b"null", b"{"])
    def test_odd_json(self, monkeypatch, body):
        monkeypatch.setattr(screenshots, "_send",
                            Backend(lambda *a: screenshots.Answer(200, {}, body)))
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "backend_error"


OFF = GOTENBERG[:2] + [
    pytest.mark.ckan_config("ckanext.webpageviewplus.screenshot_backend", "none"),
    GOTENBERG[-1],
]


@_marks(OFF)
class TestOff:
    def test_off(self):
        _, _, view = _setup()
        assert screenshots._view_target(_model_view(view)) is None


def _microlink_answer(shot_url, final=PAGE):
    return screenshots.Answer(200, {"content-type": "application/json"}, json.dumps({
        "status": "success",
        "data": {"url": final, "screenshot": {"url": shot_url, "type": "jpeg"}},
    }).encode())


IMAGE_URL = "https://iad.microlink.io/abc.jpeg"


def _microlink(shot_url=IMAGE_URL, final=PAGE, image=None):
    """A Backend answering Microlink's API and then its image URL."""
    def answer(method, url, kwargs):
        if url in ("https://api.microlink.io/", "https://pro.microlink.io/"):
            return _microlink_answer(shot_url, final)
        return image or screenshots.Answer(200, {"content-type": "image/jpeg"}, _jpeg())
    return Backend(answer)


@_marks(MICROLINK)
class TestMicrolink:
    def test_request_and_image(self, monkeypatch):
        backend = _microlink(final="https://www.example.org/report")
        monkeypatch.setattr(screenshots, "_send", backend)
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "taken"
        (_, url, kwargs), (_, image_url, image_kwargs) = backend.calls
        assert url == "https://api.microlink.io/" and kwargs["guarded"]
        assert kwargs["params"]["url"] == PAGE and "x-api-key" not in kwargs["headers"]
        assert image_url == IMAGE_URL and image_kwargs["guarded"]
        assert screenshots.stored(view["id"])["final_host"] == "www.example.org"

    @pytest.mark.ckan_config("ckanext.webpageviewplus.microlink_api_key", "mk")
    def test_key_in_a_header(self, monkeypatch):
        backend = _microlink()
        monkeypatch.setattr(screenshots, "_send", backend)
        _, _, view = _setup()
        screenshots.capture(view["id"])
        (_, url, kwargs), (_, _, image_kwargs) = backend.calls
        assert url == "https://pro.microlink.io/" and kwargs["headers"]["x-api-key"] == "mk"
        assert "mk" not in json.dumps(kwargs["params"])
        assert "x-api-key" not in image_kwargs.get("headers", {})

    @pytest.mark.parametrize("shot_url", [
        "https://evil.example.com/a.jpeg", "http://iad.microlink.io/a.jpeg",
        "https://microlink.io.evil.example.com/a.jpeg", "", None,
    ])
    def test_image_only_from_microlink(self, monkeypatch, shot_url):
        backend = _microlink(shot_url)
        monkeypatch.setattr(screenshots, "_send", backend)
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "no_image"
        assert len(backend.calls) == 1

    @pytest.mark.parametrize("status", [301, 302, 307, 404])
    def test_image_redirect_not_followed(self, monkeypatch, status):
        backend = _microlink(image=screenshots.Answer(
            status, {"location": "https://elsewhere.example.com/a.jpeg"}, b""))
        monkeypatch.setattr(screenshots, "_send", backend)
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "no_image"
        assert backend.urls() == ["https://api.microlink.io/", IMAGE_URL]

    def test_withheld_where_the_route_would_refuse(self, app, monkeypatch):
        # Public by its flag but not to anonymous visitors: serve() 404s,
        # so the card and the list must not offer the image (and the list
        # falls back to a logo or the format label).
        from ckanext.webpageviewplus import content
        monkeypatch.setattr(screenshots, "_send", _microlink())
        dataset, resource, view = _setup()
        assert screenshots.capture(view["id"]) == "taken"
        listed = {"id": view["id"], "view_type": screenshots.VIEW_TYPE, "config": view}
        with app.flask_app.test_request_context():
            assert screenshots.for_card(view["id"], view, resource, dataset)
            assert screenshots.for_list(listed, resource, dataset)
        monkeypatch.setattr(content, "is_public", lambda package_id: False)
        with app.flask_app.test_request_context():
            assert screenshots.for_card(view["id"], view, resource, dataset) is None
            assert screenshots.for_list(listed, resource, dataset) is None

    def test_page_failed(self, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", Backend(lambda *a: screenshots.Answer(
            400, {}, b'{"status":"fail","code":"EFAILED"}')))
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "page_failed"


# --- The real _send -----------------------------------------------------------


@pytest.mark.usefixtures("non_clean_db")
class TestSend:
    def test_redirects_not_followed(self):
        import responses
        with responses.RequestsMock() as mock:
            mock.post("http://gotenberg:3000/x", status=302,
                      headers={"Location": "http://elsewhere.example.com/"})
            answer = screenshots._send("POST", "http://gotenberg:3000/x", guarded=False,
                                       seconds=5, max_bytes=100)
        assert answer.status == 302

    def test_failure_names_no_url(self, caplog, monkeypatch):
        import responses
        # Tests that load CKAN's logging config disable existing loggers.
        monkeypatch.setattr(logging.getLogger("ckanext.webpageviewplus.screenshots"),
                            "disabled", False)
        with responses.RequestsMock() as mock:
            mock.post("http://gotenberg:3000/x?token=sekrit", body=OSError("boom sekrit"))
            with caplog.at_level(logging.DEBUG, logger="ckanext.webpageviewplus.screenshots"):
                with pytest.raises(screenshots.Failure) as raised:
                    screenshots._send("POST", "http://gotenberg:3000/x?token=sekrit",
                                      guarded=False, seconds=5, max_bytes=100)
        assert raised.value.kind == "backend_error"
        # Something was logged (so the absence below means something), and
        # it holds neither the URL nor the exception's text.
        assert "screenshot request failed" in caplog.text
        assert "sekrit" not in str(raised.value) and "sekrit" not in caplog.text

    def test_answer_capped(self):
        import responses
        with responses.RequestsMock() as mock:
            mock.post("http://gotenberg:3000/x", body=b"x" * 1000)
            with pytest.raises(screenshots.Failure, match="no_image"):
                screenshots._send("POST", "http://gotenberg:3000/x", guarded=False,
                                  seconds=5, max_bytes=100)

    def test_guarded_refuses_private_hosts(self, monkeypatch):
        monkeypatch.setattr(frameability, "_resolve", lambda host, port: ["10.0.0.5"])
        with pytest.raises(screenshots.Failure, match="backend_error"):
            screenshots._send("GET", "https://api.microlink.io/", guarded=True,
                              seconds=5, max_bytes=100)


# --- Showing ------------------------------------------------------------------


@_marks(GOTENBERG)
class TestShowing:
    def test_card_list_and_route(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, resource, view = _setup()
        screenshots.capture(view["id"])
        row = screenshots.stored(view["id"])

        card = url_for("webpageviewplus_screenshots.serve", view_id=view["id"],
                       v=row["digest"][:16])
        body = app.get(url_for("resource.read", id=dataset["name"],
                               resource_id=resource["id"])).body
        assert card.replace("&", "&amp;") in body
        assert 'alt="Screenshot of pages.example.com"' in body
        assert "Screenshot taken" in body

        thumb = url_for("webpageviewplus_screenshots.serve", view_id=view["id"],
                        size="thumb", v=row["digest"][:16])
        listing = app.get(url_for("dataset.read", id=dataset["name"])).body
        assert thumb.replace("&", "&amp;") in listing

        response = app.get(card)
        assert response.headers["Content-Type"] == "image/jpeg"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Cache-Control"] == "public, no-cache"
        assert response.data == row["image"]
        assert app.get(thumb).data == row["thumb"]

    def test_hidden_once_the_page_can_be_framed(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, resource, view = _setup()
        screenshots.capture(view["id"])
        health.store(view["id"], PAGE, frameability.Probe(frameability.FRAMEABLE))
        assert screenshots.stored(view["id"])["digest"]
        listing = app.get(url_for("dataset.read", id=dataset["name"])).body
        assert "webpageviewplus/screenshot/" not in listing
        app.get(url_for("webpageviewplus_screenshots.serve", view_id=view["id"]), status=404)

    def test_private_dataset_needs_access(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, resource, view = _setup(private=True)
        assert screenshots.capture(view["id"]) == "taken"
        src = url_for("webpageviewplus_screenshots.serve", view_id=view["id"])
        app.get(src, status=404)  # not 403: don't confirm it exists
        sysadmin = factories.SysadminWithToken()
        response = app.get(src, headers={"Authorization": sysadmin["token"]})
        assert response.headers["Cache-Control"].startswith("private")

    def test_without_the_thumbnails_plugin_no_card_screenshot(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, resource, view = _setup()
        screenshots.capture(view["id"])
        monkeypatch.setattr(plugin, "_thumbnails_loaded", lambda: False)
        body = app.get(url_for("resource.read", id=dataset["name"],
                               resource_id=resource["id"])).body
        assert "Screenshot of" not in body

    def test_screenshot_before_a_service_logo(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        url = "https://vimeo.com/76979871"
        dataset, resource, view = _setup(url=url)
        # Saving fills in the service's embed address: that's the page.
        _blocked(view["id"], view["page_url"])
        assert screenshots.capture(view["id"]) == "taken"
        listing = app.get(url_for("dataset.read", id=dataset["name"])).body
        assert "webpageviewplus/screenshot/" in listing
        assert "logos/vimeo.svg" not in listing


# --- The opt-out ------------------------------------------------------------------


def _save(view, **changes):
    return helpers.call_action(
        "resource_view_update",
        context={"user": factories.Sysadmin()["name"], "ignore_auth": True},
        **dict(view, **changes))


@_marks(GOTENBERG)
class TestOptOut:
    def test_opting_out_deletes_and_opting_back_in_retakes(self, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "taken"
        view = _save(view, no_auto_thumbnail="true")
        assert screenshots.stored(view["id"]) is None
        assert view["id"] not in dict(screenshots.due())
        _save(view, no_auto_thumbnail="")
        assert view["id"] in dict(screenshots.due())

    def test_opted_out_while_the_backend_worked(self, monkeypatch):
        _, _, view = _setup()
        backend = _gotenberg()

        def slow(*args, **kwargs):
            answer = backend(*args, **kwargs)
            if "files" in kwargs:
                _save(view, no_auto_thumbnail="true")
            return answer
        monkeypatch.setattr(screenshots, "_send", slow)
        assert screenshots.capture(view["id"]) == "skipped"
        assert screenshots.stored(view["id"]) is None

    def test_other_saves_keep_it(self, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        _, _, view = _setup()
        screenshots.capture(view["id"])
        _save(view, title="Renamed")
        assert screenshots.stored(view["id"])["digest"]

    def test_save_without_the_table(self, monkeypatch):
        missing = sa.Table("webpage_view_plus_no_such_table", sa.MetaData(),
                           sa.Column("view_id", sa.UnicodeText))
        monkeypatch.setattr(screenshots, "table", missing)
        _, _, view = _setup()
        assert _save(view, no_auto_thumbnail="true")["no_auto_thumbnail"]

    def test_form_says_it_stops_screenshots(self, app):
        dataset, resource, view = _setup()
        sysadmin = factories.SysadminWithToken()
        body = app.get(url_for(f"{dataset['type']}_resource.edit_view", id=dataset["name"],
                               resource_id=resource["id"], view_id=view["id"]),
                       headers={"Authorization": sysadmin["token"]}).body
        assert "It also stops screenshots" in body
        assert "help-no_auto_thumbnail help-no_auto_thumbnail-screenshot" in body


# --- The command -----------------------------------------------------------------


@_marks(GOTENBERG)
class TestCommand:
    def test_takes_due_views_and_skips_current_ones(self, cli, monkeypatch):
        from ckanext.webpageviewplus import cli as wvp_cli
        monkeypatch.setattr(wvp_cli, "_plugin_loaded", lambda name: True)
        backend = _gotenberg()
        monkeypatch.setattr(screenshots, "_send", backend)
        _, _, view = _setup()
        due = [v for v, _ in screenshots.due()]
        assert view["id"] in due

        dry = cli.invoke(ckan, ["webpageviewplus", "screenshots", "--dry-run"])
        assert dry.exit_code == 0 and view["id"] in dry.output
        assert screenshots.stored(view["id"]) is None

        result = cli.invoke(ckan, ["webpageviewplus", "screenshots"])
        assert result.exit_code == 0, result.output
        assert "{}\ttaken".format(view["id"]) in result.output
        assert view["id"] not in [v for v, _ in screenshots.due()]
        assert view["id"] in [v for v, _ in screenshots.due(refresh=True)]

    def test_waits_out_a_short_limit(self, cli, monkeypatch, redis):
        from ckanext.webpageviewplus import cli as wvp_cli
        monkeypatch.setattr(wvp_cli, "_plugin_loaded", lambda name: True)
        answers = [screenshots.Answer(429, {}, b""), _image(_jpeg())]
        monkeypatch.setattr(screenshots, "_send", _gotenberg(page=lambda: answers.pop(0)))
        slept = []
        # Waiting: the remembered limit runs out.
        monkeypatch.setattr(wvp_cli.time, "sleep",
                            lambda s: (slept.append(s), redis.data.pop(
                                screenshots._REDIS_PREFIX + "rate-limited:gotenberg", None)))
        _, _, view = _setup()
        result = cli.invoke(ckan, ["webpageviewplus", "screenshots"])
        assert "{}\ttaken".format(view["id"]) in result.output, result.output
        assert slept and slept[0] <= wvp_cli.SHORT_WAIT_SECONDS + 1

    def test_stops_at_a_long_limit(self, cli, monkeypatch, redis):
        from ckanext.webpageviewplus import cli as wvp_cli
        monkeypatch.setattr(wvp_cli, "_plugin_loaded", lambda name: True)
        monkeypatch.setattr(wvp_cli.time, "sleep", lambda s: pytest.fail("waited a day"))
        backend = _gotenberg()
        monkeypatch.setattr(screenshots, "_send", backend)
        screenshots._remember_limit("gotenberg", time.time() + 3600)
        _setup()
        _setup()
        result = cli.invoke(ckan, ["webpageviewplus", "screenshots"])
        assert result.output.count("\trate_limited\t") == 1
        assert backend.calls == []


@_marks(GOTENBERG)
class TestRobustness:
    def test_unexpected_error_is_one_views_failure(self, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg(page=lambda: ValueError("odd")))
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "backend_error"

    def test_doctor_findings(self, monkeypatch):
        from ckanext.webpageviewplus import cli as wvp_cli
        monkeypatch.setattr(wvp_cli, "_plugin_loaded", lambda name: True)
        monkeypatch.setattr(screenshots, "_send", _gotenberg(
            page=lambda: screenshots.Answer(409, {}, b"")))
        _, _, view = _setup()
        screenshots.capture(view["id"])
        found = dict((text.split(" ")[0], level) for level, text in wvp_cli._screenshot_findings())
        texts = " ".join(text for _, text in wvp_cli._screenshot_findings())
        assert "taken by gotenberg" in texts and "page_failed" in texts
        monkeypatch.setattr(wvp_cli, "_plugin_loaded", lambda name: False)
        (level, text), = wvp_cli._screenshot_findings()
        assert level == "warn" and "isn't loaded" in text
