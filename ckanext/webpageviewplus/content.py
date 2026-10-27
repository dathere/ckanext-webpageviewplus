"""The content origin: uploaded HTML served from a separate site.

A page served from a different registrable domain can keep its own origin
(storage, workers, maps) and still can't touch CKAN. CKAN serves this route
too, behind a reverse proxy that forwards the content domain to it, but it
answers only when the request's host is the content origin's.

Public resources get a stable, unsigned URL. Private ones get a URL signed for
the current time window, minted after CKAN's normal authorization check when
the view renders: the content origin has no session.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time
from typing import Any, Optional
from urllib.parse import quote, urlsplit

from flask import Blueprint, Response, abort, request, send_file

import ckan.model as model
import ckan.plugins.toolkit as toolkit
from ckan.lib.uploader import ResourceUpload, get_resource_uploader

from ckanext.webpageviewplus import frame, settings

log = logging.getLogger(__name__)

PATH = "/webpageviewplus/content/<resource_id>/<path:filename>"
HTML_FORMATS = ("html", "htm")
HTML_MIMETYPES = ("text/html", "application/xhtml+xml")
SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")
# Set by serve() for ContentHostMiddleware, which owns Cache-Control.
CACHE_ENVIRON_KEY = "webpageviewplus.cache"

content = Blueprint("webpageviewplus_content", __name__)


def is_html_upload(resource: dict[str, Any]) -> bool:
    if resource.get("url_type") != "upload":
        return False
    if (resource.get("format") or "").lower() in HTML_FORMATS:
        return True
    if (resource.get("mimetype") or "").lower() in HTML_MIMETYPES:
        return True
    _, dot, ext = (resource.get("url") or "").rpartition(".")
    return bool(dot) and ext.lower().split("?")[0] in HTML_FORMATS


def resource_dict(resource_obj: model.Resource) -> dict[str, Any]:
    return {
        "id": resource_obj.id,
        "url": resource_obj.url,
        "url_type": resource_obj.url_type,
        "format": resource_obj.format,
        "mimetype": resource_obj.mimetype,
    }


def serves(resource: dict[str, Any]) -> bool:
    """Whether this resource is framed from the content origin: one is
    configured, it's an HTML upload, and the file is on CKAN's own
    filesystem storage. Other uploaders (cloud storage) aren't supported yet,
    so their uploads stay framed from CKAN's origin, sandboxed."""
    if not settings.content_origin() or not is_html_upload(resource):
        return False
    try:
        return isinstance(get_resource_uploader(resource), ResourceUpload)
    except Exception:
        return False


def _window(now: Optional[float] = None) -> int:
    return int(now if now is not None else time.time()) // settings.url_ttl()


def _signature(resource_id: str, window: int) -> str:
    message = "{}:{}".format(resource_id, window).encode()
    return hmac.new(
        settings.signing_secret().encode(), message, hashlib.sha256
    ).hexdigest()


def signature_valid(resource_id: str, window: str, signature: str) -> bool:
    """Accept the current window and the one before it, so a URL minted just
    before a window boundary keeps working for at least one url_ttl."""
    try:
        w = int(window)
    except (TypeError, ValueError):
        return False
    if w not in (_window(), _window() - 1):
        return False
    if not SIGNATURE_RE.match(signature or ""):
        return False  # also keeps compare_digest to ASCII, which it requires
    return hmac.compare_digest(_signature(resource_id, w), signature)


def _filename(resource: dict[str, Any]) -> str:
    name = os.path.basename(urlsplit(resource.get("url") or "").path)
    return quote(name or "page.html")


def content_url(resource: dict[str, Any], private: bool) -> str:
    url = "{}/webpageviewplus/content/{}/{}".format(
        settings.content_origin(), resource["id"], _filename(resource)
    )
    if private:
        w = _window()
        url += "?w={}&s={}".format(w, _signature(resource["id"], w))
    return url


def is_public(package_id: str) -> bool:
    """Whether an anonymous visitor may see the dataset, so its content URL
    needs no signature. The one decision used to mint URLs and to serve
    them: it respects drafts, permission-label plugins and dataset state.
    The user is named explicitly as anonymous, because on CKAN's own host a
    context without one would be filled in with the logged-in viewer."""
    try:
        toolkit.check_access(
            "package_show",
            {"user": "", "auth_user_obj": None, "ignore_auth": False},
            {"id": package_id},
        )
        return True
    except (toolkit.NotAuthorized, toolkit.ObjectNotFound):
        return False


def on_content_host() -> bool:
    origin = settings.content_origin()
    if not origin:
        return False
    host = frame.canonical_config_host(request.host)
    return bool(host) and host == frame.url_host(origin)


@content.route(PATH)
def serve(resource_id: str, filename: str):
    if not on_content_host():
        abort(404)
    resource_obj = model.Resource.get(resource_id)
    if resource_obj is None or resource_obj.state != "active":
        abort(404)
    package = resource_obj.package
    # Drafts are served too (signed, since they aren't public): that's the
    # editor's preview while creating a dataset.
    if package is None or package.state not in ("active", "draft"):
        abort(404)
    resource = resource_dict(resource_obj)
    if not serves(resource):
        abort(404)

    private = not is_public(package.id)
    if private and not signature_valid(
        resource_id, request.args.get("w", ""), request.args.get("s", "")
    ):
        abort(404)  # not 403: don't confirm that a private resource exists

    try:
        path = get_resource_uploader(resource).get_path(resource_id)
    except Exception:
        log.warning("webpageviewplus: no local file for resource %s", resource_id)
        abort(404)
    if not path or not os.path.isfile(path):
        abort(404)

    # Cache-Control is set by ContentHostMiddleware from this decision: CKAN's
    # own response hooks run after this view and would overwrite it.
    request.environ[CACHE_ENVIRON_KEY] = "private" if private else "public"
    return send_file(path, mimetype="text/html", conditional=True)


def frame_ancestors() -> str:
    sources = []
    for host in settings.ckan_hosts():
        # Any port: hosts are compared without ports everywhere else too.
        sources += ["http://{}:*".format(host), "https://{}:*".format(host)]
    return "frame-ancestors " + " ".join(sources)


@content.after_request
def content_headers(response: Response) -> Response:
    # Cookies are stripped by ContentHostMiddleware, after Flask has saved
    # the session; doing it here would be too early.
    response.headers["Content-Security-Policy"] = frame_ancestors()
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


class ContentHostMiddleware:
    """WSGI wrapper that confines the content host to the content route.

    The content domain proxies to the same CKAN, so without this every CKAN
    page would also answer there, and a visitor tricked into logging in on
    it would give uploaded pages (which run on that origin) their session.
    On the content host, anything but the content route is a 404, and no
    response sets a cookie.
    """

    PREFIX = "/webpageviewplus/content/"

    def __init__(self, app: Any):
        self.app = app

    def __call__(self, environ: dict[str, Any], start_response: Any):
        origin = settings.content_origin()
        if not origin:
            return self.app(environ, start_response)
        host = frame.canonical_config_host(
            environ.get("HTTP_HOST") or environ.get("SERVER_NAME") or ""
        )
        if host != frame.url_host(origin):
            return self.app(environ, start_response)
        if not (environ.get("PATH_INFO") or "").startswith(self.PREFIX):
            start_response(
                "404 Not Found",
                [("Content-Type", "text/plain"), ("X-Content-Type-Options", "nosniff")],
            )
            return [b"Not found"]

        def finish(status: str, headers: list, exc_info: Any = None):
            headers = [
                (k, v)
                for k, v in headers
                if k.lower() not in ("set-cookie", "cache-control")
            ]
            headers.append(
                ("Cache-Control", cache_control(status, environ.get(CACHE_ENVIRON_KEY)))
            )
            return start_response(status, headers, exc_info)

        return self.app(environ, finish)


def cache_control(status: str, visibility: Optional[str]) -> str:
    """Public content is cacheable by anyone (CDN-friendly, revalidated by
    ETag); private content only by the viewer's browser; anything the route
    didn't explicitly mark (errors included) not at all."""
    if not status.startswith(("200", "304")) or visibility not in ("public", "private"):
        return "no-store"
    return "{}, max-age={}".format(visibility, settings.url_ttl())
