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
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional
from urllib.parse import quote, urlsplit

from flask import Blueprint, Response, abort, request, send_file

import ckan.model as model
import ckan.plugins.toolkit as toolkit
from ckan.lib.uploader import ResourceUpload, get_resource_uploader

try:  # CKAN 2.12+: uploads go through ckan.files (file-keeper) storages
    import file_keeper as fk
    from ckan.lib.files.default.fs import FsStorage
except ImportError:
    fk = None
    FsStorage = None

from ckanext.webpageviewplus import connections, events, frame, settings

log = logging.getLogger(__name__)

PATH = "/webpageviewplus/content/<resource_id>/<path:filename>"
HTML_FORMATS = ("html", "htm")
HTML_MIMETYPES = ("text/html", "application/xhtml+xml")
SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 64 * 1024
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
    configured, it's an HTML upload, and this extension can read its file
    (see _readable_uploader). Runs at render time, so it never touches the
    storage. Uploads it can't read stay framed from CKAN's origin, sandboxed."""
    if not settings.content_origin() or not is_html_upload(resource):
        return False
    return _readable_uploader(resource) is not None


def _readable_uploader(resource: dict[str, Any]) -> Any:
    """The resource's uploader if this extension can read its files: the
    classic ResourceUpload (CKAN's upload folder), or, on CKAN 2.12+, one
    whose ckan.files storage can stream, on disk or in the cloud (S3, Google
    Cloud Storage, Azure). None for anything else, such as a cloud storage
    extension on CKAN 2.10/2.11. Storages are built once at startup, so this
    is a type check, cheap enough for every render."""
    try:
        upload = get_resource_uploader(resource)
    except Exception:
        return None
    if isinstance(upload, ResourceUpload):
        return upload
    storage = getattr(upload, "storage", None)
    if fk is not None and isinstance(storage, fk.Storage) \
            and storage.supports(fk.Capability.STREAM):
        return upload
    return None


@dataclass(frozen=True)
class StoredFile:
    """An upload's bytes, wherever its storage keeps them. ``path`` is set
    when they're a file on CKAN's own disk; ``etag``, for a streamed file,
    is the storage's hash of it, when it keeps one."""
    size: int
    path: Optional[str]
    _chunks: Callable[[], Iterable[bytes]]
    etag: Optional[str] = None

    def chunks(self) -> Iterable[bytes]:
        return self._chunks()


def _file_chunks(path: str) -> Callable[[], Iterable[bytes]]:
    def read() -> Iterable[bytes]:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    return
                yield chunk
    return read


def stored_file(resource: dict[str, Any]) -> Optional[StoredFile]:
    """The resource's upload, or None when it can't be read (another
    uploader, or no file). Asks the storage, which may be across the
    network: never call it while rendering a page."""
    upload = _readable_uploader(resource)
    if upload is None:
        return None
    location = upload.get_path(resource["id"])
    if isinstance(upload, ResourceUpload):
        path = location
    elif FsStorage is not None and isinstance(upload.storage, FsStorage):
        # A ckan.files location; full_path refuses traversal and null bytes.
        path = upload.storage.full_path(location)
    else:
        path = None
    if path is not None:
        if not os.path.isfile(path):
            return None
        return StoredFile(os.path.getsize(path), path, _file_chunks(path))
    storage = upload.storage
    try:
        if storage.supports(fk.Capability.ANALYZE):
            # One request (a HEAD on S3) for both the size and the hash.
            data = storage.analyze(location)
        else:
            data = fk.FileData(location, size=storage.size(location))
    except (fk.exc.MissingFileError, fk.exc.LocationError):
        return None
    return StoredFile(data.size, None, lambda: storage.stream(data), etag=data.hash or None)


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
        events.emit("signature_refused", resource_id=resource_id)
        abort(404)  # not 403: don't confirm that a private resource exists

    try:
        stored = stored_file(resource)
    except Exception:
        log.warning("webpageviewplus: can't read the upload of resource %s", resource_id)
        stored = None
    if stored is None:
        abort(404)

    # Cache-Control is set by ContentHostMiddleware from this decision: CKAN's
    # own response hooks run after this view and would overwrite it.
    request.environ[CACHE_ENVIRON_KEY] = "private" if private else "public"
    response = file_response(stored, "text/html")
    if settings.connection_reports():
        # Never blocks: only tells CKAN which hosts the page contacts.
        response.headers["Content-Security-Policy-Report-Only"] = connections.policy(resource_id)
        response.headers["Reporting-Endpoints"] = connections.reporting_endpoints(resource_id)
    return response


def file_response(stored: StoredFile, mimetype: str) -> Response:
    """The response sending an upload: from disk, or streamed from its
    storage."""
    if stored.path:
        return send_file(stored.path, mimetype=mimetype, conditional=True)
    if stored.etag and request.if_none_match.contains_weak(stored.etag):
        # A browser revalidating an unchanged file (public ones are sent
        # no-cache): answered without reading the file from the storage.
        # Compared weakly, as If-None-Match is: a proxy that compresses
        # (nginx's gzip) weakens ETags.
        response = Response(status=304)
        response.set_etag(stored.etag)
        return response
    # A cloud storage: the bytes pass through CKAN (no range requests).
    response = Response(
        stored.chunks(), mimetype=mimetype,
        headers={"Content-Length": str(stored.size)},
    )
    if stored.etag:
        response.set_etag(stored.etag)
    return response


@content.route(connections.PATH, methods=["POST"])
def report(resource_id: str):
    """CSP reports from served uploads: kept as hosts per resource. Always
    204, so it says nothing about which resources exist."""
    if not on_content_host():
        abort(404)
    if settings.connection_reports():
        body = request.stream.read(connections.MAX_BODY + 1)
        resource_obj = model.Resource.get(resource_id)
        # Only uploads this route serves send reports (connections.policy).
        if (
            len(body) <= connections.MAX_BODY
            and resource_obj is not None
            and resource_obj.state == "active"
            and resource_obj.package is not None
            and serves(resource_dict(resource_obj))
        ):
            connections.record(resource_id, connections.hosts_in(
                body, frame.url_host(settings.content_origin())))
    return "", 204


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
    """WSGI wrapper that confines the content host to the content route and
    its CSP report endpoint.

    The content domain proxies to the same CKAN, so without this every CKAN
    page would also answer there, and a visitor tricked into logging in on
    it would give uploaded pages (which run on that origin) their session.
    On the content host, anything but the content route is a 404, and no
    response sets a cookie.
    """

    PREFIX = "/webpageviewplus/content/"
    # pdf_view_plus: the vendored PDF.js viewer and the PDFs it shows.
    PDF_PREFIXES = ("/webpageviewplus/pdfjs/", "/webpageviewplus/pdf/")

    def __init__(self, app: Any):
        self.app = app

    def __call__(self, environ: dict[str, Any], start_response: Any):
        origin = settings.content_origin()
        host = frame.canonical_config_host(
            environ.get("HTTP_HOST") or environ.get("SERVER_NAME") or ""
        )
        if not origin or host != frame.url_host(origin):
            return self.app(environ, _immutable_if_asked(environ, start_response))
        if not (environ.get("PATH_INFO") or "").startswith(
            (self.PREFIX, connections.PREFIX) + self.PDF_PREFIXES
        ):
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


IMMUTABLE_ENVIRON_KEY = "webpageviewplus.immutable"
IMMUTABLE = "public, max-age=31536000, immutable"


def _immutable_if_asked(environ: dict[str, Any], start_response: Any) -> Any:
    """On CKAN's host: a view that set IMMUTABLE_ENVIRON_KEY (a public
    response whose URL names its content, like a thumbnail's ?v=<digest>)
    gets a year's Cache-Control. CKAN's own response hook runs after the
    view and would set max-age=ckan.cache_expires, so it's replaced here."""
    def finish(status: str, headers: list, exc_info: Any = None):
        if environ.get(IMMUTABLE_ENVIRON_KEY) and status[:3] in ("200", "304"):
            headers = [(k, v) for k, v in headers if k.lower() != "cache-control"]
            headers.append(("Cache-Control", IMMUTABLE))
        return start_response(status, headers, exc_info)
    return finish


def cache_control(status: str, visibility: Optional[str]) -> str:
    """Public content may be stored by any cache (a CDN in front of the
    content origin) but must be revalidated with CKAN on every use, a cheap
    304 via its ETag, so making a dataset private takes effect at once.
    Private content may be kept only by the viewer's own browser, for one
    url_ttl. Anything the route didn't explicitly mark (errors) not at all.
    pdf_view_plus adds two: "immutable" for the versioned PDF.js files, and
    "max-age=N" for a public dataset's PDF linked from another site, which
    is public at its source, so caching it revokes nothing."""
    if not status.startswith(("200", "304")):
        return "no-store"
    if visibility == "immutable":
        return "public, max-age=31536000, immutable"
    if visibility and visibility.startswith("max-age="):
        return "public, " + visibility
    if visibility == "public":
        return "public, no-cache"
    if visibility == "private":
        return "private, max-age={}".format(settings.url_ttl())
    return "no-store"
