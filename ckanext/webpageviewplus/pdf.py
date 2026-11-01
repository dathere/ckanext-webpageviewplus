"""pdf_view_plus: PDFs shown with PDF.js, served from the content origin.

Browsers' own PDF viewers don't help here: Chrome blocks its viewer in any
sandboxed frame, and phones don't show PDFs inline. So the extension ships
PDF.js's viewer (``pdfjs/<version>/``, made by ``scripts/vendor_pdfjs.py``)
and serves it on the content origin only, framed with the data-schematic
sandbox: it runs with the origin uploads already run with, never CKAN's.

PDF.js only opens files from its own origin, so the PDF comes from there
too: an upload through ``content.stored_file``, or a PDF linked from another
site, fetched by CKAN with the frameability guards (public addresses only,
redirects re-checked, a size cap, a deadline) and served only if it really
is a PDF. Private datasets' PDF URLs are signed per view, as uploads' are.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import quote, urlsplit

from flask import Blueprint, Response, abort, redirect, request, send_file, send_from_directory

import ckan.model as model
import ckan.plugins.toolkit as toolkit

from ckanext.webpageviewplus import content, events, frame, frameability, settings

log = logging.getLogger(__name__)

VIEW_TYPE = "pdf_view_plus"
PDF_FORMATS = ("pdf", "pdf/a")
PDFJS_VERSION = "6.5.438"
PDFJS_DIR = os.path.join(os.path.dirname(__file__), "pdfjs", PDFJS_VERSION)
HEIGHT_MODES = ("fixed", "viewport")
DEFAULT_HEIGHT = ("fixed", "800")
FETCH_SECONDS = 30
USER_AGENT = "ckanext-webpageviewplus pdf_view_plus"
# A PDF's header must start within its first 1024 bytes.
HEADER_WINDOW = 1024

ASSET_PATH = "/webpageviewplus/pdfjs/<version>/<path:filename>"
FILE_PATH = "/webpageviewplus/pdf/<view_id>/<path:filename>"
OPEN_PATH = "/webpageviewplus/pdf-view/<view_id>"

# Only these files are served from the vendored viewer, each with its type:
# module scripts are refused without a JavaScript type, and WebAssembly is
# compiled while streaming only as application/wasm.
ASSET_TYPES = {
    ".html": "text/html",
    ".mjs": "text/javascript",
    ".js": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".ftl": "text/plain",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".gif": "image/gif",
    ".wasm": "application/wasm",
    ".bcmap": "application/octet-stream",
    ".pfb": "application/octet-stream",
    ".ttf": "font/ttf",
    ".icc": "application/octet-stream",
}

# The viewer page's policy, as PDF.js's own (a meta tag in viewer.html) but
# with connect-src limited to its origin. 'wasm-unsafe-eval' runs PDF.js's
# image and colour decoders.
VIEWER_CSP = (
    "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; "
    "style-src 'self'; img-src 'self' data: blob:; "
    "font-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; "
    "base-uri 'none'; form-action 'none'; object-src 'none'"
)

pdf = Blueprint("webpageviewplus_pdf", __name__)


# ---------------------------------------------------------------- the source


def source(config: dict[str, Any], resource: dict[str, Any]) -> Optional[tuple[str, str]]:
    """What a view shows: ("upload", "") for the resource's own uploaded
    file, ("link", url) for a PDF elsewhere (the view's pdf_url, else the
    resource's URL), or None when there's nothing it can show. Never touches
    the storage or the network, so it's safe while rendering."""
    pdf_url = (config or {}).get("pdf_url")
    if pdf_url:
        return ("link", pdf_url) if frame.is_http_url(pdf_url) else None
    if resource.get("url_type") == "upload":
        return ("upload", "") if content._readable_uploader(resource) is not None else None
    url = resource.get("url") or ""
    return ("link", url) if frame.is_http_url(url) else None


def host_allowed(url: str) -> bool:
    allowed = settings.allowed_hosts()
    return (
        not allowed
        or frame.is_ckan_host(url, settings.ckan_hosts())
        or frame.url_host(url) in allowed
    )


def looks_like_pdf(data: bytes) -> bool:
    return b"%PDF-" in data[:HEADER_WINDOW]


def _cache_dir() -> Optional[str]:
    storage = toolkit.config.get("ckan.storage_path")
    return os.path.join(storage, "webpageviewplus", "pdf") if storage else None


def _fresh(path: str) -> bool:
    try:
        return time.time() - os.path.getmtime(path) < settings.pdf_cache_seconds()
    except OSError:
        return False


def cached_pdf(url: str) -> Optional[str]:
    """A file on disk holding the PDF at ``url``: fetched at most once per
    pdf_cache_seconds and shared by every visitor (and every CKAN worker
    using the same storage_path), so a busy view doesn't make CKAN download
    the file again for each one. None when it can't be fetched or isn't a
    PDF, or when ckan.storage_path isn't set; a failed refresh keeps no
    stale copy."""
    folder = _cache_dir()
    if folder is None:
        return None
    path = os.path.join(folder, hashlib.sha256(url.encode()).hexdigest() + ".pdf")
    if _fresh(path):
        return path
    body = frameability.fetch_body(
        url, time.monotonic() + FETCH_SECONDS, settings.pdf_max_size(), USER_AGENT)
    if body is None or not looks_like_pdf(body.data):
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    os.makedirs(folder, exist_ok=True)
    # A name of its own: two threads may fetch the same PDF at once.
    fd, temp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body.data)
        os.replace(temp, path)  # atomic: a reader never sees half a file
    except OSError:
        try:
            os.remove(temp)
        except OSError:
            pass
        return None
    _prune(folder)
    return path


def _prune(folder: str) -> None:
    """Drop copies nobody has refreshed for a day past their lifetime (a
    view deleted, or its link changed)."""
    limit = time.time() - settings.pdf_cache_seconds() - 86400
    try:
        for entry in os.scandir(folder):
            if entry.is_file() and entry.stat().st_mtime < limit:
                os.remove(entry.path)
    except OSError:
        pass


# -------------------------------------------------------------------- URLs


def _subject(view_id: str) -> str:
    # Signed apart from content URLs: a view id is never a resource id.
    return "pdf:" + view_id


def _filename(kind: str, url: str, resource: dict[str, Any]) -> str:
    path = url if kind == "link" else (resource.get("url") or "")
    try:
        name = os.path.basename(urlsplit(path).path)
    except ValueError:
        name = ""
    return quote(name or "document.pdf")


def file_path(view_id: str, kind: str, url: str, resource: dict[str, Any],
              private: bool) -> str:
    """The PDF's path on the content origin (no origin: PDF.js opens files
    from its own origin only)."""
    path = "/webpageviewplus/pdf/{}/{}".format(view_id, _filename(kind, url, resource))
    if private:
        w = content._window()
        path += "?w={}&s={}".format(w, content._signature(_subject(view_id), w))
    return path


@lru_cache(maxsize=1)
def shipped_locales() -> frozenset[str]:
    try:
        with open(os.path.join(PDFJS_DIR, "web", "locale", "locale.json")) as f:
            return frozenset(json.load(f))
    except (OSError, ValueError):
        return frozenset()


# CKAN's names for languages whose PDF.js folders are named differently.
_LANG_ALIASES = {
    "zh-hans-cn": "zh-cn", "zh-hans": "zh-cn", "zh-cn": "zh-cn",
    "zh-hant-tw": "zh-tw", "zh-hant": "zh-tw", "zh-tw": "zh-tw",
    "no": "nb-no", "nb": "nb-no",
}
# Which variant a bare language means, when several are shipped.
_PREFERRED = {"es": "es-es", "pt": "pt-br", "en": "en-us", "zh": "zh-cn"}


def pdfjs_lang(ckan_lang: str) -> str:
    """PDF.js's name for the CKAN page's language, among the shipped ones,
    or en-us. PDF.js itself only tries the exact name and its first part."""
    lang = (ckan_lang or "en").replace("_", "-").lower()
    lang = _LANG_ALIASES.get(lang, lang)
    locales = shipped_locales()
    if lang in locales:
        return lang
    base = lang.split("-", 1)[0]
    if base in locales:
        return base
    preferred = _PREFERRED.get(base)
    if preferred in locales:
        return preferred
    variants = sorted(loc for loc in locales if loc.split("-", 1)[0] == base)
    return variants[0] if variants else "en-us"


def viewer_url(view_id: str, kind: str, url: str, resource: dict[str, Any],
               private: bool, lang: str) -> str:
    return "{}/webpageviewplus/pdfjs/{}/web/viewer.html?file={}&wvp_lang={}".format(
        settings.content_origin(), PDFJS_VERSION,
        quote(file_path(view_id, kind, url, resource, private), safe=""),
        quote(pdfjs_lang(lang)),
    )


def open_path(view_id: str) -> str:
    return toolkit.url_for("webpageviewplus_pdf.open_viewer", view_id=view_id)


# ------------------------------------------------------------------ routes


def _no_content_host() -> None:
    if not content.on_content_host() or not settings.isolating_content_origin():
        abort(404)


def _headers(response: Response, policy: str) -> Response:
    response.headers["Content-Security-Policy"] = policy + "; " + content.frame_ancestors()
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@pdf.route(ASSET_PATH)
def asset(version: str, filename: str):
    """The vendored viewer's files, on the content host only."""
    _no_content_host()
    mimetype = ASSET_TYPES.get(os.path.splitext(filename)[1].lower())
    if version != PDFJS_VERSION or mimetype is None:
        abort(404)
    # send_from_directory refuses paths leaving the folder (404).
    response = send_from_directory(PDFJS_DIR, filename, mimetype=mimetype, conditional=True)
    request.environ[content.CACHE_ENVIRON_KEY] = "immutable"
    return _headers(response, VIEWER_CSP)


def _view_and_resource(view_id: str) -> tuple[model.ResourceView, dict[str, Any], model.Package]:
    view = model.ResourceView.get(view_id)
    if view is None or view.view_type != VIEW_TYPE:
        abort(404)
    resource_obj = model.Resource.get(view.resource_id)
    if resource_obj is None or resource_obj.state != "active":
        abort(404)
    package = resource_obj.package
    if package is None or package.state not in ("active", "draft"):
        abort(404)
    return view, content.resource_dict(resource_obj), package


@pdf.route(FILE_PATH)
def serve(view_id: str, filename: str):
    """A view's PDF, for PDF.js on the same origin."""
    _no_content_host()
    view, resource, package = _view_and_resource(view_id)
    private = not content.is_public(package.id)
    if private and not content.signature_valid(
        _subject(view_id), request.args.get("w", ""), request.args.get("s", "")
    ):
        events.emit("signature_refused", resource_id=resource["id"], view_id=view_id)
        abort(404)
    found = source(view.config or {}, resource)
    if found is None:
        abort(404)
    kind, url = found
    if kind == "upload":
        try:
            stored = content.stored_file(resource)
        except Exception:
            log.warning("webpageviewplus: can't read the upload of resource %s", resource["id"])
            stored = None
        if stored is None:
            abort(404)
        request.environ[content.CACHE_ENVIRON_KEY] = "private" if private else "public"
        response = content.file_response(stored, "application/pdf")
    else:
        if not host_allowed(url):
            abort(404)
        path = cached_pdf(url)
        if path is None:
            events.emit("pdf_fetch_failed", view_id=view_id, host=frame.url_host(url))
            abort(404)
        request.environ[content.CACHE_ENVIRON_KEY] = (
            "private" if private else "max-age={}".format(settings.pdf_cache_seconds()))
        response = send_file(path, mimetype="application/pdf", conditional=True, etag=True)
    response.headers["Content-Disposition"] = "inline"
    # Opened on its own, it may not run anything.
    return _headers(response, "sandbox; default-src 'none'")


@pdf.route(OPEN_PATH)
def open_viewer(view_id: str):
    """On CKAN's own host: re-checks the visitor may see the view, then
    redirects to a freshly signed viewer URL. Reload and Load use it, as the
    frame's own signed URL may have expired."""
    if content.on_content_host():
        abort(404)
    user = toolkit.current_user
    context = {"user": user.name if getattr(user, "is_authenticated", False) else ""}
    try:
        view = toolkit.get_action("resource_view_show")(context, {"id": view_id})
        resource = toolkit.get_action("resource_show")(context, {"id": view["resource_id"]})
    except (toolkit.NotAuthorized, toolkit.ObjectNotFound):
        abort(404)
    if view.get("view_type") != VIEW_TYPE or not settings.isolating_content_origin():
        abort(404)
    found = source(view, resource)
    if found is None:
        abort(404)
    private = not content.is_public(resource["package_id"])
    return redirect(viewer_url(view_id, found[0], found[1], resource, private, toolkit.h.lang()))
