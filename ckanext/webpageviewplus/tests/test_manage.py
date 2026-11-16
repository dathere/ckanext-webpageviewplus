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


PASSWORD = "Correct-horse-battery-1"
# CKAN's test config turns CSRF off (2.11 and 2.12); these tests need it on.
CSRF = pytest.mark.ckan_config("WTF_CSRF_ENABLED", "true")


def _session(app, user):
    """A client logged in through the login form, so requests carry a
    session cookie, as a browser's do (CKAN 2.12 dropped REMOTE_USER, and
    API-token requests skip CSRF)."""
    client = app.test_client()
    token = _token(client.get("/user/login").body)
    client.post("/user/login", data={"login": user["name"], "password": PASSWORD,
                                     "_csrf_token": token})
    return client


def _editor_with_password(dataset):
    user = factories.User(password=PASSWORD)
    helpers.call_action("organization_member_create", context={"ignore_auth": True},
                        id=dataset["owner_org"], username=user["name"], role="editor")
    return user


@_marks(GOTENBERG)
class TestDatasetPage:
    def test_who_sees_it(self, app):
        dataset, _, view = _setup()
        editor = _member(dataset, "editor")
        body = app.get(_page(dataset), headers={"Authorization": editor["token"]}).body
        assert view["id"] in body and "Remake the thumbnail" in body
        member = _member(dataset, "member")
        app.get(_page(dataset), headers={"Authorization": member["token"]}, status=403)
        app.get(_page(dataset), status=403)

    def test_tab_on_the_edit_pages(self, app):
        dataset, _, _ = _setup()
        editor = _member(dataset, "editor")
        body = app.get("/dataset/edit/{}".format(dataset["name"]),
                       headers={"Authorization": editor["token"]}).body
        assert 'href="{}"'.format(_page(dataset)) in body

    @CSRF
    def test_form_needs_its_token(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, _, view = _setup()
        screenshots.capture(view["id"])
        client = _session(app, _editor_with_password(dataset))
        form = {"view_id": view["id"], "do": "remove"}
        assert client.post(_page(dataset), data=form).status_code == 400
        assert screenshots.stored(view["id"])["digest"]
        token = _token(client.get(_page(dataset)).body)
        answer = client.post(_page(dataset), data=dict(form, _csrf_token=token),
                             follow_redirects=False)
        assert answer.status_code == 302 and answer.headers["Location"].endswith(_page(dataset))
        assert screenshots.stored_meta(view["id"])["digest"] is None
        assert "Removed." in client.get(_page(dataset)).body

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


ADMIN = "/ckan-admin/thumbnails"


@_marks(GOTENBERG)
class TestAdminPage:
    def test_sysadmins_only(self, app):
        dataset, _, view = _setup()
        sysadmin = factories.SysadminWithToken()
        body = app.get(ADMIN, headers={"Authorization": sysadmin["token"]},
                       query_string={"q": dataset["name"]}).body
        assert view["id"] in body and "Refuses private addresses" in body
        assert 'href="{}"'.format(ADMIN) in app.get(
            "/ckan-admin", headers={"Authorization": sysadmin["token"]}).body
        editor = _member(dataset, "editor")
        app.get(ADMIN, headers={"Authorization": editor["token"]}, status=403)
        app.get(ADMIN, status=403)

    def test_never_calls_the_backend(self, app, monkeypatch, redis):
        backend = _gotenberg()
        monkeypatch.setattr(screenshots, "_send", backend)
        sysadmin = factories.SysadminWithToken()
        assert "Not checked recently." in app.get(
            ADMIN, headers={"Authorization": sysadmin["token"]}).body
        assert backend.calls == []

    @CSRF
    def test_recheck_needs_its_token(self, app, monkeypatch, redis):
        backend = _gotenberg()
        monkeypatch.setattr(screenshots, "_send", backend)
        client = _session(app, factories.Sysadmin(password=PASSWORD))
        assert client.post(ADMIN, data={"do": "recheck"}).status_code == 400
        assert backend.calls == []
        token = _token(client.get(ADMIN).body)
        client.post(ADMIN, data={"do": "recheck", "_csrf_token": token})
        assert sorted(backend.form_urls()) == sorted(screenshots.CANARIES)
        assert "Yes, checked in the last hour." in client.get(ADMIN).body

    def test_ticked_and_whole_filter(self, app, monkeypatch, jobs):
        dataset, resource, first = _setup()
        second = _view(resource["id"])
        from ckanext.webpageviewplus.tests.test_screenshots import _blocked
        _blocked(second["id"])
        sysadmin = factories.SysadminWithToken()
        headers = {"Authorization": sysadmin["token"]}
        answer = app.post(ADMIN, data={"do": "screenshot", "ids": [first["id"]],
                                       "q": dataset["name"]}, headers=headers,
                          follow_redirects=False)
        assert answer.status_code == 302 and "q=" in answer.headers["Location"]
        assert [args[0] for _, args, _ in jobs] == [first["id"]]
        app.post(ADMIN, data={"do": "screenshot", "scope": "all", "filter": "blocked",
                              "q": dataset["name"]}, headers=headers)
        # The first is pending already; the filter's other view is queued.
        assert [args[0] for _, args, _ in jobs] == [first["id"], second["id"]]

    def test_nothing_ticked(self, app, jobs):
        sysadmin = factories.SysadminWithToken()
        body = app.post(ADMIN, data={"do": "screenshot"},
                        headers={"Authorization": sysadmin["token"]}).body
        assert "Tick at least one view." in body and jobs == []


@_marks(GOTENBERG)
class TestAdminRemoveAll:
    def test_whole_filter_asks_first(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        dataset, _, view = _setup()
        screenshots.capture(view["id"])
        headers = {"Authorization": factories.SysadminWithToken()["token"]}
        form = {"do": "remove", "scope": "all", "q": dataset["name"]}
        body = app.post(ADMIN, data=form, headers=headers).body
        assert "Remove the copies?" in body and 'name="confirmed" value="1"' in body
        assert screenshots.stored(view["id"])["digest"]
        app.post(ADMIN, data=dict(form, confirmed="1"), headers=headers)
        assert screenshots.stored_meta(view["id"])["digest"] is None

    def test_ticked_rows_need_no_second_step(self, app, monkeypatch):
        monkeypatch.setattr(screenshots, "_send", _gotenberg())
        _, _, view = _setup()
        screenshots.capture(view["id"])
        headers = {"Authorization": factories.SysadminWithToken()["token"]}
        app.post(ADMIN, data={"do": "remove", "ids": [view["id"]]}, headers=headers)
        assert screenshots.stored_meta(view["id"])["digest"] is None


@_marks(GOTENBERG + THUMBNAILS)
class TestAdminBatches:
    def test_later_batches_reach_the_rest(self, app, monkeypatch, jobs):
        monkeypatch.setattr(manage, "MAX_IDS", 1)
        dataset, resource, first = _setup()
        second = _view(resource["id"])
        from ckanext.webpageviewplus.tests.test_screenshots import _blocked
        _blocked(second["id"])
        headers = {"Authorization": factories.SysadminWithToken()["token"]}
        body = app.get(ADMIN, query_string={"q": dataset["name"]}, headers=headers).body
        assert '<option value="1">views 2 to 2</option>' in body
        form = {"do": "screenshot", "scope": "all", "q": dataset["name"]}
        app.post(ADMIN, data=dict(form, start="1"), headers=headers)
        assert [args[0] for _, args, _ in jobs] == [second["id"]]
        app.post(ADMIN, data=form, headers=headers)
        assert [args[0] for _, args, _ in jobs] == [second["id"], first["id"]]

    def test_one_remake_is_reported(self, app, monkeypatch):
        monkeypatch.setattr(thumbnails, "_make", lambda *a, **kw: _jpeg((320, 180)))
        _, _, view = _setup()
        headers = {"Authorization": factories.SysadminWithToken()["token"]}
        body = app.post(ADMIN, data={"do": "refresh", "ids": [view["id"]]},
                        headers=headers).body
        assert "Remade: 1." in body

    def test_search_kept_whatever_it_says(self, app, jobs):
        headers = {"Authorization": factories.SysadminWithToken()["token"]}
        answer = app.post(ADMIN, data={"do": "screenshot", "q": "all", "page": "1"},
                          headers=headers, follow_redirects=False)
        location = answer.headers["Location"]
        assert "q=all" in location and "page=" not in location and "filter=" not in location
