import io
from types import SimpleNamespace

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

    def test_one_dataset(self, cli):
        dataset = factories.Dataset()
        mine = _stock_view(factories.Resource(
            package_id=dataset["id"], url="https://pages.example.com/a.html"))
        other = _stock_view(factories.Resource(url="https://pages.example.com/b.html"))
        result = cli.invoke(ckan, ["webpageviewplus", "migrate", "--dataset", dataset["name"]])
        assert result.exit_code == 0, result.output
        assert _show(mine)["view_type"] == "webpage_view_plus"
        assert _show(other)["view_type"] == "webpage_view"
        result = cli.invoke(ckan, ["webpageviewplus", "migrate", "--dataset", "no-such"])
        assert result.exit_code != 0

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

    def test_resource_url_host_not_allowed_skipped(self, cli, monkeypatch):
        # No pdf_url: the view shows the resource's own URL, checked the same way.
        from ckanext.webpageviewplus import settings
        monkeypatch.setattr(settings, "allowed_hosts", lambda: ["files.example.org"])
        resource = factories.Resource(url="https://elsewhere.example.org/a.pdf", format="PDF")
        view_id = _pdfview_view(resource)
        result = cli.invoke(ckan, ["webpageviewplus", "migrate-pdf"])
        assert result.exit_code == 0, result.output
        assert "skipped" in result.output
        assert _view_row(view_id).view_type == "pdf_view"

    def test_unreadable_upload_skipped(self, cli, monkeypatch):
        # A cloud storage extension on CKAN 2.10/2.11: the converted view
        # couldn't read the file, while pdfview's (the browser) can.
        from ckanext.webpageviewplus import content
        monkeypatch.setattr(content, "_readable_uploader", lambda resource: None)
        resource = factories.Resource(url="a.pdf", url_type="upload", format="PDF")
        view_id = _pdfview_view(resource)
        result = cli.invoke(ckan, ["webpageviewplus", "migrate-pdf"])
        assert result.exit_code == 0, result.output
        assert "can't read" in result.output
        assert _view_row(view_id).view_type == "pdf_view"

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


@pytest.mark.ckan_config("ckan.plugins", "webpage_view webpage_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
class TestDoctor:
    @pytest.fixture(autouse=True)
    def tables(self, monkeypatch):
        import ckan.cli.db
        monkeypatch.setattr(ckan.cli.db, "current_revision", lambda plugin: "abc (head)")

    def _levels(self):
        return {level for level, _ in wvp_cli.diagnose()}

    def test_notes_without_content_origin(self):
        found = [(level, text) for level, text in wvp_cli.diagnose()
                 if text.startswith("No content origin")]
        assert [level for level, _ in found] == ["note"]
        assert "External included" in found[0][1]

    @pytest.mark.ckan_config("ckanext.webpageviewplus.harden_downloads", False)
    def test_warns_without_sandboxed_downloads(self):
        found = [level for level, text in wvp_cli.diagnose()
                 if text.startswith("harden_downloads is off")]
        assert found == ["warn"]

    @pytest.mark.usefixtures("content_origin")
    def test_isolated(self):
        texts = [text for level, text in wvp_cli.diagnose() if level == "ok"]
        assert any(text.startswith("Uploads are isolated") for text in texts)

    def test_tables_out_of_date_fail(self, monkeypatch):
        import ckan.cli.db
        monkeypatch.setattr(ckan.cli.db, "current_revision", lambda plugin: "abc")
        assert ("fail", "The plugin's tables are missing or out of date. Run: "
                "ckan db upgrade -p webpage_view_plus") in wvp_cli.diagnose()

    def test_core_views_are_counted(self):
        _stock_view(factories.Resource(url="https://x.example/"))
        assert any(level == "note" and "core Website views remain" in text
                   for level, text in wvp_cli.diagnose())

    def test_repo_views_without_a_token(self, monkeypatch):
        monkeypatch.setattr(wvp_cli, "_plugin_loaded", lambda name: name == "repo_view_plus")
        assert any(level == "note" and text.startswith("repo_view_plus is on without github_token")
                   for level, text in wvp_cli.diagnose())

    @pytest.mark.ckan_config("ckanext.webpageviewplus.github_token", "github_pat_x")
    def test_repo_views_with_a_token(self, monkeypatch):
        monkeypatch.setattr(wvp_cli, "_plugin_loaded", lambda name: name == "repo_view_plus")
        texts = [text for level, text in wvp_cli.diagnose()]
        assert "github_token is set: GitHub allows 5,000 requests an hour." in texts
        assert not any("github_token" in text and "without" in text for text in texts)

    def test_no_token_note_without_repo_views(self, monkeypatch):
        monkeypatch.setattr(wvp_cli, "_plugin_loaded", lambda name: False)
        assert not any("github_token" in text for _, text in wvp_cli.diagnose())

    def test_missing_bundle_fails(self, cli, monkeypatch):
        monkeypatch.setattr(wvp_cli, "asset_problems",
                            lambda site_url: ["https://x.example/a.js: HTTP 404"])
        result = cli.invoke(ckan, ["webpageviewplus", "doctor"])
        assert result.exit_code == 1
        assert "https://x.example/a.js: HTTP 404" in result.output

    def test_unreachable_site_is_only_a_note(self, cli, monkeypatch):
        import requests

        def unreachable(site_url):
            raise requests.ConnectionError()
        monkeypatch.setattr(wvp_cli, "asset_problems", unreachable)
        result = cli.invoke(ckan, ["webpageviewplus", "doctor",
                                   "--site-url", "http://nowhere.invalid"])
        assert result.exit_code == 0, result.output
        assert "Couldn't reach http://nowhere.invalid" in result.output

    def test_bundles_are_fetched_from_the_site(self, monkeypatch):
        import requests
        fetched = []

        class Response:
            status_code = 404
        # Only around the call: CKAN's own teardown talks to Solr by requests.
        with monkeypatch.context() as patched:
            patched.setattr(requests, "get",
                            lambda url, timeout: fetched.append(url) or Response())
            problems = wvp_cli.asset_problems("https://ckan.example/")
        assert len(problems) == len(fetched) == 2
        assert all(url.startswith("https://ckan.example/") and "/ckanext-webpageviewplus/" in url
                   for url in fetched)


SANDBOX = "sandbox allow-scripts allow-popups allow-forms"


class TestDownloadFinding:
    """doctor's check that downloads reach the machine still sandboxed."""

    @pytest.fixture(autouse=True)
    def tables(self, monkeypatch):
        import ckan.cli.db
        monkeypatch.setattr(ckan.cli.db, "current_revision", lambda plugin: "abc (head)")

    @pytest.fixture
    def answer(self, monkeypatch):
        import requests
        from requests.structures import CaseInsensitiveDict
        monkeypatch.setattr(wvp_cli, "_probe_upload", lambda: SimpleNamespace(
            id="r1", url="page.html", url_type="upload",
            package=SimpleNamespace(id="p1", type="dataset")))
        fetched = []

        def answer(status, **headers):
            class Response:
                status_code = status

                def close(self):
                    pass
            Response.headers = CaseInsensitiveDict(
                {k.replace("_", "-"): v for k, v in headers.items()})

            def get(url, **kw):
                fetched.append((url, kw))
                return Response()
            # Only around the call: CKAN's own teardown talks to Solr by requests.
            with monkeypatch.context() as patched:
                patched.setattr(requests, "get", get)
                return wvp_cli.download_finding("https://ckan.example/")
        answer.fetched = fetched
        return answer

    def test_sandboxed(self, answer):
        level, text = answer(200, Content_Type="text/html", X_Content_Type_Options="nosniff",
                             Content_Security_Policy=SANDBOX)
        assert level == "ok"
        url, kw = answer.fetched[0]
        assert url == "https://ckan.example/dataset/p1/resource/r1/download/page.html"
        assert kw["allow_redirects"] is False

    def test_one_of_several_policies_sandboxes(self, answer):
        level, _ = answer(200, Content_Type="text/html", X_Content_Type_Options="nosniff",
                          Content_Security_Policy="default-src 'self', " + SANDBOX)
        assert level == "ok"

    @pytest.mark.parametrize("headers, says", [
        ({"X_Content_Type_Options": "nosniff"}, "without Content-Security-Policy: sandbox"),
        ({"X_Content_Type_Options": "nosniff",
          "Content_Security_Policy": "default-src 'self'"}, "Content-Security-Policy: sandbox"),
        ({"X_Content_Type_Options": "nosniff",
          "Content_Security_Policy": SANDBOX + " allow-same-origin"}, "allow-same-origin"),
        ({"Content_Security_Policy": SANDBOX}, "X-Content-Type-Options: nosniff"),
    ])
    def test_stripped_or_loosened_fails(self, answer, headers, says):
        level, text = answer(200, Content_Type="text/html", **headers)
        assert level == "fail"
        assert says in text

    def test_redirect_off_ckan_host(self, answer):
        level, text = answer(302, Location="https://usercontent.example.net/x/page.html")
        assert level == "ok"
        assert "usercontent.example.net" in text

    def test_redirect_to_ckan_host_fails(self, answer, monkeypatch):
        from ckanext.webpageviewplus import settings
        monkeypatch.setattr(settings, "ckan_hosts", lambda: ["ckan.example"])
        level, _ = answer(302, Location="https://ckan.example/elsewhere/page.html")
        assert level == "fail"

    @pytest.mark.parametrize("status, headers", [
        (403, {}),
        (200, {"Content_Type": "text/plain"}),
    ])
    def test_cannot_judge_is_a_note(self, answer, status, headers):
        level, _ = answer(status, **headers)
        assert level == "note"

    def test_no_public_upload_is_a_note(self, monkeypatch):
        monkeypatch.setattr(wvp_cli, "_probe_upload", lambda: None)
        level, text = wvp_cli.download_finding("https://ckan.example/")
        assert level == "note"
        assert "curl" in text

    @pytest.mark.usefixtures("non_clean_db")
    def test_probe_is_a_public_page_upload(self):
        private = factories.Dataset(private=True, owner_org=factories.Organization()["id"])
        public = factories.Dataset()
        factories.Resource(package_id=private["id"], url_type="upload", url="a.html",
                           format="HTML")
        factories.Resource(package_id=public["id"], url_type="upload", url="b.csv",
                           format="CSV")
        factories.Resource(package_id=public["id"], url_type="upload", url="c.html",
                           format="HTML")
        found = wvp_cli._probe_upload()
        assert found is not None
        assert not found.package.private
        assert found.package.state == "active"
        assert found.url_type == "upload" and wvp_cli._active_content(found)

    @pytest.mark.ckan_config("ckanext.webpageviewplus.harden_downloads", "false")
    def test_skipped_with_harden_downloads_off(self, cli, monkeypatch):
        # diagnose() already warns; a "stripped header" failure would mislead.
        monkeypatch.setattr(wvp_cli, "asset_problems", lambda site_url: [])
        called = []
        monkeypatch.setattr(wvp_cli, "download_finding",
                            lambda site_url: called.append(site_url) or ("ok", "x"))
        result = cli.invoke(ckan, ["webpageviewplus", "doctor", "--site-url", "https://x"])
        assert result.exit_code == 0, result.output
        assert called == []

    def test_doctor_fails_on_a_stripped_header(self, cli, monkeypatch):
        monkeypatch.setattr(wvp_cli, "asset_problems", lambda site_url: [])
        monkeypatch.setattr(wvp_cli, "download_finding",
                            lambda site_url: ("fail", "stripped at " + site_url))
        result = cli.invoke(ckan, ["webpageviewplus", "doctor", "--site-url", "https://x"])
        assert result.exit_code == 1
        assert "stripped at https://x" in result.output
