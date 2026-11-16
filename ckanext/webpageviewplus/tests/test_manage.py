"""The management interface's actions (manage.py)."""
import datetime

import pytest

import ckan.model as model
from ckan.plugins import toolkit
from ckan.tests import factories, helpers

from ckanext.webpageviewplus import manage, screenshots, thumbnails
from ckanext.webpageviewplus.tests.test_screenshots import (
    GOTENBERG, OTHER, FakeRedis, _gotenberg, _jpeg, _marks, _member, _setup, _view,
)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(screenshots, "_redis", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def jobs(monkeypatch):
    queued = []

    def enqueue_job(fn, args=None, kwargs=None, title=None, queue="default", rq_kwargs=None):
        queued.append((fn, list(args or []), queue))
    monkeypatch.setattr(screenshots.toolkit, "enqueue_job", enqueue_job)
    return queued


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    # No page is ever fetched: saves don't probe, rechecks pass, a thumbnail
    # is made only where a test says so, and a worker reads the queue.
    from ckanext.webpageviewplus import frameability, health
    monkeypatch.setattr(health, "after_save", lambda view: None)
    monkeypatch.setattr(screenshots, "_recheck", lambda url: frameability.Probe(
        frameability.BLOCKED, "x-frame-options", status=200))
    monkeypatch.setattr(thumbnails, "_make", lambda *a, **kw: None)
    monkeypatch.setattr(screenshots, "queue_has_worker", lambda: True)


def _as(user):
    return {"user": user["name"] if user else "", "ignore_auth": False}


def _call(action, user, **data):
    from flask import g, has_app_context
    if has_app_context():
        # Each call stands for a request of its own: no rows cached from the last.
        for key in ("webpageviewplus_thumbnails", "webpageviewplus_screenshots"):
            g.pop(key, None)
    return helpers.call_action(action, context=_as(user), **data)


def _sysadmin():
    return factories.Sysadmin()


def _row(answer, view):
    return next(r for r in answer["rows"] if r["view_id"] == view["id"])


@_marks(GOTENBERG)
@pytest.mark.usefixtures("with_request_context")
class TestStatus:
    def test_who_sees_a_dataset(self):
        dataset, _, view = _setup()
        rows = _call("webpageviewplus_thumbnail_status", _member(dataset, "editor"),
                     id=dataset["name"])["rows"]
        assert [r["view_id"] for r in rows] == [view["id"]]
        for user in (_member(dataset, "member"), None):
            with pytest.raises(toolkit.NotAuthorized):
                _call("webpageviewplus_thumbnail_status", user, id=dataset["name"])

    def test_site_wide_is_for_sysadmins(self):
        dataset, _, view = _setup()
        with pytest.raises(toolkit.NotAuthorized):
            _call("webpageviewplus_thumbnail_status", _member(dataset, "editor"))
        answer = _call("webpageviewplus_thumbnail_status", _sysadmin(), q=dataset["name"])
        assert [r["view_id"] for r in answer["rows"]] == [view["id"]]
        assert answer["count"] == 1 and answer["backend"]["backend"] == "gotenberg"

    def test_blocked_then_taken(self, monkeypatch):
        dataset, _, view = _setup()
        row = _row(_call("webpageviewplus_thumbnail_status", _sysadmin(), id=dataset["id"]),
                   view)
        assert row["flags"] == ["blocked"] and row["shows"] == {"kind": "format"}
        assert row["screenshot"]["state"] == "pending"
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        assert screenshots.capture(view["id"]) == "taken"
        row = _row(_call("webpageviewplus_thumbnail_status", _sysadmin(), id=dataset["id"]),
                   view)
        assert row["flags"] == [] and row["shows"]["kind"] == "screenshot"
        assert row["screenshot"]["state"] == "in_use" and row["screenshot"]["taken"]

    def test_filters_and_paging(self, monkeypatch):
        failing = _gotenberg(page=lambda: screenshots.Answer(409, {}, b""))
        monkeypatch.setattr(screenshots, "_send", failing)
        dataset, resource, failed = _setup()
        screenshots.capture(failed["id"])
        opted = _view(resource["id"], no_auto_thumbnail="true")
        sysadmin = _sysadmin()

        def ids(**data):
            return [r["view_id"] for r in _call("webpageviewplus_thumbnail_status", sysadmin,
                                                 q=dataset["name"], **data)["rows"]]
        assert ids(filter="failed") == [failed["id"]]
        assert ids(filter="opted_out") == [opted["id"]]
        assert ids(filter="stale") == []
        monkeypatch.setattr(manage, "PAGE_SIZE", 1)
        assert len(ids(page=1)) == len(ids(page=2)) == 1 and ids(page=3) == []
        with pytest.raises(toolkit.ValidationError):
            ids(filter="nonsense")

    @pytest.mark.ckan_config("ckanext.webpageviewplus.screenshot_seconds", "86400")
    def test_stale(self, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, _, view = _setup()
        screenshots.capture(view["id"])
        with model.meta.engine.begin() as connection:
            connection.execute(screenshots.table.update().where(
                screenshots.table.c.view_id == view["id"]).values(
                    taken_at=datetime.datetime.utcnow() - datetime.timedelta(days=2)))
        rows = _call("webpageviewplus_thumbnail_status", _sysadmin(), filter="stale",
                     q=dataset["name"])["rows"]
        assert [r["view_id"] for r in rows] == [view["id"]]

    def test_status_never_calls_the_backend(self, monkeypatch, redis):
        backend = _gotenberg()
        monkeypatch.setattr(screenshots, "_send", backend)
        _setup()
        answer = _call("webpageviewplus_thumbnail_status", _sysadmin())
        assert backend.calls == [] and answer["backend"]["gotenberg_safe"] is None


@_marks(GOTENBERG)
class TestRequestMany:
    def test_per_view_auth(self, jobs):
        dataset, _, view = _setup()
        other, _, elsewhere = _setup()
        answer = _call("webpageviewplus_screenshot_request", _member(dataset, "editor"),
                       ids=[view["id"], elsewhere["id"], "no-such-view"])
        assert answer == {"queued": 1, "results": {
            view["id"]: "queued", elsewhere["id"]: "not_found", "no-such-view": "not_found"}}
        answer = _call("webpageviewplus_screenshot_request", _member(dataset, "member"),
                       ids=[view["id"]])
        assert answer["results"] == {view["id"]: "not_found"}
        with pytest.raises(toolkit.NotAuthorized):
            _call("webpageviewplus_screenshot_request", None, ids=[view["id"]])
        assert len(jobs) == 1

    def test_sysadmin_skips_the_cooldown_in_the_job_too(self, monkeypatch, jobs, redis):
        backend = _gotenberg(page=lambda: screenshots.Answer(409, {}, b""))
        monkeypatch.setattr(screenshots, "_send", backend)
        dataset, _, view = _setup()
        screenshots.capture(view["id"])  # an attempt: the cooldown counts from it
        answer = _call("webpageviewplus_screenshot_request", _member(dataset, "editor"),
                       ids=[view["id"]])
        assert answer["results"] == {view["id"]: "cooldown"} and jobs == []
        answer = _call("webpageviewplus_screenshot_request", _sysadmin(), ids=[view["id"]])
        assert answer["results"] == {view["id"]: "queued"}
        (fn, args, _), = jobs
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        fn(*args)
        assert screenshots.stored(view["id"])["digest"]

    def test_no_target_and_limits(self, jobs):
        dataset, resource, view = _setup()
        opted = _view(resource["id"], no_auto_thumbnail="true")
        answer = _call("webpageviewplus_screenshot_request", _sysadmin(), ids=opted["id"])
        assert answer["results"] == {opted["id"]: "no_target"}
        with pytest.raises(toolkit.ValidationError):
            _call("webpageviewplus_screenshot_request", _sysadmin(),
                  ids=["x{}".format(i) for i in range(manage.MAX_IDS + 1)])
        with pytest.raises(toolkit.ValidationError):
            _call("webpageviewplus_screenshot_request", _sysadmin(), ids=[])

    @pytest.mark.ckan_config("ckanext.webpageviewplus.screenshot_jobs", "false")
    def test_jobs_off(self):
        _, _, view = _setup()
        with pytest.raises(toolkit.ValidationError):
            _call("webpageviewplus_screenshot_request", _sysadmin(), ids=[view["id"]])

    def test_queue_failure_stops_there(self, monkeypatch):
        calls = []

        def broken(*a, **kw):
            calls.append(a)
            raise RuntimeError("no redis")
        monkeypatch.setattr(screenshots.toolkit, "enqueue_job", broken)
        _, _, first = _setup()
        _, resource, second = _setup(url=OTHER)
        opted = _view(resource["id"], no_auto_thumbnail="true")
        answer = _call("webpageviewplus_screenshot_request", _sysadmin(),
                       ids=[first["id"], second["id"], opted["id"], "no-such-view"])
        # Views queuing wasn't tried for still get their own answers.
        assert answer["results"] == {first["id"]: "error", second["id"]: "error",
                                     opted["id"]: "no_target", "no-such-view": "not_found"}
        assert len(calls) == 1


THUMBNAILS = [pytest.mark.ckan_config("ckanext.webpageviewplus.thumbnail_fetch", "true")]


@_marks(GOTENBERG + THUMBNAILS)
class TestRefresh:
    def _making(self, monkeypatch, image):
        made = []

        def make(source, resource, deadline, memo):
            made.append(source)
            return image
        monkeypatch.setattr(thumbnails, "_make", make)
        return made

    def test_one_view_at_once(self, monkeypatch, jobs):
        dataset, _, view = _setup()
        editor = _member(dataset, "editor")
        made = self._making(monkeypatch, _jpeg((320, 180)))
        answer = _call("webpageviewplus_thumbnail_refresh", editor, ids=[view["id"]])
        assert answer == {"queued": 0, "results": {view["id"]: "remade"}} and made
        self._making(monkeypatch, None)  # the page is down: the copy stays
        answer = _call("webpageviewplus_thumbnail_refresh", editor, ids=[view["id"]])
        assert answer["results"] == {view["id"]: "kept"} and jobs == []

    def test_nothing_to_make_one_from(self, monkeypatch):
        dataset, resource, _ = _setup()
        opted = _view(resource["id"], no_auto_thumbnail="true")
        answer = _call("webpageviewplus_thumbnail_refresh", _member(dataset, "editor"),
                       ids=[opted["id"]])
        assert answer["results"] == {opted["id"]: "none"}

    def test_several_are_queued_once(self, monkeypatch, jobs, redis):
        dataset, resource, view = _setup()
        second = _view(resource["id"])
        editor = _member(dataset, "editor")
        answer = _call("webpageviewplus_thumbnail_refresh", editor,
                       ids=[view["id"], second["id"]])
        assert answer["queued"] == 2
        answer = _call("webpageviewplus_thumbnail_refresh", editor,
                       ids=[view["id"], second["id"]])
        assert answer == {"queued": 0, "results": {view["id"]: "pending",
                                                   second["id"]: "pending"}}
        made = self._making(monkeypatch, _jpeg((320, 180)))
        fn, args, queue = jobs[0]
        assert queue == screenshots.QUEUE
        fn(*args)
        assert made and thumbnails.stored(view["id"])
        assert not redis.exists(manage._refresh_key(view["id"]))

    def test_several_need_a_worker(self, monkeypatch, jobs):
        monkeypatch.setattr(screenshots, "queue_has_worker", lambda: False)
        dataset, resource, view = _setup()
        second = _view(resource["id"])
        with pytest.raises(toolkit.ValidationError):
            _call("webpageviewplus_thumbnail_refresh", _member(dataset, "editor"),
                  ids=[view["id"], second["id"]])
        assert jobs == []

    def test_auth(self):
        dataset, _, view = _setup()
        answer = _call("webpageviewplus_thumbnail_refresh", _member(dataset, "member"),
                       ids=[view["id"]])
        assert answer["results"] == {view["id"]: "not_found"}
        with pytest.raises(toolkit.NotAuthorized):
            _call("webpageviewplus_thumbnail_refresh", None, ids=[view["id"]])


@_marks(GOTENBERG + THUMBNAILS)
class TestDelete:
    def test_screenshot_removed_but_its_attempt_kept(self, monkeypatch, jobs, redis):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, _, view = _setup()
        screenshots.capture(view["id"])
        editor = _member(dataset, "editor")
        answer = _call("webpageviewplus_thumbnail_delete", editor, ids=[view["id"]],
                       remove="screenshot")
        assert answer == {"removed": 1, "results": {view["id"]: "removed"}}
        row = screenshots.stored_meta(view["id"])
        assert row["digest"] is None and row["attempted_at"]
        # Removing doesn't get around the cooldown.
        answer = _call("webpageviewplus_screenshot_request", editor, ids=[view["id"]])
        assert answer["results"] == {view["id"]: "cooldown"}
        answer = _call("webpageviewplus_thumbnail_delete", editor, ids=[view["id"]])
        assert answer["results"] == {view["id"]: "none"}

    def test_thumbnail_removed(self, monkeypatch):
        monkeypatch.setattr(thumbnails, "_make", lambda *a, **kw: _jpeg((320, 180)))
        dataset, _, view = _setup()
        _call("webpageviewplus_thumbnail_refresh", _sysadmin(), ids=[view["id"]])
        assert thumbnails.stored(view["id"])
        answer = _call("webpageviewplus_thumbnail_delete", _member(dataset, "editor"),
                       ids=[view["id"]], remove="thumbnail")
        assert answer["results"] == {view["id"]: "removed"}
        assert thumbnails.stored(view["id"]) is None

    def test_auth_and_values(self):
        dataset, _, view = _setup()
        answer = _call("webpageviewplus_thumbnail_delete", _member(dataset, "member"),
                       ids=[view["id"]])
        assert answer["results"] == {view["id"]: "not_found"}
        with pytest.raises(toolkit.NotAuthorized):
            _call("webpageviewplus_thumbnail_delete", None, ids=[view["id"]])
        with pytest.raises(toolkit.ValidationError):
            _call("webpageviewplus_thumbnail_delete", _sysadmin(), ids=[view["id"]],
                  remove="everything")


def _page(dataset):
    return "/dataset/{}/thumbnails".format(dataset["name"])


def _token(body):
    import re
    return re.search(r'name="_csrf_token" value="([^"]+)"', body).group(1)


@_marks(GOTENBERG)
class TestDatasetPage:
    def test_who_sees_it(self, app):
        dataset, _, view = _setup()
        editor = _member(dataset, "editor")
        body = app.get(_page(dataset), extra_environ={"REMOTE_USER": editor["name"]}).body
        assert view["id"] in body and "Remake the thumbnail" in body
        for user in (_member(dataset, "member"), None):
            environ = {"REMOTE_USER": user["name"]} if user else {}
            assert app.get(_page(dataset), extra_environ=environ, status=403)

    def test_tab_on_the_edit_pages(self, app):
        dataset, _, _ = _setup()
        editor = _member(dataset, "editor")
        body = app.get("/dataset/edit/{}".format(dataset["name"]),
                       extra_environ={"REMOTE_USER": editor["name"]}).body
        assert 'href="{}"'.format(_page(dataset)) in body

    def test_form_needs_its_token(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, _, view = _setup()
        screenshots.capture(view["id"])
        editor = _member(dataset, "editor")
        environ = {"REMOTE_USER": editor["name"]}
        form = {"view_id": view["id"], "do": "remove"}
        app.post(_page(dataset), data=form, extra_environ=environ, status=400)
        assert screenshots.stored(view["id"])["digest"]
        # One client, so the session holding the token is kept.
        client = app.test_client()
        token = _token(client.get(_page(dataset), extra_environ=environ).body)
        answer = client.post(_page(dataset), data=dict(form, _csrf_token=token),
                             extra_environ=environ, follow_redirects=False)
        assert answer.status_code == 302 and answer.headers["Location"].endswith(_page(dataset))
        assert screenshots.stored_meta(view["id"])["digest"] is None
        assert "Removed." in client.get(_page(dataset), extra_environ=environ).body

    def test_api_token_needs_no_form_token(self, app, monkeypatch):
        dataset, _, view = _setup()
        editor = _member(dataset, "editor")
        answer = app.post(_page(dataset), data={"view_id": view["id"], "do": "auto_off"},
                          headers={"Authorization": editor["token"]}, follow_redirects=False)
        assert answer.status_code == 302
        assert model.ResourceView.get(view["id"]).config.get("no_auto_thumbnail")

    def test_only_this_datasets_views(self, app):
        dataset, _, _ = _setup()
        _, _, elsewhere = _setup()
        sysadmin = factories.SysadminWithToken()
        app.post(_page(dataset), data={"view_id": elsewhere["id"], "do": "remove"},
                 headers={"Authorization": sysadmin["token"]}, status=404)

    @pytest.mark.ckan_config("ckanext.webpageviewplus.thumbnail_fetch", "true")
    def test_pick_and_back(self, app):
        dataset, _, view = _setup()
        image = factories.Resource(package_id=dataset["id"], url="https://img.example.com/a.png",
                                   format="PNG", name="Chart")
        sysadmin = factories.SysadminWithToken()
        headers = {"Authorization": sysadmin["token"]}
        body = app.get(_page(dataset), headers=headers).body
        assert '<option value="{}"'.format(image["id"]) in body
        app.post(_page(dataset), data={"view_id": view["id"], "do": "pick",
                                       "thumbnail_resource": image["id"]}, headers=headers)
        assert model.ResourceView.get(view["id"]).config["thumbnail_resource"] == image["id"]
        app.post(_page(dataset), data={"view_id": view["id"], "do": "pick",
                                       "thumbnail_resource": "not-an-image"}, headers=headers)
        assert model.ResourceView.get(view["id"]).config["thumbnail_resource"] == image["id"]
        app.post(_page(dataset), data={"view_id": view["id"], "do": "pick",
                                       "thumbnail_resource": ""}, headers=headers)
        assert not model.ResourceView.get(view["id"]).config.get("thumbnail_resource")
