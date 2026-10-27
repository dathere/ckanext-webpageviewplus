from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from ckan.common import CKANConfig
from ckan.types import Context

from ckanext.webpageviewplus import (
    cli,
    content,
    downloads,
    frame,
    settings,
    validators as v,
)
from ckanext.webpageviewplus.frame import is_http_url  # noqa: F401 (re-export)

ignore_empty = toolkit.get_validator("ignore_empty")
unicode_safe = toolkit.get_validator("unicode_safe")


@toolkit.blanket.config_declarations
class WebpageviewplusPlugin(plugins.SingletonPlugin):
    """Resource view that embeds a web page in an <iframe>, with a security
    preset per view. Runs beside CKAN core's ``webpage_view``."""

    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IConfigurable)
    plugins.implements(plugins.IClick)
    plugins.implements(plugins.IBlueprint)
    plugins.implements(plugins.IMiddleware, inherit=True)
    plugins.implements(plugins.IResourceView, inherit=True)

    # IConfigurer

    def update_config(self, config_: CKANConfig):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("assets", "webpageviewplus")

    # IConfigurable

    def configure(self, config_: CKANConfig):
        settings.check()

    # IBlueprint

    def get_blueprint(self):
        # The content route is always registered; it answers only on the
        # content origin's host, and only when one is configured.
        return [downloads.hardening, content.content]

    # IMiddleware

    def make_middleware(self, app: Any, config: CKANConfig) -> Any:
        app.wsgi_app = content.ContentHostMiddleware(app.wsgi_app)
        return app

    # IClick

    def get_commands(self):
        return cli.get_commands()

    # IResourceView

    def info(self) -> dict[str, Any]:
        return {
            "name": "webpage_view_plus",
            "title": toolkit._("Website (Plus)"),
            "schema": {
                "page_url": [ignore_empty, unicode_safe, v.http_url_validator],
                "preset": [unicode_safe, v.preset_validator],
                "height_mode": [ignore_empty, unicode_safe, v.height_mode_validator],
                "height_value": [ignore_empty, unicode_safe],
                "sandbox": [ignore_empty, unicode_safe, v.sandbox_validator],
                "allow": [ignore_empty, unicode_safe, v.allow_validator],
                "referrer_policy": [
                    ignore_empty, unicode_safe, v.referrer_policy_validator
                ],
                "__after": [v.view_after_validator],
            },
            "iframed": False,
            "icon": "link",
            "always_available": True,
            "default_title": toolkit._("Website"),
        }

    def can_view(self, data_dict: dict[str, Any]) -> bool:
        resource = data_dict["resource"]
        formats = settings.formats()
        if (resource.get("format") or "").lower() in formats:
            return True
        try:
            path = urlsplit(resource.get("url") or "").path
        except ValueError:
            return False
        _, dot, ext = path.rpartition(".")
        return bool(dot) and ext.lower() in formats

    def setup_template_variables(
        self, context: Context, data_dict: dict[str, Any]
    ) -> dict[str, Any]:
        # Everything is checked again at render time: views saved before
        # validation existed, previews, and the resource URL fallback never
        # went through the form. resource_view is None on the "new view" form.
        resource_view = data_dict.get("resource_view") or {}
        resource = data_dict["resource"]
        page_url = resource_view.get("page_url")
        url = page_url or resource.get("url")

        form_vars = {
            "presets": settings.allowed_presets(),
            "default_preset": settings.default_preset(),
            "height_modes": frame.HEIGHT_MODES,
            "referrer_policies": frame.REFERRER_POLICIES,
            "can_set_advanced": v.can_set_advanced(context),
        }

        if not is_http_url(url):
            return dict(form_vars, frame=None, frame_error="scheme")

        ckan_hosts = settings.ckan_hosts()
        is_upload = not page_url and resource.get("url_type") == "upload"
        if is_upload and content.serves(resource):
            package_id = (data_dict.get("package") or {}).get("id") or resource.get(
                "package_id"
            )
            url = content.content_url(
                resource, private=not content.is_public(package_id)
            )
        allowed_hosts = settings.allowed_hosts()
        if (
            page_url
            and allowed_hosts
            and not frame.is_ckan_host(page_url, ckan_hosts)
            and frame.url_host(page_url) not in allowed_hosts
        ):
            return dict(form_vars, frame=None, frame_error="host")

        # A preset the site has since removed from its list falls back to the
        # default, so narrowing the list applies to existing views too.
        preset = resource_view.get("preset")
        if preset not in settings.allowed_presets():
            preset = settings.default_preset()

        built = frame.build_frame(
            url,
            preset,
            ckan_hosts=ckan_hosts,
            is_upload=is_upload,
            content_origin=settings.isolating_content_origin(),
            sandbox_override=resource_view.get("sandbox") or "",
            allow_override=resource_view.get("allow") or "",
            referrer_policy=(
                resource_view.get("referrer_policy") or settings.referrer_policy()
            ),
            height_mode=resource_view.get("height_mode") or "",
            height_value=resource_view.get("height_value") or "",
            min_height=settings.min_height(),
            max_height=settings.max_height(),
            title=resource_view.get("title") or toolkit._("Embedded web page"),
        )
        return dict(form_vars, frame=built, frame_error=None)

    def view_template(self, context: Context, data_dict: dict[str, Any]) -> str:
        return "webpageviewplus/view.html"

    def form_template(self, context: Context, data_dict: dict[str, Any]) -> str:
        return "webpageviewplus/form.html"
