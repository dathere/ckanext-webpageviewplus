import pytest

from ckan.plugins import plugin_loaded
from ckan.plugins.toolkit import ValidationError, url_for
from ckan.tests import factories, helpers
from ckanext.webpageviewplus.plugin import WebpageviewplusPlugin, is_http_url


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.usefixtures("with_plugins")
def test_plugin_loaded():
    assert plugin_loaded("webpage_view_plus")


# non_clean_db (not clean_db) so a misconfigured run can't wipe a real database.
@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
class TestWebpageViewPlus:
    @pytest.mark.ckan_config("ckan.views.default_views", "")
    def test_view_shown_on_resource_page(self, app):
        dataset = factories.Dataset()
        resource = factories.Resource(
            package_id=dataset["id"], url="http://some.website.html"
        )
        resource_view = factories.ResourceView(
            resource_id=resource["id"],
            view_type="webpage_view_plus",
            page_url="http://some.other.website.html",
        )

        url = url_for(
            "{}_resource.read".format(dataset["type"]),
            id=dataset["name"],
            resource_id=resource["id"],
        )
        response = app.get(url)

        assert resource_view["page_url"] in response

    @pytest.mark.parametrize(
        "fmt,url,expected",
        [
            ("HTML", "http://example.com/data", True),
            ("", "http://example.com/page.htm", True),
            ("CSV", "http://example.com/data.csv", False),
            ("", "https://example.com/page.html?download=1", True),
            ("", "https://example.com/page.HTML#top", True),
            ("", "https://example.com/html", False),
            ("", "https://example.com/data.csv?f=x.html", False),
            (None, "http://example.com/page.htm", True),
        ],
    )
    def test_can_view(self, fmt, url, expected):
        plugin = WebpageviewplusPlugin()
        assert plugin.can_view({"resource": {"format": fmt, "url": url}}) is expected

    @pytest.mark.parametrize(
        "page_url",
        ["javascript:alert(document.domain)", "data:text/html,<b>x</b>", "/relative/page.html"],
    )
    def test_non_http_page_url_rejected_on_save(self, page_url):
        resource = factories.Resource(url="http://some.website.html")
        with pytest.raises(ValidationError) as e:
            factories.ResourceView(
                resource_id=resource["id"],
                view_type="webpage_view_plus",
                page_url=page_url,
            )
        assert "page_url" in e.value.error_dict


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://example.com/page.html", True),
        ("HTTPS://example.com/page.html", True),
        ("javascript:alert(1)", False),
        (" javascript:alert(1)", False),
        ("JaVaScRiPt:alert(1)", False),
        ("java\tscript:alert(1)", False),
        ("data:text/html,x", False),
        ("//example.com/page.html", False),
        ("/page.html", False),
        ("https://", False),
        ("", False),
        (None, False),
        # Hosts this parser and a browser's could read differently.
        ("https://%63kan.example.org/p", False),
        ("https://ckan.example.org\\@evil.com/p", False),
        ("https:\\\\ckan.example.org/p", False),
        ("https://user@ckan.example.org/p", False),
        ("https://\uff43kan.example.org/p", False),
        ("http://0x7f.1/p", False),
        ("http://2130706433/p", False),
        ("http://127.1/p", False),
        ("https://ckan.example.org:99999/p", False),
        ("https://ckan example.org/p", False),
        ("http://127.0.0.1:5000/p", True),
        ("http://[::1]/p", True),
        ("https://ckan.example.org./p", True),
        ("https://xn--bcher-kva.example/p", True),
    ],
)
def test_is_http_url(url, expected):
    assert is_http_url(url) is expected


@pytest.mark.parametrize(
    "page_url,resource_url,expected",
    [
        ("https://example.com/a.html", "javascript:alert(1)", "https://example.com/a.html"),
        ("", "http://example.com/b.html", "http://example.com/b.html"),
        ("", "javascript:alert(1)", None),
        ("javascript:alert(1)", "http://example.com/b.html", None),
    ],
)
@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.usefixtures("with_plugins")
def test_frame_url_checked_at_render(page_url, resource_url, expected):
    variables = WebpageviewplusPlugin().setup_template_variables(
        {},
        {
            "resource_view": {"page_url": page_url},
            "resource": {"url": resource_url},
        },
    )
    frame = variables["frame"]
    assert (frame.src if frame else None) == expected


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
def test_new_view_form_renders(app):
    sysadmin = factories.SysadminWithToken()
    dataset = factories.Dataset()
    resource = factories.Resource(
        package_id=dataset["id"], url="http://some.website.html"
    )
    url = url_for(
        "{}_resource.edit_view".format(dataset["type"]),
        id=dataset["name"],
        resource_id=resource["id"],
        view_type="webpage_view_plus",
    )
    response = app.get(url, headers={"Authorization": sysadmin["token"]})
    assert response.status_code == 200
    assert 'name="page_url"' in response


@pytest.fixture
def content_origin(monkeypatch):
    """Simulate phase 3, where uploads are served from their own origin."""
    from ckanext.webpageviewplus import settings

    monkeypatch.setattr(
        settings, "content_origin", lambda: "https://usercontent.example.net"
    )
    monkeypatch.setattr(settings, "signing_secret", lambda: "s" * 32)


def _view(user, resource_id, **fields):
    return helpers.call_action(
        "resource_view_create",
        context={"user": user["name"], "ignore_auth": True},
        resource_id=resource_id,
        view_type="webpage_view_plus",
        title="Report",
        **fields,
    )


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
class TestViewValidation:
    def test_default_preset_filled_in(self):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        view = _view(factories.Sysadmin(), resource["id"])
        assert view["preset"] == "interactive"

    def test_preset_must_be_allowed(self):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        with pytest.raises(ValidationError) as e:
            _view(factories.Sysadmin(), resource["id"], preset="bogus")
        assert "preset" in e.value.error_dict

    @pytest.mark.ckan_config("ckanext.webpageviewplus.presets", "strict interactive")
    def test_preset_outside_site_list_refused(self):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        with pytest.raises(ValidationError) as e:
            _view(factories.Sysadmin(), resource["id"], preset="external")
        assert "preset" in e.value.error_dict

    def test_external_refused_for_upload_on_own_origin(self, content_origin):
        resource = factories.Resource(url_type="upload", url="report.html")
        with pytest.raises(ValidationError) as e:
            _view(factories.Sysadmin(), resource["id"], preset="external")
        assert "preset" in e.value.error_dict

    def test_external_refused_for_page_on_site_url(self, ckan_config, content_origin):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        with pytest.raises(ValidationError) as e:
            _view(
                factories.Sysadmin(),
                resource["id"],
                preset="external",
                page_url=ckan_config["ckan.site_url"] + "/some/page.html",
            )
        assert "preset" in e.value.error_dict

    @pytest.mark.ckan_config("ckanext.webpageviewplus.site_aliases", "https://ckan-alias.example.net")
    def test_external_refused_for_alias_host(self, content_origin):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        with pytest.raises(ValidationError) as e:
            _view(
                factories.Sysadmin(),
                resource["id"],
                preset="external",
                page_url="http://ckan-alias.example.net:8080/page.html",
            )
        assert "preset" in e.value.error_dict

    def test_scripts_and_same_origin_refused_on_own_origin(self):
        resource = factories.Resource(url_type="upload", url="report.html")
        with pytest.raises(ValidationError) as e:
            _view(
                factories.Sysadmin(),
                resource["id"],
                sandbox="allow-scripts allow-same-origin",
            )
        assert "sandbox" in e.value.error_dict

    def test_data_schematic_allowed_on_upload(self):
        # Degrades to interactive, which keeps the page off CKAN's origin.
        resource = factories.Resource(url_type="upload", url="schematic.html")
        view = _view(factories.Sysadmin(), resource["id"], preset="data-schematic")
        assert view["preset"] == "data-schematic"

    def test_external_saves_without_content_origin(self):
        # Allowed, but renders as interactive (see test_frame).
        resource = factories.Resource(url="https://pages.example.com/a.html")
        view = _view(factories.Sysadmin(), resource["id"], preset="external")
        assert view["preset"] == "external"

    def test_external_refused_for_uploads(self):
        resource = factories.Resource(url_type="upload", url="report.html")
        with pytest.raises(ValidationError) as e:
            _view(factories.Sysadmin(), resource["id"], preset="external")
        assert "preset" in e.value.error_dict

    def test_data_schematic_with_same_origin_allowed_on_upload_with_content_origin(
        self, content_origin
    ):
        resource = factories.Resource(url_type="upload", url="schematic.html")
        view = _view(factories.Sysadmin(), resource["id"], preset="data-schematic")
        assert view["preset"] == "data-schematic"

    def test_scripts_and_same_origin_refused_for_any_page_without_content_origin(self):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        with pytest.raises(ValidationError) as e:
            _view(
                factories.Sysadmin(),
                resource["id"],
                sandbox="allow-scripts allow-same-origin",
            )
        assert "sandbox" in e.value.error_dict

    def test_external_allowed_for_third_party(self):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        view = _view(factories.Sysadmin(), resource["id"], preset="external")
        assert view["preset"] == "external"

    @pytest.mark.ckan_config("ckanext.webpageviewplus.allowed_hosts", "good.example.com")
    def test_allowed_hosts(self):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        _view(factories.Sysadmin(), resource["id"], page_url="https://good.example.com/x")
        with pytest.raises(ValidationError) as e:
            _view(factories.Sysadmin(), resource["id"], page_url="https://bad.example.com/x")
        assert "page_url" in e.value.error_dict

    @pytest.mark.parametrize(
        "mode,value",
        [("fixed", "tall"), ("viewport", "200"), ("aspect", "wide"), ("sideways", "1")],
    )
    def test_bad_height(self, mode, value):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        with pytest.raises(ValidationError):
            _view(factories.Sysadmin(), resource["id"], height_mode=mode, height_value=value)

    def test_sysadmin_sets_advanced_fields(self):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        view = _view(
            factories.Sysadmin(),
            resource["id"],
            sandbox="allow-scripts  allow-forms",
            allow="fullscreen, clipboard-write",
            referrer_policy="no-referrer",
        )
        assert view["sandbox"] == "allow-scripts allow-forms"
        assert view["allow"] == "fullscreen; clipboard-write"
        assert view["referrer_policy"] == "no-referrer"

    def test_denied_feature_refused(self):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        with pytest.raises(ValidationError) as e:
            _view(factories.Sysadmin(), resource["id"], allow="browsing-topics")
        assert "allow" in e.value.error_dict

    def test_editor_advanced_fields_dropped_on_create(self):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        view = _view(
            factories.User(), resource["id"], sandbox="allow-scripts allow-same-origin"
        )
        assert "sandbox" not in view

    @pytest.mark.ckan_config("ckanext.webpageviewplus.editor_can_loosen", "true")
    def test_editor_can_loosen(self):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        view = _view(factories.User(), resource["id"], sandbox="allow-scripts allow-forms")
        assert view["sandbox"] == "allow-scripts allow-forms"

    def test_editor_resave_keeps_sysadmin_settings(self):
        resource = factories.Resource(url="https://pages.example.com/a.html")
        view = _view(factories.Sysadmin(), resource["id"], sandbox="allow-scripts allow-forms")
        updated = helpers.call_action(
            "resource_view_update",
            context={"user": factories.User()["name"], "ignore_auth": True},
            id=view["id"],
            resource_id=resource["id"],
            view_type="webpage_view_plus",
            title="Renamed",
            preset="interactive",
        )
        assert updated["title"] == "Renamed"
        assert updated["sandbox"] == "allow-scripts allow-forms"


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
def test_rendered_iframe_attributes(app):
    dataset = factories.Dataset()
    resource = factories.Resource(
        package_id=dataset["id"], url="https://pages.example.com/a.html"
    )
    _view(
        factories.Sysadmin(),
        resource["id"],
        preset="strict",
        description="Quarterly report",
    )
    url = url_for(
        "{}_resource.read".format(dataset["type"]),
        id=dataset["name"],
        resource_id=resource["id"],
    )
    body = app.get(url).body
    assert 'sandbox=""' in body
    assert 'title="Report"' in body
    assert 'loading="lazy"' in body
    assert 'referrerpolicy="strict-origin-when-cross-origin"' in body
    assert "browsing-topics &#39;none&#39;" in body or "browsing-topics 'none'" in body
    assert "aria-describedby=" in body
    assert "frameborder" not in body
    assert 'data-module="data-viewer"' not in body
    assert 'data-frame-origin="https://pages.example.com"' in body
    assert 'data-opaque="true"' in body
    assert 'data-min-height="400"' in body
    assert "webpageviewplus.js" in body or "webpageviewplus-js" in body or "webpageviewplus/" in body


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.ckan_config("ckanext.webpageviewplus.presets", "strict")
@pytest.mark.ckan_config("ckanext.webpageviewplus.default_preset", "strict")
@pytest.mark.usefixtures("with_plugins")
def test_preset_removed_from_site_list_renders_as_default():
    variables = WebpageviewplusPlugin().setup_template_variables(
        {},
        {
            "resource_view": {"preset": "external"},
            "resource": {"url": "https://pages.example.com/a.html"},
        },
    )
    assert variables["frame"].preset == "strict"
    assert variables["frame"].sandbox == ""


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.ckan_config("ckanext.webpageviewplus.site_aliases", "::1 ckan-alias.example.net")
@pytest.mark.ckan_config("ckanext.webpageviewplus.allowed_hosts", "::1")
@pytest.mark.usefixtures("with_plugins")
def test_ipv6_hosts_in_settings_are_canonical():
    from ckanext.webpageviewplus import settings

    assert "[::1]" in settings.ckan_hosts()
    assert settings.allowed_hosts() == ["[::1]"]


@pytest.mark.parametrize("name", ["site_aliases", "allowed_hosts"])
def test_invalid_host_setting_stops_startup(name, monkeypatch):
    from ckan.exceptions import CkanConfigurationException
    from ckanext.webpageviewplus import settings

    values = {
        "presets": "strict interactive",
        "default_preset": "strict",
        "referrer_policy": "no-referrer",
        "min_height": 400,
        "max_height": 4000,
        "site_aliases": "",
        "allowed_hosts": "",
        name: "%63kan.example.org",
    }
    monkeypatch.setattr(settings, "_get", lambda key: values.get(key))
    with pytest.raises(CkanConfigurationException, match=name):
        settings.check()


def _render(app, view_fields, resource_url="https://pages.example.com/a.html"):
    dataset = factories.Dataset()
    resource = factories.Resource(package_id=dataset["id"], url=resource_url)
    _view(factories.Sysadmin(), resource["id"], **view_fields)
    return app.get(
        url_for("{}_resource.read".format(dataset["type"]), id=dataset["name"],
                resource_id=resource["id"])
    ).body


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
class TestControlBar:
    def test_shown_by_default(self, app):
        body = _render(app, {})
        assert 'role="toolbar"' in body
        assert 'href="https://pages.example.com/a.html" target="_blank" rel="noopener noreferrer"' in body
        assert 'data-webpageviewplus-action="fullscreen"' in body
        assert 'data-reload-url="https://pages.example.com/a.html"' in body

    def test_hidden_per_view(self, app):
        assert 'role="toolbar"' not in _render(app, {"hide_controls": "true"})

    @pytest.mark.ckan_config("ckanext.webpageviewplus.controls", "open")
    def test_site_chooses_buttons(self, app):
        body = _render(app, {})
        assert "Open in new tab" in body
        assert 'data-webpageviewplus-action="fullscreen"' not in body
        assert 'data-webpageviewplus-action="reload"' not in body

    @pytest.mark.ckan_config("ckanext.webpageviewplus.controls", "")
    def test_site_can_hide_all(self, app):
        assert 'role="toolbar"' not in _render(app, {})


def test_unknown_control_stops_startup(monkeypatch):
    from ckan.exceptions import CkanConfigurationException
    from ckanext.webpageviewplus import settings

    real_get = settings._get
    monkeypatch.setattr(
        settings, "_get", lambda key: "open zoom" if key == "controls" else real_get(key)
    )
    with pytest.raises(CkanConfigurationException, match="controls"):
        settings.check()
