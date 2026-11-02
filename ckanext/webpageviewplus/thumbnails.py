"""Thumbnails in the resource list (the webpage_view_plus_thumbnails plugin).

A resource with a Website (Plus) view gets a small image instead of CKAN's
format icon: a copy CKAN made when the view was saved, served by CKAN, or the
logo of the well-known service it shows. Opening a dataset page never
contacts another site and never makes CKAN fetch anything.

Where the image comes from, first match wins:

1. An image the editor picked: an image resource in the same dataset. An
   upload is read from CKAN's storage; a linked image is fetched (only
   with thumbnail_fetch on).
2. With thumbnail_fetch on and the view's no_auto_thumbnail unticked, for
   another site's page: a well-known service's fixed thumbnail address
   (services.thumbnail_address: YouTube, Datawrapper...), else the page's
   own preview image (og:image, then twitter:image), unless its service's
   is only a logo (services.GENERIC_PREVIEWS).
3. The service's logo, when the resource has only this view and its URL is
   a well-known service's (services.LOGOS, shipped with the package).

Images are fetched or read once, when the view is saved (or by
`ckan webpageviewplus check-views`), re-encoded small and stored per view
with the source they were made from, so a copy stops counting once the
view asks for something else. Fetches go through frameability.fetch_body,
with the frameability check's guards.

Pillow is imported only to make a copy, so the main plugin never needs it.
"""
from __future__ import annotations

import datetime
import hashlib
import io
import logging
import os
import re
import time
import zipfile
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlsplit

import sqlalchemy as sa
from flask import Blueprint, Response, abort, g, has_app_context, request
from markupsafe import Markup
from sqlalchemy.dialects.postgresql import insert

import ckan.model as model
import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from ckan.common import current_user

from ckanext.webpageviewplus import content, frame, frameability, health, services, settings

log = logging.getLogger(__name__)

VIEW_TYPE = "webpage_view_plus"
# View types that may have a copy: PDF (Plus) views take a picked image only.
PDF_VIEW_TYPE = "pdf_view_plus"
VIEW_TYPES = (VIEW_TYPE, PDF_VIEW_TYPE)
# A PDF (Plus) view's thumbnail_resource may name this instead of an image:
# the PDF's first page, drawn by PDF.js in the editor's browser (pdf.py's
# viewer script) and sent by the view form, as the server never renders PDFs.
FIRST_PAGE = "pdf:first-page"
PLUGIN = "webpage_view_plus_thumbnails"

# The stored copy fits in this box, keeping its aspect ratio; the list shows
# it at a fixed size, so it never shifts as images load.
SIZE = (320, 180)
MIMETYPE = "image/jpeg"
IMAGE_FORMATS = ("png", "jpg", "jpeg", "webp", "gif")
IMAGE_MIMETYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
# What Pillow may decode: never SVG, which can carry scripts.
PILLOW_FORMATS = ("PNG", "JPEG", "GIF", "WEBP")
# An upload larger than this isn't read, and an image with more pixels than
# this (by its header, before decoding) isn't decoded.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
# Smaller than this on either side is a placeholder (Tableau answers a
# missing workbook with a 1 x 1 PNG) or a tracking pixel, not a picture.
MIN_SIDE = 16
# Fetching (thumbnail_fetch): the whole of it when a view is saved, page and
# image together, since the editor waits; how much of a page is read for its
# preview image; and the largest image fetched.
FETCH_SECONDS = 5
MAX_PAGE_BYTES = 512 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
USER_AGENT = "ckanext-webpageviewplus thumbnail"
# How long a first page drawn in the view form waits for the view's save.
DRAFT_SECONDS = 24 * 60 * 60
# Office files that carry their own preview picture: OpenDocument always
# (Thumbnails/thumbnail.png), Word, Excel and PowerPoint files when saved
# with one (docProps/thumbnail.*; WMF and EMF ones can't be decoded). The
# older binary formats keep theirs elsewhere and aren't read.
OFFICE_PREVIEW_EXTENSIONS = ("odt", "ods", "odp", "docx", "xlsx", "pptx", "ppsx")
OFFICE_PREVIEWS = (
    "Thumbnails/thumbnail.png",
    "docProps/thumbnail.jpeg",
    "docProps/thumbnail.jpg",
    "docProps/thumbnail.png",
)
_DOWNLOAD_RE = re.compile(r"/resource/([0-9a-f-]{36})/download/")

PATH = "/webpageviewplus/thumbnail/<view_id>"

metadata = sa.MetaData()
table = sa.Table(
    "webpage_view_plus_thumbnails",
    metadata,
    sa.Column("view_id", sa.UnicodeText, primary_key=True),
    sa.Column("source", sa.UnicodeText, nullable=False),
    sa.Column("image", sa.LargeBinary, nullable=False),
    sa.Column("digest", sa.UnicodeText, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

thumbnail = Blueprint("webpageviewplus_thumbnails", __name__)


def enabled() -> bool:
    return plugins.plugin_loaded(PLUGIN)


def _looks_like_image(resource: dict[str, Any]) -> bool:
    if (resource.get("format") or "").lower() in IMAGE_FORMATS:
        return True
    if (resource.get("mimetype") or "").lower() in IMAGE_MIMETYPES:
        return True
    try:
        path = urlsplit(resource.get("url") or "").path
    except ValueError:
        return False
    _, dot, ext = path.rpartition(".")
    return bool(dot) and ext.lower() in IMAGE_FORMATS


def is_image_upload(resource: dict[str, Any]) -> bool:
    """An image uploaded to CKAN that a view may use as its thumbnail. The
    file's own bytes decide whether it's really one (encode)."""
    return resource.get("url_type") == "upload" and _looks_like_image(resource)


def is_image_link(resource: dict[str, Any]) -> bool:
    """A linked image (http or https) a view may use, fetched when it's
    saved: only with thumbnail_fetch on."""
    return (
        not resource.get("url_type")
        and frame.is_http_url(resource.get("url") or "")
        and _looks_like_image(resource)
    )


def is_pickable(resource: dict[str, Any]) -> bool:
    return is_image_upload(resource) or (settings.thumbnail_fetch() and is_image_link(resource))


def choices(package: Optional[dict[str, Any]]) -> list[dict[str, str]]:
    """The images in this dataset the view form offers."""
    return [
        {"id": r["id"], "name": r.get("name") or r["id"]}
        for r in (package or {}).get("resources") or []
        if is_pickable(r)
    ]


def auto(config: dict[str, Any]) -> bool:
    """Whether this view may take the page's own preview image."""
    return settings.thumbnail_fetch() and not toolkit.asbool(config.get("no_auto_thumbnail"))


def _stamp(last_modified: Any) -> str:
    """A resource's last_modified as the resource dict shows it."""
    if isinstance(last_modified, datetime.datetime):
        return last_modified.isoformat()
    return str(last_modified or "")


def first_page_source(
    config: dict[str, Any], resource_url: str, url_type: str, last_modified: Any,
) -> str:
    """The source of a copy of a PDF's first page: which file (the view's
    pdf_url, a link, or an upload by its file name, as the database keeps
    an upload's url bare and the resource list sees its download URL) and
    the resource's last change, so a replaced file's copy stops counting."""
    pdf_url = config.get("pdf_url")
    if pdf_url:
        target = pdf_url
    elif url_type == "upload":
        target = "upload:" + (resource_url or "").rsplit("/", 1)[-1]
    else:
        target = resource_url
    return "pdf-page:{}#{}".format(target, _stamp(last_modified))


def source_for(
    config: dict[str, Any], resource_url: str, url_type: str, view_type: str = VIEW_TYPE,
    last_modified: Any = None,
) -> Optional[str]:
    """What a view's stored copy must have been made from to count:
    ``resource:<id>`` (a picked image); ``office:<url>`` (the preview
    picture inside the Office file Microsoft's viewer shows: a CKAN upload,
    or while fetching is allowed one elsewhere); or, while fetching is
    allowed, for the page the view frames: ``service:<url>`` (its
    service's fixed thumbnail address) or ``page:<url>`` (its preview
    image). PDF (Plus) views take a picked image, or ``pdf-page:...``
    (first_page_source) for FIRST_PAGE."""
    picked = config.get("thumbnail_resource")
    if picked == FIRST_PAGE:
        return first_page_source(config, resource_url, url_type, last_modified) \
            if view_type == PDF_VIEW_TYPE else None
    if picked:
        return "resource:" + picked
    if view_type != VIEW_TYPE or toolkit.asbool(config.get("no_auto_thumbnail")):
        return None
    office = office_file(config.get("page_url") or "")
    if office and (frame.is_ckan_host(office, settings.ckan_hosts()) or settings.thumbnail_fetch()):
        return "office:" + office
    if not auto(config):
        return None
    url = health.third_party_url(config.get("page_url") or "", resource_url or "", url_type or "")
    if not url:
        return None
    if services.thumbnail_address(url):
        return "service:" + url
    found = services.suggest(url)
    if found and found.service in services.GENERIC_PREVIEWS:
        return None
    return "page:" + url


def office_file(page_url: str) -> Optional[str]:
    """The Office file a view of Microsoft's viewer shows, if it's one of
    the formats that carry a preview picture."""
    found = services.suggest(page_url) if page_url else None
    if found is None or found.service != services.OFFICE:
        return None
    src = (parse_qs(urlsplit(found.embed_url).query).get("src") or [""])[0]
    try:
        path = urlsplit(src).path
    except ValueError:
        return None
    name = path.rsplit("/", 1)[-1]
    extension = name.rpartition(".")[2].lower() if "." in name else ""
    return src if extension in OFFICE_PREVIEW_EXTENSIONS else None


def office_preview(data: bytes) -> Optional[bytes]:
    """The preview picture inside an Office file, as a copy, or None. Only
    that one entry is read, and only if it's small."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        return None
    with archive:
        for name in OFFICE_PREVIEWS:
            try:
                info = archive.getinfo(name)
            except KeyError:
                continue
            if info.file_size > MAX_IMAGE_BYTES:
                return None
            try:
                with archive.open(info) as f:
                    raw = f.read(MAX_IMAGE_BYTES + 1)
            except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
                return None
            return encode(raw) if len(raw) <= MAX_IMAGE_BYTES else None
    return None


def office_bytes(url: str, package_id: str, deadline: float) -> Optional[bytes]:
    """The Office file at ``url``: read from storage when it's an upload to
    this dataset on CKAN's own host (no fetch), else fetched while fetching
    is allowed. At most MAX_UPLOAD_BYTES either way."""
    if frame.is_ckan_host(url, settings.ckan_hosts()):
        match = _DOWNLOAD_RE.search(urlsplit(url).path)
        target = model.Resource.get(match.group(1)) if match else None
        if target is None or target.state != "active" or target.package_id != package_id \
                or target.url_type != "upload":
            return None
        try:
            stored = content.stored_file(content.resource_dict(target))
            if stored is None or stored.size > MAX_UPLOAD_BYTES:
                return None
            body = bytearray()
            for chunk in stored.chunks():
                body += chunk
                if len(body) > MAX_UPLOAD_BYTES:
                    return None
            return bytes(body)
        except Exception:
            log.warning("webpageviewplus: can't read the Office file of resource %s", target.id)
            return None
    if not settings.thumbnail_fetch():
        return None
    body = frameability.fetch_body(url, deadline, MAX_UPLOAD_BYTES, USER_AGENT)
    return body.data if body else None


def encode(data: bytes) -> Optional[bytes]:
    """A small JPEG of the image in ``data`` (PNG, JPEG, GIF or WebP; the
    first frame of an animation), metadata dropped, transparency on white.
    None for anything else, or for an image too large to decode."""
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data), formats=PILLOW_FORMATS) as im:
            width, height = im.size
            if width * height > MAX_PIXELS or min(width, height) < MIN_SIDE:
                return None
            im.seek(0)
            if im.format == "JPEG":
                im.draft("RGB", (SIZE[0] * 2, SIZE[1] * 2))
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P", "PA"):
                im = im.convert("RGBA")
                flat = Image.new("RGB", im.size, "white")
                flat.paste(im, mask=im.getchannel("A"))
                im = flat
            else:
                im = im.convert("RGB")
            im.thumbnail(SIZE, Image.Resampling.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=80, optimize=True)
            return out.getvalue()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None


def read_upload(resource_id: str, package_id: str) -> Optional[bytes]:
    """The bytes of an image uploaded to this dataset, from any storage
    content.stored_file can read, or None (missing, unreadable, too big)."""
    resource = model.Resource.get(resource_id)
    if resource is None or resource.state != "active" or resource.package_id != package_id:
        return None
    resource_dict = content.resource_dict(resource)
    if not is_image_upload(resource_dict):
        return None
    try:
        stored = content.stored_file(resource_dict)
        if stored is None or stored.size > MAX_UPLOAD_BYTES:
            return None
        body = bytearray()
        for chunk in stored.chunks():
            body += chunk
            if len(body) > MAX_UPLOAD_BYTES:
                return None
    except Exception:
        log.warning("webpageviewplus: can't read the image of resource %s", resource_id)
        return None
    return bytes(body)


class _HeadDone(Exception):
    pass


class _Preview(HTMLParser):
    """The preview image a page's <head> names: og:image (or
    og:image:url), else twitter:image (or twitter:image:src)."""

    KEYS = ("og:image", "og:image:url", "twitter:image", "twitter:image:src")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "body":
            raise _HeadDone()
        if tag != "meta":
            return
        a = dict(attrs)
        key = (a.get("property") or a.get("name") or "").strip().lower()
        value = (a.get("content") or "").strip()
        if key in self.KEYS and value:
            self.found.setdefault(key, value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            raise _HeadDone()

    def image(self) -> Optional[str]:
        return next((self.found[k] for k in self.KEYS if k in self.found), None)


def preview_image_url(html: str, base: str) -> Optional[str]:
    """The page's preview image as an absolute http(s) URL, or None."""
    parser = _Preview()
    try:
        parser.feed(html)
        parser.close()
    except _HeadDone:
        pass
    except Exception:  # whatever malformed markup makes the parser raise
        log.debug("webpageviewplus: parsing a page for its image failed", exc_info=True)
    found = parser.image()
    if not found:
        return None
    try:
        url = urljoin(base, found)
    except ValueError:
        return None
    return url if frame.is_http_url(url) else None


def _text(body: frameability.Body) -> str:
    try:
        return body.data.decode(body.charset or "utf-8", errors="replace")
    except LookupError:
        return body.data.decode("utf-8", errors="replace")


def fetch_image(url: str, deadline: float) -> Optional[bytes]:
    """A small copy of the image at ``url``, or None. The URL comes from
    an editor or another site's page, so it gets every guard itself."""
    if not frame.is_http_url(url):
        return None
    body = frameability.fetch_body(url, deadline, MAX_IMAGE_BYTES, USER_AGENT)
    return encode(body.data) if body else None


def page_image(url: str, deadline: float) -> Optional[bytes]:
    """A small copy of the preview image of the page at ``url``, or None."""
    body = frameability.fetch_body(url, deadline, MAX_PAGE_BYTES, USER_AGENT, truncate=True)
    if body is None or body.content_type not in ("text/html", "application/xhtml+xml"):
        return None
    image_url = preview_image_url(_text(body), body.url)
    return fetch_image(image_url, deadline) if image_url else None


def _make(
    source: str,
    resource: model.Resource,
    deadline: float,
    memo: Optional[dict[tuple[str, str], Optional[bytes]]],
) -> Optional[bytes]:
    """A copy made from ``source``, for a view of ``resource``. ``memo``
    lets one run fetch each URL once."""
    kind, _, ref = source.partition(":")
    if kind == "resource":
        picked = model.Resource.get(ref)
        if picked is None or picked.state != "active" \
                or picked.package_id != resource.package_id:
            return None
        picked_dict = content.resource_dict(picked)
        if is_image_upload(picked_dict):
            data = read_upload(ref, resource.package_id)
            return encode(data) if data else None
        if not (settings.thumbnail_fetch() and is_image_link(picked_dict)):
            return None
        key, fetch = ("image", picked_dict["url"]), fetch_image
    elif kind == "page":
        key, fetch = ("page", ref), page_image
    elif kind == "service":
        address = services.thumbnail_address(ref)
        if not address:
            return None
        key, fetch = ("image", address), fetch_image
    elif kind == "office":
        package_id = resource.package_id

        def fetch(url: str, deadline: float) -> Optional[bytes]:
            data = office_bytes(url, package_id, deadline)
            return office_preview(data) if data else None
        # office_bytes reads an upload only for a view in its own dataset:
        # a run's memo mustn't hand one dataset's copy to another.
        local = frame.is_ckan_host(ref, settings.ckan_hosts())
        key = ("office " + (package_id if local else ""), ref)
    else:
        return None
    if memo is not None and key in memo:
        return memo[key]
    image = fetch(key[1], deadline)
    if memo is not None:
        memo[key] = image
    return image


def store(view_id: str, source: str, image: bytes) -> None:
    row = {
        "view_id": view_id,
        "source": source,
        "image": image,
        "digest": hashlib.sha256(image).hexdigest(),
        "created_at": datetime.datetime.utcnow(),
    }
    stmt = insert(table).values(row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["view_id"],
        set_={k: stmt.excluded[k] for k in row if k != "view_id"},
    )
    try:
        with model.meta.engine.begin() as connection:
            connection.execute(stmt)
    except sa.exc.ProgrammingError:
        log.warning("webpageviewplus: no thumbnails table; run "
                    "ckan db upgrade -p webpage_view_plus")
    except sa.exc.IntegrityError:
        pass  # the view was deleted meanwhile


def delete(view_id: str) -> None:
    try:
        with model.meta.engine.begin() as connection:
            connection.execute(table.delete().where(table.c.view_id == view_id))
    except sa.exc.ProgrammingError:
        pass


def stored(view_id: str) -> Optional[dict[str, Any]]:
    try:
        with model.meta.engine.connect() as connection:
            row = connection.execute(
                sa.select(table).where(table.c.view_id == view_id)).first()
    except sa.exc.ProgrammingError:
        return None
    return dict(row._mapping) if row else None


def after_save(
    view: dict[str, Any],
    deadline: Optional[float] = None,
    keep: bool = False,
    memo: Optional[dict[tuple[str, str], Optional[bytes]]] = None,
) -> None:
    """Make (or drop) a saved view's copy. Never fails the save. With
    ``keep`` (view saves and check-views), a copy whose source is unchanged
    stays when making a new one fails, so a site that's down for a moment
    doesn't lose its thumbnails."""
    if view.get("view_type") not in VIEW_TYPES:
        return
    try:
        resource = model.Resource.get(view.get("resource_id"))
        source = image = None
        if resource is not None:
            source = source_for(view, resource.url or "", resource.url_type or "",
                                view["view_type"], resource.last_modified)
        if source and source.startswith("pdf-page:"):
            # Made in the editor's browser (store_first_page, as a draft),
            # never here: take a matching draft (a linked PDF's is newer
            # than its copy under the same source), else keep a copy that
            # still matches, else drop the copy.
            draft = _draft(view["id"])
            if draft and draft["source"] == source:
                store(view["id"], source, draft["image"])
            else:
                row = stored(view["id"])
                if not (row and row["source"] == source):
                    delete(view["id"])
            _drop_draft(view["id"])
            return
        if source:
            if deadline is None:
                deadline = time.monotonic() + FETCH_SECONDS
            image = _make(source, resource, deadline, memo)
        if image and source:
            store(view["id"], source, image)
            return
        if keep and source:
            row = stored(view["id"])
            if row and row["source"] == source:
                return
        delete(view["id"])
    except Exception:
        log.warning("webpageviewplus: making a thumbnail failed", exc_info=True)


def _views(package_id: str) -> dict[str, list[dict[str, Any]]]:
    """Every view of the dataset's resources, in view order, with its stored
    copy's source and digest: one query per dataset page."""
    rv = model.ResourceView
    query = (
        sa.select(rv.id, rv.resource_id, rv.view_type, rv.config,
                  table.c.source, table.c.digest)
        .join(model.Resource, model.Resource.id == rv.resource_id)
        .outerjoin(table, table.c.view_id == rv.id)
        .where(model.Resource.package_id == package_id,
               model.Resource.state == "active")
        .order_by(rv.resource_id, rv.order)
    )
    try:
        rows = model.Session.execute(query).all()
    except sa.exc.ProgrammingError:
        # No thumbnails table yet: the rest of the page still renders.
        model.Session.rollback()
        return {}
    by_resource: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_resource.setdefault(row.resource_id, []).append(dict(row._mapping))
    return by_resource


def _package_views(package_id: str) -> dict[str, list[dict[str, Any]]]:
    if not has_app_context():
        return _views(package_id)
    cache = g.setdefault("webpageviewplus_thumbnails", {})
    if package_id not in cache:
        cache[package_id] = _views(package_id)
    return cache[package_id]


def _source_active(source: str, pkg: Optional[dict[str, Any]] = None) -> bool:
    """Whether a picked image a copy was made from is still in its dataset,
    and still one a view may use (a linked one only while fetching is
    allowed): a deleted image's copy must stop showing at once, however it
    was deleted. The same goes for an uploaded Office file's preview. The
    dataset page's own resource list answers it without a query. Page
    sources need nothing more than source_for."""
    kind, _, picked = source.partition(":")
    if kind == "office":
        match = _DOWNLOAD_RE.search(urlsplit(picked).path) \
            if frame.is_ckan_host(picked, settings.ckan_hosts()) else None
        if match is None:
            return True
        picked, usable = match.group(1), (lambda r: r.get("url_type") == "upload")
    elif kind != "resource":
        return kind in ("page", "service", "pdf-page")
    else:
        usable = is_pickable
    resources = (pkg or {}).get("resources")
    if resources is not None:
        found = next((r for r in resources if r.get("id") == picked), None)
        return found is not None and usable(found)
    row = model.Resource.get(picked)
    return row is not None and row.state == "active" and usable(content.resource_dict(row))


def refresh_from(resource_id: str) -> None:
    """Remake (or drop) every copy made from this image, or from this
    uploaded Office file, after it was updated or deleted. Never fails the
    action."""
    office = sa.and_(
        table.c.source.startswith("office:", autoescape=True),
        table.c.source.contains("/resource/{}/download/".format(resource_id), autoescape=True))
    try:
        with model.meta.engine.connect() as connection:
            view_ids = [row.view_id for row in connection.execute(
                sa.select(table.c.view_id).where(
                    (table.c.source == "resource:" + resource_id) | office))]
    except sa.exc.SQLAlchemyError:
        log.warning("webpageviewplus: refreshing thumbnails failed", exc_info=True)
        return
    for view_id in view_ids:
        view = model.ResourceView.get(view_id)
        if view is None:
            continue
        after_save(dict(view.config or {}, id=view.id, view_type=view.view_type,
                        resource_id=view.resource_id))


def first_page_ready(view: dict[str, Any], resource: dict[str, Any]) -> bool:
    """Whether a PDF (Plus) view has a first-page copy matching its file now.
    Only an upload's last_modified says its file changed: a linked PDF's
    copy is never taken as current, so the form redraws it each time."""
    if not view.get("id") or not enabled():
        return False
    if view.get("pdf_url") or resource.get("url_type") != "upload":
        return False
    source = first_page_source(view, resource.get("url") or "", resource.get("url_type") or "",
                               resource.get("last_modified"))
    return any(row and row["source"] == source
               for row in (stored(view["id"]), _draft(view["id"])))


def _draft_key(view_id: str) -> str:
    return "{}:webpageviewplus:first-page:{}".format(
        toolkit.config.get("ckan.site_id"), view_id)


def _draft(view_id: str) -> Optional[dict[str, Any]]:
    """A first page drawn for a view, waiting for the view's save. Drafts
    live in Redis (the thumbnails table's rows belong to saved views) and
    expire, so an abandoned edit leaves nothing behind."""
    from ckan.lib.redis import connect_to_redis
    try:
        found = connect_to_redis().hgetall(_draft_key(view_id))
    except Exception:
        log.warning("webpageviewplus: can't read a first-page draft", exc_info=True)
        return None
    if not found or b"source" not in found or b"image" not in found:
        return None
    return {"source": found[b"source"].decode(), "image": found[b"image"]}


def _store_draft(view_id: str, source: str, image: bytes) -> bool:
    from ckan.lib.redis import connect_to_redis
    key = _draft_key(view_id)
    try:
        redis = connect_to_redis()
        redis.hset(key, mapping={"source": source, "image": image})
        redis.expire(key, DRAFT_SECONDS)
    except Exception:
        log.warning("webpageviewplus: can't keep a first-page draft", exc_info=True)
        return False
    return True


def _drop_draft(view_id: str) -> None:
    from ckan.lib.redis import connect_to_redis
    try:
        connect_to_redis().delete(_draft_key(view_id))
    except Exception:
        log.warning("webpageviewplus: can't drop a first-page draft", exc_info=True)


def store_first_page(view: model.ResourceView, png: bytes) -> Optional[str]:
    """Store a picture of a PDF (Plus) view's first page, drawn by PDF.js
    in the editor's browser. It's re-encoded like any image, so only a
    valid picture is kept. It's a draft until the view is saved with its
    thumbnail set to FIRST_PAGE (after_save), so an edit that's cancelled
    leaves the view's copy alone. Returns its source, or None for anything
    that isn't a usable image."""
    resource = model.Resource.get(view.resource_id)
    if resource is None or len(png) > MAX_IMAGE_BYTES:
        return None
    image = encode(png)
    if image is None:
        return None
    source = first_page_source(view.config or {}, resource.url or "", resource.url_type or "",
                               resource.last_modified)
    if (view.config or {}).get("thumbnail_resource") == FIRST_PAGE:
        # Already the view's choice: there's no other thumbnail to keep, and
        # a save may have landed before this drawing did.
        store(view.id, source, image)
        return source
    return source if _store_draft(view.id, source, image) else None


def service_format(context: dict[str, Any], view: dict[str, Any]) -> None:
    """With service_format on: name the service in the resource's format
    when a view is saved on a resource whose only view shows a well-known
    service's page, and whose format is empty or HTML. Never overwrites
    another format, never reverted, never fails the save."""
    if view.get("view_type") != VIEW_TYPE or not settings.service_format():
        return
    try:
        resource = model.Resource.get(view.get("resource_id"))
        if resource is None or (resource.format or "").strip().lower() not in ("", "html"):
            return
        views = model.Session.query(model.ResourceView).filter(
            model.ResourceView.resource_id == resource.id).count()
        if views != 1:
            return
        found = services.suggest(view.get("page_url") or resource.url or "")
        # An Office document keeps its own format (DOCX...), not the viewer's name.
        if found is None or found.service == services.OFFICE:
            return
        # The editor may already change this resource (adding views needs
        # it); auth is skipped so a theme's stricter resource_update rules
        # can't fail a save that already succeeded.
        toolkit.get_action("resource_patch")(
            {"user": context.get("user"), "ignore_auth": True},
            {"id": resource.id, "format": found.service},
        )
    except Exception:
        log.warning("webpageviewplus: setting the service's format failed", exc_info=True)


def for_resource(pkg: dict[str, Any], res: dict[str, Any]) -> Optional[dict[str, Any]]:
    """What the resource list shows for ``res`` instead of CKAN's icon and
    format: ``src`` (an image, or None for the icon) and ``service`` (a
    name for the label, or None for the format). None leaves it as it is."""
    package_id = (pkg or {}).get("id") or res.get("package_id")
    if not package_id or not res.get("id"):
        return None
    views = _package_views(package_id).get(res["id"]) or []
    plus = next((v for v in views if v["view_type"] in VIEW_TYPES), None)
    if plus is None:
        return None
    config = plus["config"] or {}
    service = None
    if len(views) == 1 and plus["view_type"] == VIEW_TYPE:
        found = services.suggest(config.get("page_url") or res.get("url") or "")
        # An Office document keeps its own format label (DOCX...).
        service = found.service if found and found.service != services.OFFICE else None
    src = None
    logo = False
    source = source_for(config, res.get("url") or "", res.get("url_type") or "",
                        plus["view_type"], res.get("last_modified"))
    if source and plus["digest"] and plus["source"] == source \
            and _source_active(source, pkg):
        src = toolkit.url_for(
            "webpageviewplus_thumbnails.serve", view_id=plus["id"], v=plus["digest"][:16])
    elif service in services.LOGOS:
        src = toolkit.h.url_for_static(
            "/webpageviewplus/logos/{}.svg".format(services.LOGOS[service]))
        logo = True
    if not src and not service:
        return None
    return {"src": src, "service": service, "logo": logo}


# CKAN's own format label, the same in 2.10 to 2.12. Themes that change it
# keep their markup unchanged.
_FORMAT_LABEL = re.compile(r'<span class="format-label"[^>]*>.*?</span>', re.S)


def replace_format_label(markup: str, replacement: str) -> Markup:
    """CKAN's resource title markup with its format label swapped for ours,
    so each CKAN version keeps its own link attributes."""
    return Markup(_FORMAT_LABEL.sub(lambda _: str(replacement), str(markup), count=1))


@thumbnail.route(PATH)
def serve(view_id: str):
    view = model.ResourceView.get(view_id)
    if view is None or view.view_type not in VIEW_TYPES:
        abort(404)
    resource = model.Resource.get(view.resource_id)
    if resource is None or resource.state != "active":
        abort(404)
    try:
        toolkit.check_access(
            "package_show",
            {"user": getattr(current_user, "name", "") or ""},
            {"id": resource.package_id},
        )
    except (toolkit.NotAuthorized, toolkit.ObjectNotFound):
        abort(404)  # not 403: don't confirm that a private dataset exists
    row = stored(view_id)
    source = source_for(view.config or {}, resource.url or "", resource.url_type or "",
                        view.view_type, resource.last_modified)
    if row is None or not source or row["source"] != source or not _source_active(source):
        abort(404)
    # CKAN's response hook sets Cache-Control from __no_cache__ after this
    # view: private for private datasets, public otherwise (the copy is the
    # same for everyone who may see it).
    if content.is_public(resource.package_id):
        request.environ.pop("__no_cache__", None)
    else:
        request.environ["__no_cache__"] = True
    response = Response(row["image"], mimetype=MIMETYPE)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.set_etag(row["digest"])
    return response.make_conditional(request)
