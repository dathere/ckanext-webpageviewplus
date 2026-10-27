"""Hardening for HTML that CKAN itself serves from its own origin.

CKAN core serves an uploaded HTML resource inline, as text/html, from CKAN's
own origin, so anyone who opens its download link (or a frame that navigates
there) runs its scripts as CKAN. The resource proxy does the same for any
linked page. This adds a CSP ``sandbox`` to such responses: the page still
renders and runs its scripts, but in an opaque origin that can't read CKAN
pages, its cookies or its API with the viewer's session.
"""
from __future__ import annotations

from flask import Blueprint, Response, request

from ckanext.webpageviewplus import settings

# Response types a browser runs scripts in.
ACTIVE_TYPES = frozenset(
    [
        "text/html",
        "application/xhtml+xml",
        "image/svg+xml",
        "text/xml",
        "application/xml",
    ]
)

# No allow-same-origin: that is the point.
DOWNLOAD_SANDBOX = (
    "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox "
    "allow-forms allow-downloads allow-modals"
)

hardening = Blueprint("webpageviewplus_hardening", __name__)


def _is_ckan_served_resource(endpoint: str) -> bool:
    # "<package type>_resource.download" for every dataset type, and the
    # resource proxy (ckanext-resourceproxy).
    return endpoint.endswith("_resource.download") or endpoint == (
        "resource_proxy.proxy_view"
    )


@hardening.after_app_request
def sandbox_active_downloads(response: Response) -> Response:
    if not settings.harden_downloads():
        return response
    if not _is_ckan_served_resource(request.endpoint or ""):
        return response
    if (response.mimetype or "").lower() not in ACTIVE_TYPES:
        return response
    # Added as its own header, so any policy the site already sends still
    # applies too (browsers enforce every CSP header).
    response.headers.add("Content-Security-Policy", DOWNLOAD_SANDBOX)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
