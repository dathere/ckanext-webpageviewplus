import pytest

from ckan.plugins import plugin_loaded
from ckan.plugins.toolkit import ValidationError, url_for
from ckan.tests import factories
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
def test_frame_url_checked_at_render(page_url, resource_url, expected):
    variables = WebpageviewplusPlugin().setup_template_variables(
        {},
        {
            "resource_view": {"page_url": page_url},
            "resource": {"url": resource_url},
        },
    )
    assert variables["frame_url"] == expected
