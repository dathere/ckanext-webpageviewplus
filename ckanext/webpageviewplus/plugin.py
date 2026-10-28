from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from ckan.common import CKANConfig
from ckan.lib.plugins import DefaultTranslation
from ckan.types import Context
from flask import Blueprint, Response, g, has_request_context, request

from ckanext.webpageviewplus import (
    actions,
    cli,
    connections,
    content,
    downloads,
    events,
    health,
    stats,
    frame,
    settings,
    validators as v,
)
from ckanext.webpageviewplus.frame import is_http_url  # noqa: F401 (re-export)

ignore_empty = toolkit.get_validator("ignore_empty")
ignore_missing = toolkit.get_validator("ignore_missing")
boolean_validator = toolkit.get_validator("boolean_validator")
unicode_safe = toolkit.get_validator("unicode_safe")


# Pages that render a view vary by Save-Data (load on tap) and Sec-GPC (the
# consent card), so a cache mustn't serve one reader's card or frame to
# another.
vary = Blueprint("webpageviewplus_vary", __name__)


@vary.after_app_request
def vary_held_back(response: Response) -> Response:
    if g.get("webpageviewplus_vary_held_back"):
        response.vary.add("Save-Data")
        response.vary.add("Sec-GPC")
    return response


@toolkit.blanket.config_declarations
class WebpageviewplusPlugin(plugins.SingletonPlugin, DefaultTranslation):
    """Resource view that embeds a web page in an <iframe>, with a security
    preset per view. Runs beside CKAN core's ``webpage_view``."""

    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IConfigurable)
    plugins.implements(plugins.IClick)
    plugins.implements(plugins.IBlueprint)
    plugins.implements(plugins.IMiddleware, inherit=True)
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IAuthFunctions)
    plugins.implements(plugins.IResourceView, inherit=True)
    # Translations in ckanext/webpageviewplus/i18n (DefaultTranslation).
    plugins.implements(plugins.ITranslation)

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
        # The beacon carries no session state worth forging, and must keep
        # working on sites that turn CSRF protection on for extensions.
        try:
            from ckan.config.middleware.flask_app import csrf
            csrf.exempt(stats.beacon)
            # Browsers post CSP reports without a CSRF token.
            csrf.exempt(content.content)
        except (ImportError, AttributeError):
            pass
        return [downloads.hardening, content.content, vary, stats.beacon]

    # IActions

    def get_actions(self):
        return {
            "webpageviewplus_frameability": actions.webpageviewplus_frameability,
            "webpageviewplus_view_stats_show": actions.webpageviewplus_view_stats_show,
            "webpageviewplus_connections_show": actions.webpageviewplus_connections_show,
            "resource_view_create": actions.resource_view_create,
            "resource_view_update": actions.resource_view_update,
        }

    # IAuthFunctions

    def get_auth_functions(self):
        return {
            "webpageviewplus_frameability": actions.frameability_auth,
            "webpageviewplus_view_stats_show": actions.view_stats_auth,
            "webpageviewplus_connections_show": actions.connections_auth,
        }

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
                "hide_controls": [ignore_missing, boolean_validator],
                "consent_gate": [ignore_missing, boolean_validator],
                "count_views": [ignore_missing, boolean_validator],
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
            "frameability_check": settings.frameability_check(),
            "view_counting": settings.view_counting(),
            # Only the view's edit form shows them.
            "view_counts": (
                stats.totals_or_none(resource_view["id"], 30)
                if settings.view_counting() and resource_view.get("id")
                and has_request_context()
                and (request.endpoint or "").endswith("edit_view")
                else None
            ),
        }

        if not is_http_url(url):
            return dict(form_vars, frame=None, frame_error="scheme")

        ckan_hosts = settings.ckan_hosts()
        is_upload = not page_url and resource.get("url_type") == "upload"
        # Open in new tab and Reload use this URL. For an upload framed from
        # the content origin it's CKAN's own download URL, which re-checks
        # access and redirects to a freshly signed content URL: the frame's
        # own signed URL may have expired by the time someone clicks.
        open_url = url
        preconnect = None
        if is_upload and content.serves(resource):
            preconnect = settings.content_origin()
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
            credentialless_presets=settings.credentialless_presets(),
        )
        # Share sends the CKAN page showing this view (none in the form's
        # preview, which has no view id yet).
        share_url = None
        if resource_view.get("id"):
            package = data_dict.get("package") or {}
            share_url = toolkit.url_for(
                "{}_resource.read".format(package.get("type") or "dataset"),
                id=package.get("name") or resource.get("package_id"),
                resource_id=resource.get("id"),
                view_id=resource_view["id"],
                qualified=True,
            )
        # Held back behind a card until the reader asks: a third-party page
        # when consent is needed (site or view setting, the eu profile on an
        # external frame, or the browser's Global Privacy Control), or any
        # page when the browser asks to save data. Both come from request
        # headers, so the page varies by them (vary_held_back).
        save_data = gpc = False
        endpoint = None
        if has_request_context():
            save_data = request.headers.get("Save-Data", "").strip().lower() == "on"
            gpc = request.headers.get("Sec-GPC", "").strip() == "1"
            endpoint = request.endpoint
            g.webpageviewplus_vary_held_back = True
        third_party = not is_upload and not frame.is_ckan_host(url, ckan_hosts)
        # A third-party page browsers won't show here (http on an https site,
        # or its stored check found it refusing to be framed) gets a card
        # instead of a blank frame. Only from what's already known: rendering
        # never fetches anything (health.py).
        refused = health.refused(resource_view.get("id"), url) if third_party else None
        if refused:
            events.emit("frame_refused", reason=refused, host=frame.url_host(url))
        consent = third_party and not refused and (
            gpc
            or settings.consent_gate()
            or toolkit.asbool(resource_view.get("consent_gate"))
            or (settings.privacy_profile() == "eu" and built.preset == "external")
        )
        held_back = "consent" if consent else (
            "save-data" if save_data and not refused else None)
        # Opt-in view counting: impressions at render (not in the view form
        # or its preview), the rest by beacon. Rendered for every reader so a
        # cached page stays right; GPC browsers and bots are dropped here and
        # at the beacon.
        count_url = None
        if stats.countable(resource_view) and resource_view.get("id") and not refused:
            count_url = toolkit.url_for(
                "webpageviewplus_beacon.count", view_id=resource_view["id"])
            if endpoint and not endpoint.endswith(("edit_view", "new_view")) \
                    and not stats.viewer_opts_out():
                stats.add(resource_view["id"], impressions=1)
        events.emit(
            "view_rendered", preset=built.preset, requested_preset=preset,
            notices=",".join(built.notices) or "-", endpoint=endpoint,
        )
        if "same_origin_fallback" in built.notices:
            events.emit("same_origin_refused", resource_id=resource.get("id"))
        try:
            size = int(resource.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        load_size = toolkit.h.localised_filesize(size) if size > 0 else None
        return dict(
            form_vars,
            frame=built,
            share_url=share_url,
            held_back=held_back,
            count_url=count_url,
            privacy_policy_url=settings.privacy_policy_url(),
            # Uploads on the content origin: the hosts they were seen
            # contacting (CSP reports), if the site shows them.
            connections=(
                [c["host"] for c in connections.hosts_for(resource["id"])]
                if preconnect and settings.show_connections() else []
            ),
            consent_host=frame.url_host(built.src),
            # A content-origin URL's signature may expire before the reader
            # taps Load, so a held-back upload loads through CKAN's download
            # URL, which re-checks access and redirects to a fresh one.
            deferred_src=open_url if preconnect else built.src,
            load_size=load_size,
            frame_error=None,
            refused=refused,
            refused_host=frame.url_host(url),
            open_url=open_url,
            controls=settings.controls(),
            show_controls=not toolkit.asbool(resource_view.get("hide_controls")),
            color_scheme=settings.color_scheme(),
            narrow_width=settings.narrow_width(),
            preconnect=preconnect,
        )

    def view_template(self, context: Context, data_dict: dict[str, Any]) -> str:
        return "webpageviewplus/view.html"

    def form_template(self, context: Context, data_dict: dict[str, Any]) -> str:
        return "webpageviewplus/form.html"
