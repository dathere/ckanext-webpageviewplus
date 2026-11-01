import io

import pytest
from werkzeug.datastructures import FileStorage

import ckan.model as model
from ckan.cli.cli import ckan
from ckan.tests import factories, helpers

from ckanext.webpageviewplus import cli as wvp_cli


@pytest.fixture
def content_origin(monkeypatch):
    """Simulate phase 3, where uploads are served from their own origin."""
    from ckanext.webpageviewplus import settings

    monkeypatch.setattr(
        settings, "content_origin", lambda: "https://usercontent.example.net"
    )
    monkeypatch.setattr(settings, "signing_secret", lambda: "s" * 32)


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

    def test_uploads_only(self, cli):
        upload_view = _stock_view(factories.Resource(url_type="upload", url="a.html"))
        link_view = _stock_view(factories.Resource(url="https://pages.example.com/a.html"))
        page_url_view = _stock_view(
            factories.Resource(url_type="upload", url="b.html"),
            page_url="https://pages.example.com/b.html",
        )
        result = cli.invoke(ckan, ["webpageviewplus", "migrate", "--uploads-only"])
        assert result.exit_code == 0, result.output
        assert _show(upload_view)["view_type"] == "webpage_view_plus"
        assert _show(link_view)["view_type"] == "webpage_view"
        assert _show(page_url_view)["view_type"] == "webpage_view"

    def test_preset_option(self, cli):
        view = _stock_view(factories.Resource(url="https://pages.example.com/a.html"))
        result = cli.invoke(ckan, ["webpageviewplus", "migrate", "--preset", "external"])
        assert result.exit_code == 0, result.output
        assert _show(view)["preset"] == "external"

    def test_unsafe_preset_on_upload_falls_back_to_default(self, cli, content_origin):
        view = _stock_view(factories.Resource(url_type="upload", url="a.html"))
        result = cli.invoke(ckan, ["webpageviewplus", "migrate", "--preset", "external"])
        assert result.exit_code == 0, result.output
        assert _show(view)["preset"] == "interactive"

    @pytest.mark.ckan_config("ckanext.webpageviewplus.presets", "strict external")
    @pytest.mark.ckan_config("ckanext.webpageviewplus.default_preset", "external")
    def test_unsafe_preset_on_upload_becomes_strict_when_nothing_else_fits(self, cli, content_origin):
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

    @pytest.mark.parametrize("storage", ["local_storage", "memory_storage"])
    def test_schematic_detected_in_a_real_upload(self, request, storage):
        # Reads the file itself, on disk or streamed (memory: CKAN 2.12+).
        # Called directly: the CLI reloads test.ini, losing the storage.
        request.getfixturevalue(storage)
        def upload(body):
            resource = factories.Resource(
                url="s.html", url_type="upload",
                upload=FileStorage(io.BytesIO(body), filename="s.html"))
            return model.Resource.get(resource["id"])
        assert wvp_cli.looks_like_schematic(
            upload(b'<script id="qsv-schematic-source">{}</script>'))
        assert not wvp_cli.looks_like_schematic(upload(b"<p>Not a schematic</p>"))

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
    def test_unsafe_default_falls_back_to_allowed_preset(self, cli, content_origin):
        view = _stock_view(factories.Resource(url_type="upload", url="a.html"))
        result = cli.invoke(ckan, ["webpageviewplus", "migrate"])
        assert result.exit_code == 0, result.output
        assert _show(view)["preset"] == "interactive"

    @pytest.mark.ckan_config("ckanext.webpageviewplus.presets", "external")
    @pytest.mark.ckan_config("ckanext.webpageviewplus.default_preset", "external")
    def test_view_skipped_when_no_allowed_preset_fits(self, cli):
        view = _stock_view(factories.Resource(url_type="upload", url="a.html"))
        result = cli.invoke(ckan, ["webpageviewplus", "migrate"])
        assert result.exit_code == 0, result.output
        assert "skipped" in result.output
        assert _show(view)["view_type"] == "webpage_view"

    @pytest.mark.ckan_config("ckanext.webpageviewplus.presets", "strict interactive")
    def test_preset_outside_site_list_refused(self, cli):
        result = cli.invoke(ckan, ["webpageviewplus", "migrate", "--preset", "external"])
        assert result.exit_code != 0


CHUNK = 1024


def _chunks(body, size=CHUNK):
    return (body[i:i + size] for i in range(0, len(body), size))


@pytest.mark.parametrize("offset", [0, CHUNK - 5, CHUNK + 3])
@pytest.mark.parametrize("size", [CHUNK, 7])  # 7: chunks shorter than the marker
def test_contains_across_chunks(offset, size):
    body = b"x" * offset + wvp_cli.SCHEMATIC_MARKER + b"y" * 10
    assert wvp_cli.contains(_chunks(body, size), wvp_cli.SCHEMATIC_MARKER)


def test_contains_respects_limit():
    body = b"x" * (2 * CHUNK) + wvp_cli.SCHEMATIC_MARKER
    assert not wvp_cli.contains(_chunks(body), wvp_cli.SCHEMATIC_MARKER, limit=CHUNK)
    # Cut mid-chunk too: the limit isn't rounded up to a whole chunk.
    body = b"x" * (CHUNK // 2) + wvp_cli.SCHEMATIC_MARKER
    assert not wvp_cli.contains(_chunks(body), wvp_cli.SCHEMATIC_MARKER, limit=CHUNK // 2)


def _pdfview_view(resource, title="PDF", **config):
    """A ckanext-pdfview view, as a site upgrading from it has them (that
    plugin isn't installed here, so the row is written directly)."""
    view = model.ResourceView(
        resource_id=resource["id"], view_type="pdf_view", title=title,
        description="From pdfview", order=0, config=config)
    model.Session.add(view)
    model.Session.commit()
    return view.id


def _view_row(view_id):
    model.Session.expire_all()
    return model.ResourceView.get(view_id)


@pytest.fixture
def pdf_site(monkeypatch, content_origin):
    """The CLI reloads CKAN from test.ini, so markers don't reach it: say
    pdf_view_plus is loaded (cli's own helper only: patching
    ckan.plugins.plugin_loaded would fool with_plugins' teardown)."""
    monkeypatch.setattr(wvp_cli, "_plugin_loaded", lambda name: True)


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus pdf_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "pdf_site")
class TestMigratePdf:
    def test_dry_run_changes_nothing(self, cli):
        view_id = _pdfview_view(factories.Resource(url="https://files.example.org/a.pdf", format="PDF"))
        result = cli.invoke(ckan, ["webpageviewplus", "migrate-pdf", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert view_id in result.output
        assert _view_row(view_id).view_type == "pdf_view"

    def test_migrates_keeping_fields(self, cli):
        resource = factories.Resource(url="https://files.example.org/a.pdf", format="PDF")
        plain = _pdfview_view(resource, title="Report")
        linked = _pdfview_view(resource, title="Other", pdf_url="https://b.example.org/b.pdf")
        result = cli.invoke(ckan, ["webpageviewplus", "migrate-pdf"])
        assert result.exit_code == 0, result.output
        shown = helpers.call_action("resource_view_show", id=plain)
        assert shown["view_type"] == "pdf_view_plus"
        assert (shown["title"], shown["description"]) == ("Report", "From pdfview")
        assert "pdf_url" not in shown
        assert helpers.call_action("resource_view_show", id=linked)["pdf_url"] == "https://b.example.org/b.pdf"

    def test_bad_pdf_url_skipped(self, cli):
        resource = factories.Resource(url="https://files.example.org/a.pdf", format="PDF")
        view_id = _pdfview_view(resource, pdf_url="javascript:alert(1)")
        result = cli.invoke(ckan, ["webpageviewplus", "migrate-pdf"])
        assert result.exit_code == 0, result.output
        assert "skipped" in result.output
        assert _view_row(view_id).view_type == "pdf_view"

    def test_host_not_allowed_skipped(self, cli, monkeypatch):
        from ckanext.webpageviewplus import settings
        monkeypatch.setattr(settings, "allowed_hosts", lambda: ["files.example.org"])
        resource = factories.Resource(url="https://files.example.org/a.pdf", format="PDF")
        kept = _pdfview_view(resource, pdf_url="https://elsewhere.example.org/b.pdf")
        moved = _pdfview_view(resource, pdf_url="https://files.example.org/c.pdf")
        cli.invoke(ckan, ["webpageviewplus", "migrate-pdf"])
        assert _view_row(kept).view_type == "pdf_view"
        assert _view_row(moved).view_type == "pdf_view_plus"

    def test_needs_content_origin(self, cli, monkeypatch):
        from ckanext.webpageviewplus import settings
        monkeypatch.setattr(settings, "isolating_content_origin", lambda: None)
        result = cli.invoke(ckan, ["webpageviewplus", "migrate-pdf"])
        assert result.exit_code != 0
        assert "content_origin" in result.output


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
def test_migrate_pdf_needs_the_plugin(cli):
    result = cli.invoke(ckan, ["webpageviewplus", "migrate-pdf"])
    assert result.exit_code != 0
    assert "pdf_view_plus" in result.output
