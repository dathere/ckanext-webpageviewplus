from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from ckan.common import CKANConfig
from ckan.exceptions import CkanConfigurationException
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
    manage,
    pdf,
    repos,
    screenshots,
    stats,
    frame,
    services,
    settings,
    thumbnails,
    validators as v,
)
from ckanext.webpageviewplus.frame import is_http_url  # noqa: F401 (re-export)

def _thumbnails_loaded() -> bool:
    """Whether webpage_view_plus_thumbnails, which owns screenshots, is on.
    A function of its own so tests can patch it (patching
    plugins.plugin_loaded confuses with_plugins' teardown on CKAN 2.10)."""
    return plugins.plugin_loaded(thumbnails.PLUGIN)


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


def _service_hint(url: str, private: bool, repo_views: bool = False) -> dict[str, str] | None:
    """The problem the form's service region names for a page URL (a
    service's settings that won't work here, a site that refuses framing, a
    repository a Repository (Plus) view can show), with the service, preset
    and embed host it names, else None."""
    answer = actions.service_answer(url or "", private, repo_views=repo_views)
    if answer is None or not answer["problem"]:
        return None
    return {
        "problem": answer["problem"],
        "service": answer["service"],
        "preset": answer["preset"],
        "host": frame.url_host(answer["embed_url"]) if answer["embed_url"] else "",
    }


def _repo_view_url(data_dict: dict[str, Any]) -> str | None:
    """CKAN's new-view form for a Repository (Plus) view of the same
    resource (``?view_type=...``; the form and its script add ``repo_url``
    when the page URL isn't blank), naming a saved view as the one it
    ``replaces``, or None without a request."""
    resource = data_dict.get("resource") or {}
    if not has_request_context() or not resource.get("id"):
        return None
    package = data_dict.get("package") or {}
    params = {"view_type": "repo_view_plus"}
    saved = (data_dict.get("resource_view") or {}).get("id")
    if saved:
        params["replaces"] = saved
    return toolkit.url_for(
        "{}_resource.edit_view".format(package.get("type") or "dataset"),
        id=package.get("name") or resource.get("package_id"),
        resource_id=resource["id"], **params)


def _replaced_view(
    resource_view: dict[str, Any], resource: dict[str, Any]
) -> dict[str, Any] | None:
    """The Website (Plus) view a new Repository (Plus) view's form offers to
    delete (``?replaces=<id>``, from the Website (Plus) form's offer): only a
    view of the same resource that the user may delete. Ticked on the
    form's first GET; after a failed save, as it was posted."""
    if not has_request_context() or resource_view.get("id"):
        return None
    old_id = (request.args.get("replaces") or "").strip()
    if not old_id:
        return None
    user = toolkit.current_user
    name = user.name if getattr(user, "is_authenticated", False) else ""
    try:
        old = toolkit.get_action("resource_view_show")({"user": name}, {"id": old_id})
        toolkit.check_access("resource_view_delete", {"user": name}, {"id": old_id})
    except (toolkit.ObjectNotFound, toolkit.NotAuthorized):
        return None
    if old.get("view_type") != "webpage_view_plus" or old.get("resource_id") != resource.get("id"):
        return None
    return {"id": old_id, "title": old.get("title") or "",
            "checked": request.method == "GET" or bool(resource_view.get("replaces"))}


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

    # ITranslation

    def i18n_domain(self) -> str:
        # DefaultTranslation derives the domain from the plugin name
        # (ckanext-webpage_view_plus); the catalogues are named after the
        # package, as setup.cfg builds them.
        return "ckanext-webpageviewplus"

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
            "webpageviewplus_service_suggest": actions.webpageviewplus_service_suggest,
            "webpageviewplus_view_stats_show": actions.webpageviewplus_view_stats_show,
            "webpageviewplus_connections_show": actions.webpageviewplus_connections_show,
            "resource_view_create": actions.resource_view_create,
            "resource_view_update": actions.resource_view_update,
        }

    # IAuthFunctions

    def get_auth_functions(self):
        return {
            "webpageviewplus_frameability": actions.frameability_auth,
            "webpageviewplus_service_suggest": actions.frameability_auth,
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
                # Used by webpage_view_plus_thumbnails; checked in __after.
                "thumbnail_resource": [ignore_empty, unicode_safe],
                "no_auto_thumbnail": [ignore_missing, boolean_validator],
                "__after": [v.view_after_validator],
            },
            "iframed": False,
            "icon": "link",
            "always_available": True,
            "default_title": toolkit._("Website"),
        }

    def can_view(self, data_dict: dict[str, Any]) -> bool:
        # The view is always offered (always_available), so this only picks
        # the resources ckan.views.default_views and `ckan views create` give
        # one: a well-known service's link whose settings work on this site
        # (filled in by actions.resource_view_create), plus any formats the
        # site lists.
        resource = data_dict["resource"]
        private = bool((data_dict.get("package") or {}).get("private"))
        if actions.default_view_suggestion(resource, private) is not None:
            return True
        if resource.get("url_type") != "upload" and health.mixed_content(resource.get("url") or ""):
            return False  # browsers block an http:// page in an https:// site
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
        repo_views = plugins.plugin_loaded("repo_view_plus")

        form_vars = {
            "presets": settings.allowed_presets(),
            # An upload's own address is never checked for framing.
            "upload_resource": resource.get("url_type") == "upload",
            # Kept by a storage this extension can't serve from the content
            # origin (a cloud storage extension on CKAN 2.10/2.11).
            "upload_not_served": (
                not page_url and resource.get("url_type") == "upload"
                and bool(settings.isolating_content_origin())
                and not content.serves(resource)
            ),
            # A PDF shows better in PDF (Plus), where it's offered.
            "suggest_pdf_view": (
                plugins.plugin_loaded(pdf.VIEW_TYPE)
                and pdf.offered(data_dict.get("package") or {"id": resource.get("package_id")})
                and pdf.is_pdf_resource(resource)
            ),
            # Set below once the frame is built, for a PDF framed in a
            # sandbox, where Chrome won't run its PDF viewer.
            "pdf_sandboxed": None,
            "default_preset": settings.default_preset(),
            "height_modes": frame.HEIGHT_MODES,
            "referrer_policies": frame.REFERRER_POLICIES,
            "can_set_advanced": v.can_set_advanced(context),
            "frameability_check": settings.frameability_check(),
            # `external` runs as `interactive` unless downloads are sandboxed.
            "isolated": settings.harden_downloads(),
            "allowed_hosts": settings.allowed_hosts(),
            "service_names": services.NAMES,
            # Shown without the form's script too, so a site whose asset
            # bundles fail to load still tells the editor why.
            "service_hint": _service_hint(
                url, bool((data_dict.get("package") or {}).get("private")),
                repo_views=repo_views),
            # Where the form offers a Repository (Plus) view instead.
            "repo_view_url": _repo_view_url(data_dict) if repo_views else None,
            "view_counting": settings.view_counting(),
            # The images this view may use, when thumbnails are on.
            "thumbnail_choices": (
                thumbnails.choices(data_dict.get("package"))
                if thumbnails.enabled() else None
            ),
            # The page's own preview image may be fetched (thumbnails.auto).
            "thumbnail_fetch": thumbnails.enabled() and settings.thumbnail_fetch(),
            # The opt-out stops screenshots too (screenshots.target).
            "screenshots_on": _thumbnails_loaded() and screenshots.enabled(),
            # The view form's Screenshot panel, from the form's own values.
            "screenshot_panel": (
                screenshots.panel(resource_view.get("id"), resource_view, resource,
                                  data_dict.get("package") or {})
                if _thumbnails_loaded() and has_request_context()
                and (request.endpoint or "").endswith("edit_view")
                else None
            ),
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
            content_origin=content.frame_content_origin(resource, is_upload),
            downloads_sandboxed=settings.harden_downloads(),
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
        # when consent is needed (site or view setting, the eu profile, or the
        # browser's Global Privacy Control), or any
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
        # A page browsers won't show here (http on an https site, or a
        # third-party page its stored check found refusing framing) gets a card
        # instead of a blank frame. Only from what's already known: rendering
        # never fetches anything (health.py).
        refused = None if is_upload else health.refused(
            resource_view.get("id"), url, third_party)
        # Microsoft's Office viewer fetches the file from its own servers.
        if (not refused and not is_upload and (data_dict.get("package") or {}).get("private")
                and services.is_office_viewer(url)):
            refused = "private_file"
        if refused:
            events.emit("frame_refused", reason=refused, host=frame.url_host(url))
        consent = third_party and not refused and (
            gpc
            or settings.consent_gate()
            or toolkit.asbool(resource_view.get("consent_gate"))
            # Any preset: a sandbox doesn't stop the browser sending the
            # visitor's cookies for that host when the frame loads.
            or settings.privacy_profile() == "eu"
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
        # Judged on the frame as built, which knows about degraded presets,
        # the upload policy and sandbox overrides. strict is sandbox="".
        # "external" means switching to External would show it: never on
        # CKAN's own host or the content origin, which External can't free,
        # nor for a view already on External.
        shows_pdf = pdf.is_pdf_url(page_url) if page_url else pdf.is_pdf_resource(resource)
        pdf_sandboxed = None
        if shows_pdf and built.sandbox is not None:
            external_helps = (
                third_party and preset != "external"
                and not frame.on_content_origin(url, settings.content_origin())
            )
            pdf_sandboxed = "external" if external_helps else "other"
        return dict(
            form_vars,
            pdf_sandboxed=pdf_sandboxed,
            frame=built,
            share_url=share_url,
            held_back=held_back,
            # A remembered "always load" from this host still waits when
            # the browser asks to save data.
            save_data=save_data,
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
            screenshot=(
                screenshots.for_card(resource_view.get("id"), resource_view, resource,
                                     data_dict.get("package") or {})
                if refused == "blocked" and _thumbnails_loaded() else None
            ),
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


class WebpageviewplusThumbnailsPlugin(plugins.SingletonPlugin):
    """Thumbnails in the dataset page's resource list for resources with a
    Website (Plus) view (thumbnails.py). A plugin of its own, so a site that
    doesn't enable it never loads the resource_item.html override."""

    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IConfigurable)
    plugins.implements(plugins.IBlueprint)
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IAuthFunctions)
    plugins.implements(plugins.ITemplateHelpers)

    # IConfigurer

    def update_config(self, config_: CKANConfig):
        toolkit.add_template_directory(config_, "thumbnail_templates")

    # IConfigurable

    def configure(self, config_: CKANConfig):
        if not plugins.plugin_loaded("webpage_view_plus"):
            raise CkanConfigurationException(
                "webpage_view_plus_thumbnails needs the webpage_view_plus plugin: "
                "add it to ckan.plugins before webpage_view_plus_thumbnails")
        try:
            import PIL  # noqa: F401
        except ImportError:
            raise CkanConfigurationException(
                "webpage_view_plus_thumbnails needs Pillow: "
                "pip install ckanext-webpageviewplus[thumbnails]")

    # IBlueprint

    def get_blueprint(self):
        return [thumbnails.thumbnail, screenshots.screenshot, manage.blueprint]

    # IActions

    def get_actions(self):
        return {
            "resource_view_create": actions.thumbnails_view_create,
            "resource_view_update": actions.thumbnails_view_update,
            "resource_update": actions.thumbnails_resource_update,
            "resource_delete": actions.thumbnails_resource_delete,
            "webpageviewplus_screenshot_request": actions.webpageviewplus_screenshot_request,
            "webpageviewplus_thumbnail_status": manage.webpageviewplus_thumbnail_status,
            "webpageviewplus_thumbnail_refresh": manage.webpageviewplus_thumbnail_refresh,
            "webpageviewplus_thumbnail_delete": manage.webpageviewplus_thumbnail_delete,
        }

    # IAuthFunctions

    def get_auth_functions(self):
        return {
            "webpageviewplus_screenshot_request": actions.screenshot_request_auth,
            "webpageviewplus_thumbnail_status": manage.status_auth,
            "webpageviewplus_thumbnail_refresh": manage.change_auth,
            "webpageviewplus_thumbnail_delete": manage.change_auth,
        }

    # ITemplateHelpers

    def get_helpers(self):
        return {
            "webpageviewplus_thumbnail": thumbnails.for_resource,
            "webpageviewplus_replace_format_label": thumbnails.replace_format_label,
        }


class PdfViewPlusPlugin(plugins.SingletonPlugin):
    """PDF views shown with PDF.js from the content origin (pdf.py). Offered
    only where uploads are isolated, as PDF.js needs an origin of its own."""

    plugins.implements(plugins.IConfigurable)
    plugins.implements(plugins.IBlueprint)
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IAuthFunctions)
    plugins.implements(plugins.IResourceView, inherit=True)

    # IActions

    def get_actions(self):
        return {"webpageviewplus_pdf_thumbnail": actions.webpageviewplus_pdf_thumbnail}

    # IAuthFunctions

    def get_auth_functions(self):
        return {"webpageviewplus_pdf_thumbnail": actions.pdf_thumbnail_auth}

    # IConfigurable

    def configure(self, config_: CKANConfig):
        # Its templates, styles, script, translations and middleware come
        # from the main plugin.
        if not plugins.plugin_loaded("webpage_view_plus"):
            raise CkanConfigurationException(
                "pdf_view_plus needs the webpage_view_plus plugin: "
                "add it to ckan.plugins before pdf_view_plus")

    # IBlueprint

    def get_blueprint(self):
        return [pdf.pdf]

    # IResourceView

    def info(self) -> dict[str, Any]:
        return {
            "name": pdf.VIEW_TYPE,
            "title": toolkit._("PDF (Plus)"),
            "schema": {
                "pdf_url": [ignore_empty, unicode_safe, v.http_url_validator],
                "height_mode": [ignore_empty, unicode_safe, v.pdf_height_mode_validator],
                "height_value": [ignore_empty, unicode_safe],
                "hide_controls": [ignore_missing, boolean_validator],
                # Used by webpage_view_plus_thumbnails; checked in __after.
                "thumbnail_resource": [ignore_empty, unicode_safe],
                "__after": [v.pdf_after_validator],
            },
            "iframed": False,
            "icon": "file-pdf",
            "always_available": False,
            "default_title": toolkit._("PDF"),
        }

    def can_view(self, data_dict: dict[str, Any]) -> bool:
        if not pdf.offered(data_dict.get("package")):
            return False
        resource = data_dict["resource"]
        if resource.get("url_type") == "upload" and pdf.source({}, resource) is None:
            # Kept by a storage this extension can't read (a cloud storage
            # extension on CKAN 2.10/2.11): the view would show nothing.
            return False
        return pdf.is_pdf_resource(resource)

    def setup_template_variables(
        self, context: Context, data_dict: dict[str, Any]
    ) -> dict[str, Any]:
        # Checked again at render time, like webpage_view_plus: resource_view
        # is None on the "new view" form, and unsaved form data in previews.
        resource_view = data_dict.get("resource_view") or {}
        resource = data_dict["resource"]
        form_vars = {
            "height_modes": pdf.HEIGHT_MODES,
            "content_origin": settings.content_origin(),
            "pdf_max_mb": settings.pdf_max_size() // (1024 * 1024),
            # Whether the stored first-page copy still matches the file, so
            # the form redraws it only when needed.
            "first_page_ready": (
                thumbnails.first_page_ready(resource_view, resource)
                if has_request_context()
                and (request.endpoint or "").endswith(("edit_view", "new_view"))
                else False
            ),
            "isolated": bool(settings.pdf_mode()),
            "pdf_on_ckan_host": pdf.on_ckan_host(),
            "allowed_hosts": settings.allowed_hosts(),
            # The images this view may use, when thumbnails are on.
            "thumbnail_choices": (
                thumbnails.choices(data_dict.get("package"))
                if thumbnails.enabled() else None
            ),
        }
        if not settings.pdf_mode():
            return dict(form_vars, frame=None, frame_error="content_origin")
        found = pdf.source(resource_view, resource)
        if found is None:
            return dict(form_vars, frame=None, frame_error="pdf_url")
        kind, url = found
        if kind == "link" and not pdf.host_allowed(url):
            return dict(form_vars, frame=None, frame_error="host")
        view_id = resource_view.get("id")
        if not view_id:
            # The form's preview of an unsaved view: there's no view to serve
            # the file for yet.
            return dict(form_vars, frame=None, frame_error="unsaved")

        package_id = (data_dict.get("package") or {}).get("id") or resource.get("package_id")
        private = not content.is_public(package_id)
        on_ckan_host = pdf.on_ckan_host()
        if private and on_ckan_host:
            return dict(form_vars, frame=None, frame_error="pdf_private")
        lang = toolkit.h.lang() if has_request_context() else "en"
        title = resource_view.get("title") or toolkit._("PDF document")
        mode = resource_view.get("height_mode") or pdf.DEFAULT_HEIGHT[0]
        value = resource_view.get("height_value") or (
            pdf.DEFAULT_HEIGHT[1] if mode == pdf.DEFAULT_HEIGHT[0] else "80")
        built = frame.build_frame(
            pdf.viewer_url(view_id, kind, url, resource, bool(private), lang),
            # On CKAN's host: interactive's sandbox, whose links escape it
            # (pdf.HOST_SANDBOX). No allow-same-origin, so the invariant holds.
            "interactive" if on_ckan_host else "data-schematic",
            ckan_hosts=settings.ckan_hosts(),
            is_upload=True,
            content_origin=None if on_ckan_host else settings.isolating_content_origin(),
            downloads_sandboxed=on_ckan_host,
            sandbox_override=pdf.HOST_SANDBOX if on_ckan_host else "",
            referrer_policy=settings.referrer_policy(),
            height_mode=mode,
            height_value=value,
            min_height=settings.min_height(),
            max_height=settings.max_height(),
            title=title,
        )
        # Open in new tab: the file itself, from CKAN (an upload's download
        # URL re-checks access) or from its own site. Reload and Load: CKAN's
        # route minting a fresh viewer URL, as the frame's may have expired.
        open_url = url if kind == "link" else resource.get("url")
        reload_url = pdf.open_path(view_id)
        save_data = False
        if has_request_context():
            save_data = request.headers.get("Save-Data", "").strip().lower() == "on"
            g.webpageviewplus_vary_held_back = True
        events.emit("view_rendered", preset=built.preset, requested_preset=pdf.VIEW_TYPE,
                    notices=",".join(built.notices) or "-",
                    endpoint=request.endpoint if has_request_context() else None)
        package = data_dict.get("package") or {}
        share_url = toolkit.url_for(
            "{}_resource.read".format(package.get("type") or "dataset"),
            id=package.get("name") or resource.get("package_id"),
            resource_id=resource.get("id"),
            view_id=view_id,
            qualified=True,
        )
        try:
            size = int(resource.get("size") or 0) if kind == "upload" else 0
        except (TypeError, ValueError):
            size = 0
        return dict(
            form_vars,
            frame=built,
            share_url=share_url,
            # The visitor never contacts a linked PDF's site (CKAN fetches
            # it), so there's no consent card: only Save-Data holds it back.
            held_back="save-data" if save_data else None,
            save_data=save_data,
            count_url=None,
            privacy_policy_url=settings.privacy_policy_url(),
            connections=[],
            consent_host=frame.url_host(built.src),
            deferred_src=reload_url,
            load_size=toolkit.h.localised_filesize(size) if size > 0 else None,
            frame_error=None,
            refused=None,
            refused_host=None,
            open_url=open_url,
            reload_url=reload_url,
            controls=settings.controls(),
            show_controls=not toolkit.asbool(resource_view.get("hide_controls")),
            color_scheme=settings.color_scheme(),
            narrow_width=settings.narrow_width(),
            preconnect=None if on_ckan_host else settings.content_origin(),
        )

    def view_template(self, context: Context, data_dict: dict[str, Any]) -> str:
        return "webpageviewplus/view.html"

    def form_template(self, context: Context, data_dict: dict[str, Any]) -> str:
        return "webpageviewplus/pdf_form.html"


class RepoViewPlusPlugin(plugins.SingletonPlugin):
    """A public repository's root on GitHub, Codeberg, GitLab, Bitbucket or
    a repo_hosts site (repos.py): fetched by CKAN, shown as a static page with
    no scripts, so no content origin is needed."""

    plugins.implements(plugins.IConfigurable)
    plugins.implements(plugins.IBlueprint)
    plugins.implements(plugins.IResourceView, inherit=True)

    # IConfigurable

    def configure(self, config_: CKANConfig):
        if not plugins.plugin_loaded("webpage_view_plus"):
            raise CkanConfigurationException(
                "repo_view_plus needs the webpage_view_plus plugin: "
                "add it to ckan.plugins before repo_view_plus")
        try:
            import cmarkgfm  # noqa: F401
            import nh3  # noqa: F401
        except ImportError:
            raise CkanConfigurationException(
                "repo_view_plus needs nh3 and cmarkgfm: "
                "pip install \"ckanext-webpageviewplus[repos]\"")

    # IBlueprint

    def get_blueprint(self):
        return [repos.repo]

    # IResourceView

    def info(self) -> dict[str, Any]:
        return {
            "name": repos.VIEW_TYPE,
            "title": toolkit._("Repository (Plus)"),
            "schema": {
                "repo_url": [ignore_empty, unicode_safe, v.http_url_validator],
                "height_mode": [ignore_empty, unicode_safe, v.pdf_height_mode_validator],
                "height_value": [ignore_empty, unicode_safe],
                "hide_controls": [ignore_missing, boolean_validator],
                "__after": [v.repo_after_validator],
            },
            "iframed": False,
            "icon": "code-branch",
            "always_available": False,
            "default_title": toolkit._("Repository"),
        }

    def can_view(self, data_dict: dict[str, Any]) -> bool:
        resource = data_dict["resource"]
        return resource.get("url_type") != "upload" \
            and repos.is_repo_link(resource.get("url") or "")

    def setup_template_variables(
        self, context: Context, data_dict: dict[str, Any]
    ) -> dict[str, Any]:
        resource_view = data_dict.get("resource_view") or {}
        resource = data_dict["resource"]
        # The Website (Plus) form's offer links here with the page URL its
        # editor typed. Only for a new view's first GET (a failed save keeps
        # what was posted), and only a repository's address.
        prefill = ""
        if has_request_context() and request.method == "GET" \
                and not resource_view.get("id") and not resource_view.get("repo_url"):
            asked = (request.args.get("repo_url") or "").strip()
            if repos.is_repo_link(asked):
                prefill = asked
        form_vars = {"height_modes": repos.HEIGHT_MODES, "prefill_repo_url": prefill,
                     "replaces_view": _replaced_view(resource_view, resource)}
        url = repos.repo_url(resource_view, resource)
        found = repos.parse(url) if frame.is_http_url(url) else None
        if found is None:
            return dict(form_vars, frame=None, frame_error="repo_url",
                        self_hosted_sites=repos.self_hosted_sites())
        view_id = resource_view.get("id")
        if not view_id:
            return dict(form_vars, frame=None, frame_error="repo_unsaved")
        mode = resource_view.get("height_mode") or repos.DEFAULT_HEIGHT[0]
        value = resource_view.get("height_value") or (
            repos.DEFAULT_HEIGHT[1] if mode == repos.DEFAULT_HEIGHT[0] else "80")
        page = toolkit.url_for("webpageviewplus_repo.page", view_id=view_id, qualified=True)
        built = frame.build_frame(
            page,
            "strict",
            ckan_hosts=settings.ckan_hosts(),
            content_origin=settings.isolating_content_origin(),
            sandbox_override=repos.SANDBOX,
            referrer_policy=settings.referrer_policy(),
            height_mode=mode,
            height_value=value,
            min_height=settings.min_height(),
            max_height=settings.max_height(),
            title=resource_view.get("title") or toolkit._("Repository"),
        )
        save_data = gpc = False
        endpoint = None
        if has_request_context():
            save_data = request.headers.get("Save-Data", "").strip().lower() == "on"
            gpc = request.headers.get("Sec-GPC", "").strip() == "1"
            endpoint = request.endpoint
            g.webpageviewplus_vary_held_back = True
        # CKAN fetches the repository, but the README's images load in the
        # visitor's browser from the repository's site and others:
        # third-party content.
        consent = gpc or settings.consent_gate() or settings.privacy_profile() == "eu"
        events.emit("view_rendered", preset=built.preset, requested_preset=repos.VIEW_TYPE,
                    notices=",".join(built.notices) or "-", endpoint=endpoint)
        package = data_dict.get("package") or {}
        share_url = toolkit.url_for(
            "{}_resource.read".format(package.get("type") or "dataset"),
            id=package.get("name") or resource.get("package_id"),
            resource_id=resource.get("id"),
            view_id=view_id,
            qualified=True,
        )
        return dict(
            form_vars,
            frame=built,
            share_url=share_url,
            held_back="consent" if consent else ("save-data" if save_data else None),
            save_data=save_data,
            count_url=None,
            privacy_policy_url=settings.privacy_policy_url(),
            connections=[],
            consent_host=found.host.host,
            deferred_src=built.src,
            load_size=None,
            frame_error=None,
            refused=None,
            refused_host=None,
            open_url=found.html_url,
            reload_url=built.src,
            controls=settings.controls(),
            show_controls=not toolkit.asbool(resource_view.get("hide_controls")),
            color_scheme=settings.color_scheme(),
            narrow_width=settings.narrow_width(),
            preconnect=None,
        )

    def view_template(self, context: Context, data_dict: dict[str, Any]) -> str:
        return "webpageviewplus/view.html"

    def form_template(self, context: Context, data_dict: dict[str, Any]) -> str:
        return "webpageviewplus/repo_form.html"
