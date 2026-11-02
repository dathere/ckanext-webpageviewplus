from __future__ import annotations

import base64
import dataclasses
from typing import Any, Optional

import ckan.model as model
import ckan.plugins.toolkit as toolkit
from ckan.types import Context

from ckanext.webpageviewplus import (
    connections,
    events,
    frame,
    frameability,
    health,
    services,
    settings,
    stats,
    thumbnails,
)


def frameability_auth(context: Context, data_dict: dict[str, Any]) -> dict[str, Any]:
    """Only someone who may add views to the resource may make CKAN fetch a
    URL on its behalf."""
    try:
        toolkit.check_access(
            "resource_view_create",
            context,
            {"resource_id": data_dict.get("resource_id")},
        )
    except toolkit.NotAuthorized:
        return {"success": False}
    return {"success": True}


@toolkit.side_effect_free
def webpageviewplus_frameability(
    context: Context, data_dict: dict[str, Any]
) -> dict[str, Any]:
    """Whether the page at ``url`` allows this site to frame it.

    :param url: the page URL
    :param resource_id: the resource the view is for (for authorization)
    :returns: ``{"verdict": "frameable" | "blocked" | "unknown" | "skipped",
        "reason": "..."}``. Never refuses on the URL itself: the view's own
        validation does that when it's saved.
    """
    toolkit.check_access("webpageviewplus_frameability", context, data_dict)
    url = toolkit.get_or_bust(data_dict, "url")
    if not settings.frameability_check():
        return {"verdict": "skipped", "reason": "disabled"}
    if not frame.is_http_url(url):
        return {"verdict": "skipped", "reason": "not an http(s) URL"}
    hosts = settings.ckan_hosts()
    if frame.is_ckan_host(url, hosts):
        return {"verdict": "skipped", "reason": "a page on this site"}
    allowed = settings.allowed_hosts()
    if allowed and frame.url_host(url) not in allowed:
        return {"verdict": "skipped", "reason": "host not allowed"}
    identity = frameability.ckan_identity(settings.site_url())
    result = frameability.summary(frameability.check(url, identity))
    if result.get("verdict") == "blocked":
        events.emit("frameability_blocked", host=frame.url_host(url))
    return result


@toolkit.side_effect_free
def webpageviewplus_service_suggest(
    context: Context, data_dict: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """Embed settings for a well-known service's page (YouTube, Vimeo,
    Datawrapper, Flourish, Observable, Tableau Public). Fetches nothing.

    :param url: the page URL an editor typed
    :param resource_id: the resource the view is for (for authorization)
    :returns: ``None``, or ``{"service", "embed_url", "preset",
        "height_mode", "height_value", "problem"}``, where ``problem`` is
        ``""`` or why the settings won't work on this site:
        ``"private_dataset"`` (Microsoft's Office viewer fetches the file
        itself, so it can't show a private dataset's), ``"preset_not_allowed"``,
        ``"needs_content_origin"`` or ``"host_not_allowed"``.
    """
    toolkit.check_access("webpageviewplus_service_suggest", context, data_dict)
    found = services.suggest(toolkit.get_or_bust(data_dict, "url"))
    if found is None:
        return None
    resource = model.Resource.get(data_dict.get("resource_id") or "")
    private = bool(resource is not None and resource.package is not None
                   and resource.package.private)
    return dict(dataclasses.asdict(found), problem=suggestion_problem(found, private))


def suggestion_problem(found: services.Suggestion, private: bool) -> str:
    """Why a service's settings won't work on this site, or ``""``."""
    allowed = settings.allowed_hosts()
    if found.service == services.OFFICE and private:
        return "private_dataset"
    if found.preset not in settings.allowed_presets():
        return "preset_not_allowed"
    if frame.PRESETS[found.preset].needs_content_origin \
            and not settings.isolating_content_origin():
        return "needs_content_origin"
    if allowed and frame.url_host(found.embed_url) not in allowed:
        return "host_not_allowed"
    return ""


def default_view_suggestion(
    resource: dict[str, Any], private: bool
) -> Optional[services.Suggestion]:
    """The settings a view CKAN creates by default gets for a well-known
    service's link, if the site lists the service in default_view_services
    and the settings work on this site. Never an upload: an editor chooses
    whether Microsoft's viewer may fetch the site's own file."""
    if resource.get("url_type") == "upload":
        return None
    found = services.suggest(resource.get("url") or "")
    if found is None or found.service not in settings.default_view_services() \
            or suggestion_problem(found, private):
        return None
    return found


# Saving a view checks its page (with frameability_check on), so rendering
# only reads the stored result and visitors never make CKAN fetch anything.


@toolkit.chained_action
def resource_view_create(
    original: Any, context: Context, data_dict: dict[str, Any]
) -> dict[str, Any]:
    view = original(context, _with_service_settings(data_dict))
    health.after_save(view)
    return view


def _with_service_settings(data_dict: dict[str, Any]) -> dict[str, Any]:
    """A Website (Plus) view created with neither a page URL nor a preset,
    as CKAN creates its default views, gets a well-known service's settings
    for the resource's link."""
    if data_dict.get("view_type") != "webpage_view_plus" \
            or data_dict.get("page_url") or data_dict.get("preset"):
        return data_dict
    resource = model.Resource.get(data_dict.get("resource_id") or "")
    if resource is None or resource.package is None:
        return data_dict
    found = default_view_suggestion(
        {"url": resource.url, "url_type": resource.url_type},
        bool(resource.package.private))
    if found is None:
        return data_dict
    filled = dict(data_dict, page_url=found.embed_url, preset=found.preset)
    if not data_dict.get("height_mode") and not data_dict.get("height_value"):
        filled.update(height_mode=found.height_mode, height_value=found.height_value)
    if not data_dict.get("title") or data_dict["title"] == toolkit._("Website"):
        filled["title"] = found.service
    return filled


@toolkit.chained_action
def resource_view_update(
    original: Any, context: Context, data_dict: dict[str, Any]
) -> dict[str, Any]:
    view = original(context, data_dict)
    health.after_save(view)
    return view


# The webpage_view_plus_thumbnails plugin's own chain, so its copies are made
# only when it's enabled. keep=True: re-saving a view while its image's site
# is slow mustn't lose the thumbnail it already has.
@toolkit.chained_action
def thumbnails_view_create(
    original: Any, context: Context, data_dict: dict[str, Any]
) -> dict[str, Any]:
    view = original(context, data_dict)
    thumbnails.after_save(view, keep=True)
    thumbnails.service_format(context, view)
    return view


@toolkit.chained_action
def thumbnails_view_update(
    original: Any, context: Context, data_dict: dict[str, Any]
) -> dict[str, Any]:
    view = original(context, data_dict)
    thumbnails.after_save(view, keep=True)
    thumbnails.service_format(context, view)
    return view


# A picked image or uploaded Office file that's replaced or deleted remakes or
# drops its copies.
@toolkit.chained_action
def thumbnails_resource_update(
    original: Any, context: Context, data_dict: dict[str, Any]
) -> dict[str, Any]:
    resource = original(context, data_dict)
    thumbnails.refresh_from(resource["id"])
    return resource


@toolkit.chained_action
def thumbnails_resource_delete(
    original: Any, context: Context, data_dict: dict[str, Any]
) -> Any:
    result = original(context, data_dict)
    thumbnails.refresh_from(data_dict["id"])
    return result


def _counted_view(data_dict: dict[str, Any]):
    view_id = toolkit.get_or_bust(data_dict, "id")
    view = model.Session.query(model.ResourceView).get(view_id)
    if view is None or view.view_type != "webpage_view_plus":
        raise toolkit.ObjectNotFound("View not found")
    return view


def view_stats_auth(context: Context, data_dict: dict[str, Any]) -> dict[str, Any]:
    """Anyone who may update the view's resource."""
    try:
        view = _counted_view(data_dict)
        toolkit.check_access("resource_update", context, {"id": view.resource_id})
    except (toolkit.NotAuthorized, toolkit.ObjectNotFound, toolkit.ValidationError):
        return {"success": False}
    return {"success": True}


@toolkit.side_effect_free
def webpageviewplus_view_stats_show(
    context: Context, data_dict: dict[str, Any]
) -> dict[str, Any]:
    """Opt-in view counts for one view.

    :param id: the view's id
    :param days: how many days back, today included (default 30, at most the
        retention period)
    :returns: ``{"counting": bool, "days": [{"day", "impressions", "seen",
        "loaded", "engaged", "failed"}, ...], "totals": {...}}``. Counts hold
        nothing about viewers.
    """
    toolkit.check_access("webpageviewplus_view_stats_show", context, data_dict)
    view = _counted_view(data_dict)
    try:
        days = int(data_dict.get("days") or 30)
    except (TypeError, ValueError):
        raise toolkit.ValidationError({"days": [toolkit._("Must be a whole number")]})
    days = max(1, min(days, settings.view_counting_retention_days()))
    result = stats.totals(view.id, days)
    result["counting"] = stats.countable(view.config or {})
    return result


def connections_auth(context: Context, data_dict: dict[str, Any]) -> dict[str, Any]:
    """Anyone who may update the resource."""
    try:
        toolkit.check_access("resource_update", context, {"id": data_dict.get("id")})
    except (toolkit.NotAuthorized, toolkit.ObjectNotFound):
        return {"success": False}
    return {"success": True}


@toolkit.side_effect_free
def webpageviewplus_connections_show(
    context: Context, data_dict: dict[str, Any]
) -> list[dict[str, Any]]:
    """The hosts an uploaded page was seen contacting, from CSP reports on
    the content origin. Reports can be posted by anyone: a hint, not proof.

    :param id: the resource's id
    :returns: ``[{"host", "reports", "last_seen"}, ...]``, most reported first
    """
    toolkit.check_access("webpageviewplus_connections_show", context, data_dict)
    resource_id = toolkit.get_or_bust(data_dict, "id")
    return connections.hosts_for(resource_id)


def _pdf_view(data_dict: dict[str, Any]):
    view = model.ResourceView.get(data_dict.get("id") or "")
    if view is None or view.view_type != thumbnails.PDF_VIEW_TYPE:
        raise toolkit.ObjectNotFound(toolkit._("PDF view not found"))
    return view


def pdf_thumbnail_auth(context: Context, data_dict: dict[str, Any]) -> dict[str, Any]:
    """Anyone who may edit the view: the picture becomes its thumbnail."""
    try:
        view = _pdf_view(data_dict)
        toolkit.check_access("resource_view_update", context,
                             {"id": view.id, "resource_id": view.resource_id})
    except (toolkit.NotAuthorized, toolkit.ObjectNotFound, toolkit.ValidationError):
        return {"success": False}
    return {"success": True}


def webpageviewplus_pdf_thumbnail(context: Context, data_dict: dict[str, Any]) -> dict[str, Any]:
    """Store a picture of a PDF (Plus) view's first page, drawn by PDF.js in
    the editor's browser (the view form sends it). It becomes the view's
    thumbnail when the view is saved with "Thumbnail in the resource list"
    set to the PDF's first page, while the file is unchanged.

    :param id: the PDF (Plus) view's id
    :param image: the picture, base64 (PNG, JPEG, WebP or GIF; it's
        re-encoded)
    :returns: ``{"source": ...}``
    """
    toolkit.check_access("webpageviewplus_pdf_thumbnail", context, data_dict)
    view = _pdf_view(data_dict)
    if not thumbnails.enabled():
        raise toolkit.ValidationError({"id": [toolkit._("Thumbnails aren't enabled on this site")]})
    raw = data_dict.get("image") or ""
    if isinstance(raw, str) and raw.startswith("data:"):
        raw = raw.partition(",")[2]
    try:
        # A base64 limit a little over the image cap, checked before decoding.
        if len(raw) > thumbnails.MAX_IMAGE_BYTES * 4 // 3 + 4:
            raise ValueError("too big")
        png = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        raise toolkit.ValidationError({"image": [toolkit._("Not a usable image")]})
    source = thumbnails.store_first_page(view, png)
    if source is None:
        raise toolkit.ValidationError({"image": [toolkit._("Not a usable image")]})
    return {"source": source}
