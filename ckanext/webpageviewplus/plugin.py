from __future__ import annotations

from typing import Any

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from ckan.common import CKANConfig
from ckan.types import Context

ignore_empty = toolkit.get_validator("ignore_empty")
unicode_safe = toolkit.get_validator("unicode_safe")


class WebpageviewplusPlugin(plugins.SingletonPlugin):
    """Resource view that embeds a web page in an <iframe>.

    Starts as a copy of CKAN core's ``webpage_view`` under its own view type
    so both can be enabled side by side.
    """

    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IResourceView, inherit=True)

    # IConfigurer

    def update_config(self, config_: CKANConfig):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("assets", "webpageviewplus")

    # IResourceView

    def info(self) -> dict[str, Any]:
        return {
            "name": "webpage_view_plus",
            "title": toolkit._("Website (Plus)"),
            "schema": {"page_url": [ignore_empty, unicode_safe]},
            "iframed": False,
            "icon": "link",
            "always_available": True,
            "default_title": toolkit._("Website"),
        }

    def can_view(self, data_dict: dict[str, Any]) -> bool:
        resource = data_dict["resource"]
        return (
            resource.get("format", "").lower() in ["html", "htm"]
            or resource["url"].split(".")[-1] in ["html", "htm"]
        )

    def view_template(self, context: Context, data_dict: dict[str, Any]) -> str:
        return "webpageviewplus/view.html"

    def form_template(self, context: Context, data_dict: dict[str, Any]) -> str:
        return "webpageviewplus/form.html"
