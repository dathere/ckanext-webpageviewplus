import pytest

from ckan.cli.cli import ckan
from ckan.tests import factories, helpers

from ckanext.webpageviewplus import cli as wvp_cli


def _stock_view(resource, **fields):
    return factories.ResourceView(
        resource_id=resource["id"], view_type="webpage_view", **fields
    )


def _show(view):
    return helpers.call_action("resource_view_show", id=view["id"])


@pytest.mark.ckan_config("ckan.plugins", "webpage_view webpage_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
class TestMigrate:
    def test_dry_run_changes_nothing(self, cli):
        view = _stock_view(factories.Resource(url="https://pages.example.com/a.html"))
        result = cli.invoke(ckan, ["webpageviewplus", "migrate", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert view["id"] in result.output
        assert _show(view)["view_type"] == "webpage_view"

    def test_migrates_keeping_fields(self, cli):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        view = _stock_view(
            resource,
            title="Report",
            description="Quarterly",
            page_url="https://other.example.com/b.html",
        )
        result = cli.invoke(ckan, ["webpageviewplus", "migrate"])
        assert result.exit_code == 0, result.output
        migrated = _show(view)
        assert migrated["view_type"] == "webpage_view_plus"
        assert migrated["id"] == view["id"]
        assert migrated["title"] == "Report"
        assert migrated["description"] == "Quarterly"
        assert migrated["page_url"] == "https://other.example.com/b.html"
        assert migrated["preset"] == "interactive"

    def test_preset_option(self, cli):
        view = _stock_view(factories.Resource(url="https://pages.example.com/a.html"))
        result = cli.invoke(ckan, ["webpageviewplus", "migrate", "--preset", "external"])
        assert result.exit_code == 0, result.output
        assert _show(view)["preset"] == "external"

    def test_unsafe_preset_on_upload_falls_back_to_default(self, cli):
        view = _stock_view(factories.Resource(url_type="upload", url="a.html"))
        result = cli.invoke(ckan, ["webpageviewplus", "migrate", "--preset", "external"])
        assert result.exit_code == 0, result.output
        assert _show(view)["preset"] == "interactive"

    @pytest.mark.ckan_config("ckanext.webpageviewplus.presets", "strict external")
    @pytest.mark.ckan_config("ckanext.webpageviewplus.default_preset", "external")
    def test_unsafe_preset_on_upload_becomes_strict_when_nothing_else_fits(self, cli):
        view = _stock_view(factories.Resource(url_type="upload", url="a.html"))
        result = cli.invoke(ckan, ["webpageviewplus", "migrate"])
        assert result.exit_code == 0, result.output
        assert _show(view)["preset"] == "strict"

    def test_schematic_detected(self, cli, monkeypatch):
        monkeypatch.setattr(wvp_cli, "looks_like_schematic", lambda resource: True)
        view = _stock_view(factories.Resource(url_type="upload", url="s.html"))
        result = cli.invoke(ckan, ["webpageviewplus", "migrate"])
        assert result.exit_code == 0, result.output
        assert _show(view)["preset"] == "data-schematic"

    @pytest.mark.ckan_config("ckanext.webpageviewplus.presets", "strict")
    @pytest.mark.ckan_config("ckanext.webpageviewplus.default_preset", "strict")
    def test_schematic_not_chosen_when_not_allowed(self, cli, monkeypatch):
        monkeypatch.setattr(wvp_cli, "looks_like_schematic", lambda resource: True)
        view = _stock_view(factories.Resource(url_type="upload", url="s.html"))
        result = cli.invoke(ckan, ["webpageviewplus", "migrate"])
        assert result.exit_code == 0, result.output
        assert _show(view)["preset"] == "strict"

    @pytest.mark.ckan_config("ckanext.webpageviewplus.presets", "external interactive")
    @pytest.mark.ckan_config("ckanext.webpageviewplus.default_preset", "external")
    def test_unsafe_default_falls_back_to_allowed_preset(self, cli):
        view = _stock_view(factories.Resource(url_type="upload", url="a.html"))
        result = cli.invoke(ckan, ["webpageviewplus", "migrate"])
        assert result.exit_code == 0, result.output
        assert _show(view)["preset"] == "interactive"

    @pytest.mark.ckan_config("ckanext.webpageviewplus.presets", "strict interactive")
    def test_preset_outside_site_list_refused(self, cli):
        result = cli.invoke(ckan, ["webpageviewplus", "migrate", "--preset", "external"])
        assert result.exit_code != 0


@pytest.mark.parametrize("offset", [0, wvp_cli.CHUNK - 5, wvp_cli.CHUNK + 3])
def test_file_contains_across_chunks(tmp_path, offset):
    path = tmp_path / "s.html"
    path.write_bytes(b"x" * offset + wvp_cli.SCHEMATIC_MARKER + b"y" * 10)
    assert wvp_cli.file_contains(str(path), wvp_cli.SCHEMATIC_MARKER)


def test_file_contains_respects_limit(tmp_path):
    path = tmp_path / "s.html"
    path.write_bytes(b"x" * (2 * wvp_cli.CHUNK) + wvp_cli.SCHEMATIC_MARKER)
    assert not wvp_cli.file_contains(
        str(path), wvp_cli.SCHEMATIC_MARKER, limit=wvp_cli.CHUNK
    )
