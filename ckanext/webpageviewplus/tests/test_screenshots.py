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
from ckan.plugins import toolkit
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

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = str(value).encode()
        return True

    def exists(self, key):
        return int(key in self.data)

    def delete(self, key):
        return int(self.data.pop(key, None) is not None)


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

    def recheck(url):
        checked.append(url)
        return frameability.Probe(frameability.BLOCKED, "x-frame-options", status=200)
    monkeypatch.setattr(screenshots, "_recheck", recheck)
    return checked


@pytest.fixture(autouse=True)
def jobs(monkeypatch):
    # Saves with a backend set queue screenshot jobs: record them, never
    # push them into a real queue.
    queued = []

    def enqueue_job(fn, args=None, kwargs=None, title=None, queue="default", rq_kwargs=None):
        queued.append((fn, list(args or []), queue))
    monkeypatch.setattr(screenshots.toolkit, "enqueue_job", enqueue_job)
    return queued


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

    def test_error_pages_refused(self):
        # Gotenberg answers 409 (page_failed) when the page itself answers 4xx/5xx.
        assert dict(screenshots._gotenberg_form(PAGE))["failOnHttpStatusCodes"] == (
            None, "[499,599]")

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
                                              (503, "unreachable"), (401, "backend_error"),
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


def _snapshot(image=None, status=200, title="Report", success=True):
    """Cloudflare's /snapshot answer: the screenshot in base64, and the
    page's own status and title as its browser saw them."""
    import base64
    shot = base64.b64encode(_jpeg() if image is None else image).decode()
    return screenshots.Answer(200, {"content-type": "application/json"}, json.dumps({
        "success": success,
        "result": {"screenshot": shot, "markdown": "# Report"},
        "meta": {"status": status, "title": title},
    }).encode())


@_marks(CLOUDFLARE)
class TestCloudflare:
    def test_request(self, monkeypatch):
        backend = Backend(lambda *a: _snapshot())
        monkeypatch.setattr(screenshots, "_send", backend)
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "taken"
        (method, url, kwargs), = backend.calls
        assert method == "POST" and kwargs["guarded"]
        assert url == ("https://api.cloudflare.com/client/v4/accounts/" + ACCOUNT
                       + "/browser-run/snapshot")
        # The token in a header, never in the URL or the body.
        assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
        assert "secret-token" not in url and "secret-token" not in json.dumps(kwargs["json"])
        assert kwargs["json"]["url"] == PAGE
        assert kwargs["json"]["viewport"]["width"] == 1350
        assert kwargs["json"]["screenshotOptions"]["type"] == "jpeg"
        assert "screenshot" in kwargs["json"]["formats"]
        assert screenshots.stored(view["id"])["digest"]
        # Cloudflare's body refuses unknown keys: cacheTTL is a query parameter.
        assert kwargs["params"] == {"cacheTTL": "0"} and "cacheTTL" not in kwargs["json"]

    @pytest.mark.parametrize("probe, result", [
        (frameability.Probe(frameability.BLOCKED, "x-frame-options", "gone", 404), "page_failed"),
        (frameability.Probe(frameability.UNKNOWN, "", "http-error", 500), "page_failed"),
        (frameability.Probe(frameability.UNKNOWN, "", "unreachable"), "unreachable"),
        (frameability.Probe(frameability.FRAMEABLE, status=200), "skipped"),
    ])
    def test_page_rechecked_first(self, monkeypatch, probe, result):
        # Cloudflare answers an error page with a screenshot of it: the
        # page is checked first, and nothing is sent unless it still
        # refuses framing without an error.
        backend = Backend(lambda *a: pytest.fail("sent a page that failed its check"))
        monkeypatch.setattr(screenshots, "_send", backend)
        monkeypatch.setattr(screenshots, "_recheck", lambda url: probe)
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == result
        assert backend.calls == []
        # A gate only: the stored check is untouched, so the view stays
        # eligible (a page that failed waits out its back-off first).
        assert health.stored(view["id"], PAGE)["verdict"] == frameability.BLOCKED
        assert view["id"] in dict(screenshots.due(refresh=True))

    def test_view_deleted_while_the_backend_worked(self, monkeypatch):
        _, _, view = _setup()

        def answer(*args, **kwargs):
            helpers.call_action("resource_view_delete",
                                context={"user": factories.Sysadmin()["name"]}, id=view["id"])
            return _snapshot()
        monkeypatch.setattr(screenshots, "_send", Backend(answer))
        assert screenshots.capture(view["id"]) == "skipped"

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
                                              (500, "backend_error"), (422, "unreachable")])
    def test_failures(self, monkeypatch, status, kind):
        monkeypatch.setattr(screenshots, "_send",
                            Backend(lambda *a: screenshots.Answer(status, {}, b"{}")))
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == kind


    @pytest.mark.parametrize("answer, kind", [
        # A bot challenge shown to Cloudflare's browser, not to CKAN's re-check.
        (lambda: _snapshot(status=403), "page_failed"),
        (lambda: _snapshot(status=503), "page_failed"),
        (lambda: _snapshot(title="Just a moment..."), "page_failed"),
        (lambda: _snapshot(success=False), "backend_error"),
        (lambda: _snapshot(image=b""), "no_image"),
        (lambda: screenshots.Answer(200, {}, json.dumps({
            "success": True, "result": {"screenshot": "not base64!"}}).encode()), "no_image"),
    ])
    def test_challenges_and_odd_answers(self, monkeypatch, answer, kind):
        monkeypatch.setattr(screenshots, "_send", Backend(lambda *a: answer()))
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == kind
        assert screenshots.stored(view["id"])["image"] is None


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

    @pytest.mark.parametrize("where", ["top", "data"])
    def test_bot_challenge_not_kept(self, monkeypatch, where):
        # The page answered 403, as a bot challenge does: no screenshot of it.
        answer = json.loads(_microlink_answer(IMAGE_URL).body)
        (answer if where == "top" else answer["data"])["statusCode"] = 403
        backend = Backend(lambda method, url, kw: screenshots.Answer(
            200, {"content-type": "application/json"}, json.dumps(answer).encode()))
        monkeypatch.setattr(screenshots, "_send", backend)
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "page_failed"
        assert len(backend.calls) == 1 and screenshots.stored(view["id"])["image"] is None


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

    @pytest.mark.parametrize("resolved", [["10.0.0.5"], ["127.0.0.1"], ["169.254.169.254"],
                                          ["93.184.215.14", "10.0.0.5"], OSError("no DNS answer")])
    def test_guarded_refuses_before_any_request(self, monkeypatch, resolved):
        # Private, mixed or unresolvable: refused before a connection is made.
        def resolve(host, port):
            if isinstance(resolved, Exception):
                raise resolved
            return resolved
        monkeypatch.setattr(frameability, "_resolve", resolve)
        monkeypatch.setattr(frameability, "_http_request",
                            lambda *a, **kw: pytest.fail("sent a request"))
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


@_marks(GOTENBERG)
class TestDueOrder:
    def test_never_tried_first_and_failures_back_off(self, monkeypatch):
        failing = _gotenberg(page=lambda: screenshots.Answer(409, {}, b""))
        monkeypatch.setattr(screenshots, "_send", failing)
        _, _, failed = _setup()
        screenshots.capture(failed["id"])  # page_failed: backs off for a week
        _, _, fresh = _setup(url=OTHER)
        due = [view_id for view_id, _ in screenshots.due()]
        assert fresh["id"] in due and failed["id"] not in due
        # --refresh tries it anyway, after the never-tried views.
        due = [view_id for view_id, _ in screenshots.due(refresh=True)]
        assert due.index(fresh["id"]) < due.index(failed["id"])

    def test_unreachable_doesnt_back_off(self, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg(
            page=lambda: screenshots.Answer(503, {}, b"")))
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "unreachable"
        assert view["id"] in dict(screenshots.due())

    def test_backend_failures_dont_back_off(self, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg(
            page=lambda: screenshots.Answer(500, {}, b"")))
        _, _, view = _setup()
        assert screenshots.capture(view["id"]) == "backend_error"
        assert view["id"] in dict(screenshots.due())


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


# --- Jobs and the retake limit -------------------------------------------------------


def _member(dataset, role):
    user = factories.UserWithToken()
    helpers.call_action("organization_member_create", context={"ignore_auth": True},
                        id=dataset["owner_org"], username=user["name"], role=role)
    return user


def _ask(view, user):
    return helpers.call_action(
        "webpageviewplus_screenshot_request",
        context={"user": user["name"], "ignore_auth": False}, id=view["id"])


@_marks(GOTENBERG)
class TestJobs:
    def test_a_save_queues_one_when_there_is_none(self, jobs):
        _, _, view = _setup()
        _save(view, title="Renamed")
        assert [(fn.__name__, args[0], queue) for fn, args, queue in jobs] == [
            ("job", view["id"], "webpageviewplus")]
        assert screenshots.pending(view["id"])

    def test_a_save_keeps_a_current_one(self, monkeypatch, jobs):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        _, _, view = _setup()
        screenshots.capture(view["id"])
        _save(view, title="Renamed")
        assert jobs == []

    @pytest.mark.ckan_config("ckanext.webpageviewplus.screenshot_jobs", "false")
    def test_jobs_off(self, jobs):
        _, _, view = _setup()
        _save(view, title="Renamed")
        assert jobs == []

    def test_queue_failure_never_fails_the_save(self, monkeypatch):
        def broken(*a, **kw):
            raise RuntimeError("no redis")
        monkeypatch.setattr(screenshots.toolkit, "enqueue_job", broken)
        _, _, view = _setup()
        assert _save(view, title="Renamed")["title"] == "Renamed"
        assert not screenshots.pending(view["id"])

    def test_job_takes_it_and_clears_the_mark(self, monkeypatch, jobs):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        _, _, view = _setup()
        _save(view, title="Renamed")
        (fn, args, _), = jobs
        fn(*args)
        assert screenshots.stored(view["id"])["digest"]
        assert not screenshots.pending(view["id"])

    def test_job_inside_the_cooldown_does_nothing(self, monkeypatch):
        backend = _gotenberg()
        monkeypatch.setattr(screenshots, "_send", backend)
        _, _, view = _setup()
        screenshots.capture(view["id"])
        sent = len(backend.calls)
        screenshots.job(view["id"])
        assert len(backend.calls) == sent

    def test_url_saved_during_a_capture_gets_its_own_job(self, monkeypatch, jobs):
        _, _, view = _setup()
        backend = _gotenberg()

        def answer(*args, **kwargs):
            if "files" in kwargs:  # the capture of PAGE: the editor saves OTHER meanwhile
                _blocked(view["id"], OTHER)
                _save(view, page_url=OTHER)
            return backend(*args, **kwargs)
        monkeypatch.setattr(screenshots, "_send", answer)
        _save(view, title="Renamed")
        (fn, args, _), = jobs  # OTHER's save was held back by this job's mark
        fn(*args)
        assert len(jobs) == 2 and jobs[1][1][0] == view["id"]
        assert screenshots.pending(view["id"])

    def test_a_job_clears_only_its_own_mark(self, redis):
        _, _, view = _setup()
        redis.set(screenshots._pending_key(view["id"]), "newer")
        screenshots.job(view["id"], "older")
        assert screenshots.pending(view["id"])
        screenshots.job(view["id"], "newer")
        assert not screenshots.pending(view["id"])

    def test_job_never_raises(self, monkeypatch):
        def broken(view_id):
            raise ValueError("https://secret.example.com/")
        monkeypatch.setattr(screenshots, "capture", broken)
        _, _, view = _setup()
        screenshots.job(view["id"])


@_marks(GOTENBERG)
class TestRequestAction:
    def test_editor_member_anonymous(self, jobs):
        dataset, _, view = _setup()
        assert _ask(view, _member(dataset, "editor")) == {"state": "pending"}
        with pytest.raises(toolkit.NotAuthorized):
            _ask(view, _member(dataset, "member"))
        with pytest.raises(toolkit.NotAuthorized):
            helpers.call_action("webpageviewplus_screenshot_request",
                                context={"user": "", "ignore_auth": False}, id=view["id"])
        assert len(jobs) == 1

    def test_second_request_while_pending(self, jobs):
        dataset, _, view = _setup()
        editor = _member(dataset, "editor")
        assert _ask(view, editor) == {"state": "pending"}
        assert _ask(view, editor) == {"state": "pending"}
        assert len(jobs) == 1

    def test_inside_the_cooldown(self, monkeypatch, jobs):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, _, view = _setup()
        screenshots.capture(view["id"])
        with pytest.raises(toolkit.ValidationError) as refused:
            _ask(view, _member(dataset, "editor"))
        left = refused.value.error_dict["seconds_left"][0]
        assert 0 < left <= 60
        assert "{} seconds".format(left) in refused.value.error_dict["id"][0]
        assert jobs == []

    @pytest.mark.ckan_config("ckanext.webpageviewplus.screenshot_cooldown_seconds", "0")
    def test_no_cooldown(self, monkeypatch, jobs):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, _, view = _setup()
        screenshots.capture(view["id"])
        assert _ask(view, _member(dataset, "editor")) == {"state": "pending"}
        assert len(jobs) == 1

    def test_changed_url_skips_the_cooldown(self, monkeypatch, jobs):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, _, view = _setup()
        screenshots.capture(view["id"])
        _blocked(view["id"], OTHER)
        _save(view, page_url=OTHER)
        assert len(jobs) == 1  # the save itself: no screenshot of OTHER yet

    def test_view_without_a_screenshot_target(self, jobs):
        dataset, _, view = _setup(no_auto_thumbnail="true")
        with pytest.raises(toolkit.ValidationError):
            _ask(view, _member(dataset, "editor"))

    @pytest.mark.ckan_config("ckanext.webpageviewplus.screenshot_jobs", "false")
    def test_refused_when_jobs_are_off(self, jobs):
        dataset, _, view = _setup()
        with pytest.raises(toolkit.ValidationError):
            _ask(view, _member(dataset, "editor"))


@_marks(GOTENBERG)
class TestFormImage:
    def test_editor_sees_it_even_when_not_in_use(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, _, view = _setup()
        screenshots.capture(view["id"])
        health.store(view["id"], PAGE, frameability.Probe(frameability.FRAMEABLE))
        src = url_for("webpageviewplus_screenshots.serve", view_id=view["id"], form=1)
        editor = _member(dataset, "editor")
        response = app.get(src, headers={"Authorization": editor["token"]})
        assert response.data == screenshots.stored(view["id"])["image"]
        cache = response.headers["Cache-Control"]
        assert "no-store" in cache and "private" in cache and "public" not in cache
        app.get(src, headers={"Authorization": _member(dataset, "member")["token"]}, status=404)
        app.get(src, status=404)
        # Without form=1, a copy not in use is never served.
        app.get(url_for("webpageviewplus_screenshots.serve", view_id=view["id"]), status=404)


def _form(app, dataset, resource, view):
    sysadmin = factories.SysadminWithToken()
    return app.get(url_for(f"{dataset['type']}_resource.edit_view", id=dataset["name"],
                           resource_id=resource["id"], view_id=view["id"]),
                   headers={"Authorization": sysadmin["token"]}).body


@_marks(GOTENBERG)
class TestPanel:
    def test_in_use_and_inside_the_cooldown(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, resource, view = _setup()
        screenshots.capture(view["id"])
        body = _form(app, dataset, resource, view)
        assert 'id="webpageviewplus-screenshot-heading"' in body
        assert "form=1" in body
        assert "card and as its thumbnail in the resource list" in body
        assert "Take a new screenshot" in body and "disabled" in body
        assert "You can take a new screenshot in" in body

    @pytest.mark.ckan_config("ckanext.webpageviewplus.screenshot_cooldown_seconds", "0")
    def test_button_enabled_without_cooldown(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, resource, view = _setup()
        screenshots.capture(view["id"])
        body = _form(app, dataset, resource, view)
        assert "data-webpageviewplus-screenshot-request" in body
        assert "You can take a new screenshot in" not in body

    def test_pending(self, app):
        dataset, resource, view = _setup()
        body = _form(app, dataset, resource, view)
        assert "A screenshot will be taken in the background" in body

    def test_failed(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg(
            page=lambda: screenshots.Answer(409, {}, b"")))
        dataset, resource, view = _setup()
        screenshots.capture(view["id"])
        body = _form(app, dataset, resource, view)
        assert "failed: the page didn" in body and "t load." in body

    def test_not_in_use(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, resource, view = _setup()
        screenshots.capture(view["id"])
        health.store(view["id"], PAGE, frameability.Probe(frameability.FRAMEABLE))
        body = _form(app, dataset, resource, view)
        assert "webpageviewplus-screenshot-unused" in body
        assert "can now be shown in a frame" in body
        assert "data-webpageviewplus-screenshot-request" not in body

    def test_panel_never_queues(self, app, jobs):
        dataset, resource, view = _setup()
        _form(app, dataset, resource, view)
        assert jobs == []


@_marks(OFF)
class TestPanelOff:
    def test_no_panel(self, app):
        dataset, resource, view = _setup()
        assert "webpageviewplus-screenshot-heading" not in _form(app, dataset, resource, view)


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
class TestCheckViews:
    """check-views queues a screenshot for a blocked view with none."""

    @pytest.fixture(autouse=True)
    def checked(self, monkeypatch):
        from ckanext.webpageviewplus import cli as wvp_cli
        monkeypatch.setattr(wvp_cli, "_plugin_loaded", lambda name: True)
        monkeypatch.setattr(wvp_cli.frameability, "probe", lambda url, identity, deadline:
                            frameability.Probe(frameability.BLOCKED, "x-frame-options",
                                               status=200, urls=(url,)))
        monkeypatch.setattr(screenshots, "queue_has_worker", lambda: True)

    def _run(self, cli, dataset, *args):
        result = cli.invoke(ckan, ["webpageviewplus", "check-views", "--dataset",
                                   dataset["name"], *args])
        # The blocked page is a problem whether or not a screenshot is queued.
        assert result.exit_code == 1, result.output
        return result

    def test_queues_one_without_a_screenshot(self, cli, jobs):
        dataset, _, view = _setup()
        result = self._run(cli, dataset)
        assert [(fn.__name__, args[0]) for fn, args, _ in jobs] == [("job", view["id"])]
        assert "1 screenshot(s) queued." in result.output
        # Pending: the next run doesn't queue it again.
        self._run(cli, dataset)
        assert len(jobs) == 1

    def test_not_for_a_current_screenshot(self, cli, monkeypatch, jobs):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, _, view = _setup()
        screenshots.capture(view["id"])
        self._run(cli, dataset)
        assert jobs == []

    def test_not_with_no_store(self, cli, jobs):
        dataset, _, _ = _setup()
        self._run(cli, dataset, "--no-store")
        assert jobs == []

    @pytest.mark.ckan_config("ckanext.webpageviewplus.screenshot_jobs", "false")
    def test_not_with_jobs_off(self, cli, jobs):
        dataset, _, _ = _setup()
        self._run(cli, dataset)
        assert jobs == []

    def test_not_without_the_thumbnails_plugin(self, cli, monkeypatch, jobs):
        from ckanext.webpageviewplus import cli as wvp_cli
        monkeypatch.setattr(wvp_cli, "_plugin_loaded", lambda name: False)
        dataset, _, _ = _setup()
        self._run(cli, dataset)
        assert jobs == []

    def test_not_without_a_worker(self, cli, monkeypatch, jobs):
        monkeypatch.setattr(screenshots, "queue_has_worker", lambda: False)
        dataset, _, _ = _setup()
        result = self._run(cli, dataset)
        assert jobs == [] and "No worker reads the webpageviewplus queue" in result.output

    def test_page_failure_backs_off(self, cli, monkeypatch, jobs):
        monkeypatch.setattr(screenshots, "_send", _gotenberg(
            page=lambda: screenshots.Answer(409, {}, b"")))
        dataset, _, view = _setup()
        assert screenshots.capture(view["id"]) == "page_failed"
        monkeypatch.setattr(settings, "screenshot_cooldown_seconds", lambda: 0)
        self._run(cli, dataset)
        assert jobs == []

    def test_unreachable_doesnt_back_off(self, cli, monkeypatch, jobs):
        monkeypatch.setattr(screenshots, "_send", _gotenberg(
            page=lambda: screenshots.Answer(503, {}, b"")))
        dataset, _, view = _setup()
        assert screenshots.capture(view["id"]) == "unreachable"
        monkeypatch.setattr(settings, "screenshot_cooldown_seconds", lambda: 0)
        self._run(cli, dataset)
        assert [args[0] for _, args, _ in jobs] == [view["id"]]

    def test_queue_failure_stops_queuing_not_the_command(self, cli, monkeypatch):
        calls = []

        def broken(*a, **kw):
            calls.append(a)
            raise RuntimeError("no redis")
        monkeypatch.setattr(screenshots.toolkit, "enqueue_job", broken)
        dataset, resource, view = _setup()
        second = _view(resource["id"])
        _blocked(second["id"])
        result = self._run(cli, dataset)
        assert len(calls) == 1 and "queued" not in result.output
        assert result.output.count("refuses-embedding") == 2


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

    @pytest.mark.parametrize("listening, level", [(False, "warn"), (None, "note"), (True, None)])
    def test_doctor_worker(self, monkeypatch, listening, level):
        from ckanext.webpageviewplus import cli as wvp_cli
        monkeypatch.setattr(wvp_cli, "_plugin_loaded", lambda name: True)
        monkeypatch.setattr(screenshots, "queue_has_worker", lambda: listening)
        found = [lv for lv, text in wvp_cli._screenshot_findings() if "worker" in text]
        assert found == ([level] if level else [])
