"""Hardening for HTML that CKAN itself serves from its own origin.

CKAN core serves an uploaded HTML resource inline, as text/html, from CKAN's
own origin, so anyone who opens its download link (or a frame that navigates
there) runs its scripts as CKAN. The resource proxy does the same for any
linked page. This adds a CSP ``sandbox`` to such responses: the page still
renders and runs its scripts, but in an opaque origin that can't read CKAN
pages, its cookies or its API with the viewer's session.
"""
from __future__ import annotations

from flask import Blueprint, Response, redirect, request

import ckan.model as model

from ckanext.webpageviewplus import content, settings

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


def is_active_type(mimetype: str) -> bool:
    """HTML, SVG and any XML type (``+xml`` subtypes like atom or rss can
    carry XHTML or SVG with scripts)."""
    mimetype = mimetype.lower().strip()
    return (
        mimetype in ACTIVE_TYPES
        or mimetype.endswith("+xml")
        or mimetype.endswith("/xml")
    )


def _is_download(endpoint: str) -> bool:
    # "<package type>_resource.download" for every dataset type. CKAN 2.10
    # also registers a "resource" blueprint on the same URLs, so its
    # downloads can resolve to "resource.download".
    return endpoint.endswith("_resource.download") or endpoint == "resource.download"


def _is_ckan_served_resource(endpoint: str) -> bool:
    # Resource downloads, and the resource proxy (ckanext-resourceproxy).
    return _is_download(endpoint) or endpoint == "resource_proxy.proxy_view"


def _content_redirect(response: Response) -> Response | None:
    """With a content origin, send an HTML upload's download there instead:
    CKAN's origin then never serves it. CKAN's download view has already
    checked that the viewer may see the resource, so a private one gets a
    signed URL."""
    if not settings.content_origin() or not _is_download(request.endpoint or ""):
        return None
    resource_id = (request.view_args or {}).get("resource_id")
    resource_obj = model.Resource.get(resource_id) if resource_id else None
    if resource_obj is None or resource_obj.package is None:
        return None
    resource = content.resource_dict(resource_obj)
    if not content.serves(resource):
        return None
    response.close()
    return redirect(
        content.content_url(
            resource, private=not content.is_public(resource_obj.package_id)
        ),
        code=302,
    )


@hardening.after_app_request
def sandbox_active_downloads(response: Response) -> Response:
    if not _is_ckan_served_resource(request.endpoint or ""):
        return response
    if response.status_code == 200:
        redirected = _content_redirect(response)
        if redirected is not None:
            return redirected
    if not settings.harden_downloads():
        return response
    if not is_active_type(response.mimetype or ""):
        return response
    # Added as its own header, so any policy the site already sends still
    # applies too (browsers enforce every CSP header).
    response.headers.add("Content-Security-Policy", DOWNLOAD_SANDBOX)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
