from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlsplit

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from ckan.common import CKANConfig
from ckan.types import Context

ignore_empty = toolkit.get_validator("ignore_empty")
unicode_safe = toolkit.get_validator("unicode_safe")

ALLOWED_SCHEMES = ("http", "https")


def is_http_url(url: Any) -> bool:
    """True only for absolute http(s) URLs with a host.

    Anything else (``javascript:``, ``data:``, relative or scheme-less URLs,
    URLs with leading whitespace or control characters) is refused, because an
    iframe ``src`` with a ``javascript:`` URL runs in CKAN's own origin.
    """
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in ALLOWED_SCHEMES and bool(parts.netloc)


def http_url_validator(value: Any, context: Context) -> Any:
    if value and not is_http_url(value):
        raise toolkit.Invalid(
            toolkit._("Web Page url must be an http:// or https:// URL")
        )
    return value


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
            "schema": {
                "page_url": [ignore_empty, unicode_safe, http_url_validator]
            },
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

    def setup_template_variables(
        self, context: Context, data_dict: dict[str, Any]
    ) -> dict[str, Any]:
        # Checked again at render time: views saved before validation existed,
        # and the resource URL fallback, never went through the form.
        url = data_dict["resource_view"].get("page_url") or data_dict[
            "resource"
        ].get("url")
        frame_url: Optional[str] = url if is_http_url(url) else None
        return {"frame_url": frame_url}

    def view_template(self, context: Context, data_dict: dict[str, Any]) -> str:
        return "webpageviewplus/view.html"

    def form_template(self, context: Context, data_dict: dict[str, Any]) -> str:
        return "webpageviewplus/form.html"
