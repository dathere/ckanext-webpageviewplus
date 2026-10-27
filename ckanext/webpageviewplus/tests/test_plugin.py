import pytest

from ckan.plugins import plugin_loaded
from ckan.plugins.toolkit import url_for
from ckan.tests import factories


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
        from ckanext.webpageviewplus.plugin import WebpageviewplusPlugin

        plugin = WebpageviewplusPlugin()
        assert plugin.can_view({"resource": {"format": fmt, "url": url}}) is expected
