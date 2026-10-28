"""Views whose page can't be shown: the stored check, the card and
`ckan webpageviewplus check-views`."""
import json

import pytest

from ckan.cli.cli import ckan
from ckan.plugins.toolkit import url_for
from ckan.tests import factories, helpers

from ckanext.webpageviewplus import frameability as fa
from ckanext.webpageviewplus import health, stats

PAGE = "https://pages.example.com/a.html"
BLOCKED = fa.Probe(fa.BLOCKED, "X-Frame-Options: DENY", "", 200)
FINE = fa.Probe(fa.FRAMEABLE, "", "", 200)
REFUSED = "pages.example.com doesn't allow its pages to be shown inside other sites."
MIXED = "browsers block those inside https:// sites"


def _view(resource_id, **fields):
    return helpers.call_action(
        "resource_view_create",
        context={"user": factories.Sysadmin()["name"], "ignore_auth": True},
        resource_id=resource_id,
        view_type="webpage_view_plus",
        title="Report",
        **fields,
    )


@pytest.fixture
def probes(monkeypatch):
    """Every page fetch, faked: URL -> Probe (default FINE). Records calls."""
    answers = {}
    calls = []

    def probe(url, ckan, deadline=None):
        calls.append(url)
        return answers.get(url, FINE)

    monkeypatch.setattr(fa, "probe", probe)
    answers["calls"] = calls
    return answers


def _page(app, page_url=PAGE, view_fields=None, **resource_fields):
    dataset = factories.Dataset()
    resource_fields.setdefault("url", "https://other.example.com/data.csv")
    resource = factories.Resource(package_id=dataset["id"], **resource_fields)
    view = _view(resource["id"], page_url=page_url, **(view_fields or {}))
    body = app.get(
        url_for("{}_resource.read".format(dataset["type"]), id=dataset["name"],
                resource_id=resource["id"])
    ).body
    return view, body


@pytest.mark.parametrize(
    "site,url,mixed",
    [
        ("https://ckan.example.org", "http://pages.example.com/", True),
        ("https://ckan.example.org", "https://pages.example.com/", False),
        ("http://ckan.example.org", "http://pages.example.com/", False),
        ("https://ckan.example.org", "http://localhost:8000/", False),
        ("https://ckan.example.org", "http://127.0.0.1/", False),
        ("https://ckan.example.org", "http://app.localhost/", False),
    ],
)
def test_mixed_content(monkeypatch, site, url, mixed):
    monkeypatch.setattr(health.settings, "site_url", lambda: site)
    assert health.mixed_content(url) is mixed


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "counts_table")
class TestRefusedCard:
    def test_not_checked_on_save_by_default(self, app, probes):
        view, body = _page(app)
        assert probes["calls"] == []
        assert health.stored(view["id"], PAGE) is None
        assert "<iframe" in body

    @pytest.mark.ckan_config("ckanext.webpageviewplus.frameability_check", "true")
    def test_blocked_page_shows_card(self, app, probes):
        probes[PAGE] = BLOCKED
        view, body = _page(app)
        assert probes["calls"] == [PAGE]
        assert health.stored(view["id"], PAGE)["verdict"] == fa.BLOCKED
        assert REFUSED in body
        assert "<iframe" not in body
        assert 'href="{}" target="_blank"'.format(PAGE) in body

    @pytest.mark.ckan_config("ckanext.webpageviewplus.frameability_check", "true")
    def test_rendering_never_fetches(self, app, probes):
        _page(app)
        assert probes["calls"] == [PAGE]  # the save only

    @pytest.mark.ckan_config("ckanext.webpageviewplus.frameability_check", "true")
    @pytest.mark.parametrize("probe", [FINE, fa.Probe(fa.UNKNOWN, problem="unreachable")])
    def test_frameable_or_unknown_shows_frame(self, app, probes, probe):
        probes[PAGE] = probe
        _, body = _page(app)
        assert REFUSED not in body
        assert "<iframe" in body

    @pytest.mark.ckan_config("ckanext.webpageviewplus.frameability_check", "true")
    def test_result_only_counts_for_its_url(self, app, probes):
        probes[PAGE] = BLOCKED
        view, _ = _page(app)
        assert health.refused(view["id"], PAGE) == "blocked"
        assert health.refused(view["id"], "https://pages.example.com/new.html") is None
        health.store(view["id"], "https://pages.example.com/old.html", BLOCKED)
        assert health.refused(view["id"], PAGE) is None

    @pytest.mark.ckan_config("ckanext.webpageviewplus.frameability_check", "true")
    def test_card_beats_consent_and_save_data(self, app, probes):
        probes[PAGE] = BLOCKED
        _, body = _page(app, view_fields={"consent_gate": "true"})
        assert REFUSED in body
        assert "data-webpageviewplus-card" not in body

    @pytest.mark.ckan_config("ckanext.webpageviewplus.frameability_check", "true")
    @pytest.mark.ckan_config("ckanext.webpageviewplus.controls", "fullscreen reload")
    def test_no_link_without_open_control(self, app, probes):
        probes[PAGE] = BLOCKED
        _, body = _page(app)
        assert REFUSED in body
        assert 'href="{}" target="_blank"'.format(PAGE) not in body

    def test_stored_result_ignored_with_check_off(self, app, probes):
        view, _ = _page(app)
        health.store(view["id"], PAGE, BLOCKED)
        assert health.refused(view["id"], PAGE) is None

    @pytest.mark.ckan_config("ckanext.webpageviewplus.frameability_check", "true")
    def test_uploads_and_own_pages_never_checked(self, app, probes):
        _page(app, page_url="", url="https://test.ckan.net/other/page.html")
        _page(app, page_url="https://test.ckan.net/about")
        assert probes["calls"] == []

    @pytest.mark.ckan_config("ckanext.webpageviewplus.frameability_check", "true")
    def test_save_never_fails_on_a_check_error(self, app, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("boom")
        monkeypatch.setattr(fa, "probe", boom)
        view, body = _page(app)
        assert view["id"]
        assert "<iframe" in body

    def test_mixed_content_card(self, app, probes, ckan_config, monkeypatch):
        monkeypatch.setitem(ckan_config, "ckan.site_url", "https://test.ckan.net")
        _, body = _page(app, page_url="http://pages.example.com/a.html")
        assert MIXED in body
        assert "<iframe" not in body
        assert probes["calls"] == []

    @pytest.mark.ckan_config("ckanext.webpageviewplus.frameability_check", "true")
    def test_refused_view_not_counted(self, app, probes, ckan_config, monkeypatch):
        monkeypatch.setitem(ckan_config, "ckanext.webpageviewplus.view_counting", "true")
        monkeypatch.setitem(
            ckan_config, "ckanext.webpageviewplus.privacy_policy_url", "https://x.example/p")
        probes[PAGE] = BLOCKED
        view, body = _page(app, view_fields={"count_views": "true"})
        assert "data-count-url" not in body
        assert stats.totals(view["id"], 1)["totals"]["impressions"] == 0

    def test_row_deleted_with_view(self, app, probes):
        view, _ = _page(app)
        health.store(view["id"], PAGE, BLOCKED)
        helpers.call_action("resource_view_delete", id=view["id"])
        assert health.stored(view["id"], PAGE) is None


@pytest.mark.ckan_config("ckan.plugins", "webpage_view webpage_view_plus")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "counts_table")
class TestCheckViews:
    def _run(self, cli, dataset, *args):
        return cli.invoke(
            ckan, ["webpageviewplus", "check-views", "--dataset", dataset["name"], *args])

    def test_reports_problems_and_exits_1(self, cli, probes):
        dataset = factories.Dataset()
        r1 = factories.Resource(package_id=dataset["id"], url=PAGE)
        r2 = factories.Resource(package_id=dataset["id"], url="https://gone.example.com/")
        ok = factories.Resource(package_id=dataset["id"], url="https://fine.example.com/")
        blocked = _view(r1["id"])
        _view(r2["id"])
        _view(ok["id"])
        probes[PAGE] = BLOCKED
        probes["https://gone.example.com/"] = fa.Probe(fa.FRAMEABLE, "", "gone", 404)
        result = self._run(cli, dataset)
        assert result.exit_code == 1, result.output
        assert "refuses-embedding\twebpage_view_plus" in result.output
        assert "gone\twebpage_view_plus" in result.output
        assert "HTTP 404" in result.output
        assert "fine.example.com" not in result.output
        assert "3 view(s) checked, 2 with problems." in result.output
        # Saved, so the view shows the card (with the check on).
        assert health.stored(blocked["id"], PAGE)["verdict"] == fa.BLOCKED

    def test_all_and_json(self, cli, probes):
        dataset = factories.Dataset()
        r = factories.Resource(package_id=dataset["id"], url="https://fine.example.com/")
        view = _view(r["id"])
        result = self._run(cli, dataset, "--all", "--json")
        assert result.exit_code == 0, result.output
        out = result.output
        rows = json.loads(out[out.index("[\n"):out.rindex("]") + 1])
        assert [(row["view_id"], row["problem"]) for row in rows] == [(view["id"], "")]

    def test_fetches_each_url_once(self, cli, probes):
        dataset = factories.Dataset()
        for _ in range(3):
            _view(factories.Resource(package_id=dataset["id"], url=PAGE)["id"])
        probes["calls"].clear()
        self._run(cli, dataset)
        assert probes["calls"] == [PAGE]

    def test_no_store(self, cli, probes):
        dataset = factories.Dataset()
        view = _view(factories.Resource(package_id=dataset["id"], url=PAGE)["id"])
        probes[PAGE] = BLOCKED
        self._run(cli, dataset, "--no-store")
        assert health.stored(view["id"], PAGE) is None

    def test_core_views_too(self, cli, probes, monkeypatch):
        monkeypatch.setattr(health.settings, "site_url", lambda: "https://test.ckan.net")
        dataset = factories.Dataset()
        http = factories.Resource(package_id=dataset["id"], url="http://pages.example.com/")
        factories.ResourceView(resource_id=http["id"], view_type="webpage_view")
        result = self._run(cli, dataset)
        assert result.exit_code == 1
        assert "mixed-content\twebpage_view\t" in result.output
        assert probes["calls"] == []

    def test_private_hosts_not_problems(self, cli, probes):
        dataset = factories.Dataset()
        view = _view(factories.Resource(package_id=dataset["id"], url=PAGE)["id"])
        probes[PAGE] = fa.Probe(fa.UNKNOWN, problem="private")
        result = self._run(cli, dataset, "--all")
        assert result.exit_code == 0, result.output
        assert "not a public address" in result.output
        assert health.stored(view["id"], PAGE) is None
