"""Opt-in view counting. Needs the plugin's table in the test database:
ckan db upgrade -p webpage_view_plus."""
import datetime

import pytest

from ckan import model
from ckan.plugins.toolkit import NotAuthorized, url_for
from ckan.tests import factories, helpers

from ckanext.webpageviewplus import stats

BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
COUNTING = [
    pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus"),
    pytest.mark.ckan_config("ckan.views.default_views", ""),
    pytest.mark.ckan_config("ckanext.webpageviewplus.view_counting", "true"),
    pytest.mark.ckan_config("ckanext.webpageviewplus.privacy_policy_url",
                            "https://example.org/privacy"),
    pytest.mark.usefixtures("counts_table", "with_plugins"),
]


def _counted(count_views=True):
    sysadmin = factories.Sysadmin()
    dataset = factories.Dataset()
    resource = factories.Resource(package_id=dataset["id"], url="https://pages.example.com/a.html")
    view = helpers.call_action(
        "resource_view_create", context={"user": sysadmin["name"], "ignore_auth": True},
        resource_id=resource["id"], view_type="webpage_view_plus", title="Counted",
        count_views=count_views,
    )
    return sysadmin, dataset, resource, view


def _stats(sysadmin, view):
    return helpers.call_action(
        "webpageviewplus_view_stats_show", context={"user": sysadmin["name"]}, id=view["id"])


def _beacon(app, view, headers=BROWSER, **flags):
    return app.post("/webpageviewplus/count/" + view["id"],
                    params={k: str(v) for k, v in flags.items()}, headers=headers)


def _read(app, dataset, resource, headers=BROWSER):
    return app.get(url_for("dataset_resource.read", id=dataset["name"],
                           resource_id=resource["id"]), headers=headers)


@pytest.mark.parametrize("ua,bot", [
    (BROWSER["User-Agent"], False),
    ("", True),
    ("Googlebot/2.1 (+http://www.google.com/bot.html)", True),
    ("Mozilla/5.0 HeadlessChrome/130", True),
    ("python-requests/2.32", True),
    ("curl/8.4", True),
])
def test_bot_filter(ua, bot):
    assert stats.is_bot(ua) is bot


class TestCounting:
    pytestmark = COUNTING

    def test_impression_and_beacon(self, app):
        sysadmin, dataset, resource, view = _counted()
        body = _read(app, dataset, resource).body
        assert 'data-count-url="' in body and "/webpageviewplus/count/" in body
        assert "This view counts visits." in body
        assert 'href="https://example.org/privacy"' in body
        assert _beacon(app, view, seen=1, loaded=1, engaged=0, failed=0).status_code == 204
        result = _stats(sysadmin, view)
        assert result["counting"] is True
        assert result["totals"] == {
            "impressions": 1, "seen": 1, "loaded": 1, "engaged": 0, "failed": 0}
        assert result["days"][0]["day"] == datetime.date.today().isoformat()

    def test_readers_pages_query_no_totals(self, app, monkeypatch):
        calls = []
        monkeypatch.setattr(stats, "totals_or_none", lambda *a: calls.append(a))
        _, dataset, resource, _ = _counted()
        _read(app, dataset, resource)
        assert calls == []

    def test_flags_other_than_1_ignored(self, app):
        sysadmin, _, _, view = _counted()
        _beacon(app, view, seen=5, loaded="yes", engaged=-1, failed=1)
        assert _stats(sysadmin, view)["totals"]["failed"] == 1
        assert sum(_stats(sysadmin, view)["totals"].values()) == 1

    def test_gpc_and_bots_not_counted(self, app):
        sysadmin, dataset, resource, view = _counted()
        _read(app, dataset, resource, headers=dict(BROWSER, **{"Sec-GPC": "1"}))
        _read(app, dataset, resource, headers={"User-Agent": "Googlebot/2.1"})
        _beacon(app, view, headers=dict(BROWSER, **{"Sec-GPC": "1"}), seen=1)
        _beacon(app, view, headers={"User-Agent": "curl/8"}, seen=1)
        assert sum(_stats(sysadmin, view)["totals"].values()) == 0

    def test_view_not_opted_in(self, app):
        sysadmin, dataset, resource, view = _counted(count_views=False)
        body = _read(app, dataset, resource).body
        assert "data-count-url" not in body and "counts visits" not in body
        assert _beacon(app, view, seen=1).status_code == 204
        result = _stats(sysadmin, view)
        assert result["counting"] is False and sum(result["totals"].values()) == 0

    def test_unknown_view_is_a_quiet_204(self, app):
        assert _beacon(app, {"id": "nope"}, seen=1).status_code == 204

    def test_stats_need_resource_update(self, app):
        org = factories.Organization()
        dataset = factories.Dataset(owner_org=org["id"])
        resource = factories.Resource(package_id=dataset["id"],
                                      url="https://pages.example.com/a.html")
        view = helpers.call_action(
            "resource_view_create", context={"user": factories.Sysadmin()["name"]},
            resource_id=resource["id"], view_type="webpage_view_plus", title="C",
            count_views=True)
        editor = factories.User()
        helpers.call_action("organization_member_create", id=org["id"],
                            username=editor["name"], role="editor")
        assert helpers.call_auth("webpageviewplus_view_stats_show",
                                 {"user": editor["name"], "model": model}, id=view["id"])
        for user in (factories.User()["name"], ""):
            with pytest.raises(NotAuthorized):
                helpers.call_auth("webpageviewplus_view_stats_show",
                                  {"user": user, "model": model}, id=view["id"])

    def test_purge(self, app):
        sysadmin, _, _, view = _counted()
        stats.add(view["id"], seen=1)
        with model.meta.engine.begin() as connection:
            connection.execute(stats.table.insert().values(
                view_id=view["id"], day=datetime.date.today() - datetime.timedelta(days=400),
                seen=7))
        assert stats.purge(365) >= 1
        assert _stats(sysadmin, view)["totals"]["seen"] == 1


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.usefixtures("counts_table", "with_plugins")
def test_off_for_the_site_counts_nothing(app):
    sysadmin, dataset, resource, view = _counted()
    assert "data-count-url" not in _read(app, dataset, resource).body
    _beacon(app, view, seen=1)
    result = _stats(sysadmin, view)
    assert result["counting"] is False and sum(result["totals"].values()) == 0


@pytest.mark.parametrize("values,match", [
    ({"view_counting": "true", "privacy_policy_url": ""}, "privacy_policy_url"),
    ({"view_counting": "true", "privacy_policy_url": "javascript:alert(1)"}, "privacy_policy_url"),
    ({"view_counting_retention_days": "0"}, "view_counting_retention_days"),
    ({"view_counting_retention_days": 0}, "view_counting_retention_days"),
])
def test_counting_settings_checked_at_startup(monkeypatch, values, match):
    from ckan.exceptions import CkanConfigurationException
    from ckanext.webpageviewplus import settings

    real_get = settings._get
    monkeypatch.setattr(settings, "_get", lambda key: values.get(key, real_get(key)))
    with pytest.raises(CkanConfigurationException, match=match):
        settings.check()


class TestCountingCli:
    pytestmark = COUNTING

    def test_purge_and_summary(self, cli):
        from ckan.cli.cli import ckan

        _, _, _, view = _counted()
        stats.add(view["id"], impressions=3, seen=2)
        with model.meta.engine.begin() as connection:
            connection.execute(stats.table.insert().values(
                view_id=view["id"], day=datetime.date.today() - datetime.timedelta(days=500),
                impressions=9))
        result = cli.invoke(ckan, ["webpageviewplus", "purge-stats"])
        assert result.exit_code == 0, result.output
        assert "older than 365 days deleted" in result.output
        result = cli.invoke(ckan, ["webpageviewplus", "stats-summary", "--limit", "1000"])
        assert result.exit_code == 0, result.output
        line = [l for l in result.output.splitlines() if l.startswith(view["id"])]
        assert line == ["{}\t3\t2\t0\t0\t0".format(view["id"])]
