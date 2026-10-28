from __future__ import annotations

from typing import Any

import ckan.model as model
import ckan.plugins.toolkit as toolkit
from ckan.types import Context

from ckanext.webpageviewplus import (
    connections,
    events,
    frame,
    frameability,
    settings,
    stats,
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
        raise toolkit.ValidationError({"days": ["Must be a whole number"]})
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
