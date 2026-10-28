from __future__ import annotations

from typing import Any

import ckan.plugins.toolkit as toolkit
from ckan.types import Context

from ckanext.webpageviewplus import events, frame, frameability, settings


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
