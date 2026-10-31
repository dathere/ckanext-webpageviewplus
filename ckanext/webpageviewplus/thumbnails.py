"""Thumbnails in the resource list (the webpage_view_plus_thumbnails plugin).

A resource with a Website (Plus) view gets a small image instead of CKAN's
format icon: a copy CKAN made when the view was saved, served by CKAN, or the
logo of the well-known service it shows. Opening a dataset page never
contacts another site and never makes CKAN fetch anything.

Where the image comes from, first match wins:

1. An image the editor picked: an image uploaded to the same dataset. It's
   read from CKAN's storage when the view is saved, re-encoded small, and
   stored per view.
2. The service's logo, when the resource has only this view and its URL is
   a well-known service's (services.LOGOS, shipped with the package).

Pillow is imported only to make a copy, so the main plugin never needs it.
"""
from __future__ import annotations

import datetime
import hashlib
import io
import logging
import os
import re
from typing import Any, Optional

import sqlalchemy as sa
from flask import Blueprint, Response, abort, g, has_app_context, request
from markupsafe import Markup
from sqlalchemy.dialects.postgresql import insert

import ckan.model as model
import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from ckan.common import current_user

from ckanext.webpageviewplus import content, services

log = logging.getLogger(__name__)

VIEW_TYPE = "webpage_view_plus"
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


def is_image_upload(resource: dict[str, Any]) -> bool:
    """An image uploaded to CKAN that a view may use as its thumbnail. The
    file's own bytes decide whether it's really one (encode)."""
    if resource.get("url_type") != "upload":
        return False
    if (resource.get("format") or "").lower() in IMAGE_FORMATS:
        return True
    if (resource.get("mimetype") or "").lower() in IMAGE_MIMETYPES:
        return True
    _, dot, ext = (resource.get("url") or "").rpartition(".")
    return bool(dot) and ext.lower() in IMAGE_FORMATS


def choices(package: Optional[dict[str, Any]]) -> list[dict[str, str]]:
    """The images in this dataset the view form offers."""
    return [
        {"id": r["id"], "name": r.get("name") or r["id"]}
        for r in (package or {}).get("resources") or []
        if is_image_upload(r)
    ]


def source_for(config: dict[str, Any]) -> Optional[str]:
    """What a view's stored copy must have been made from to count."""
    picked = config.get("thumbnail_resource")
    return "resource:" + picked if picked else None


def encode(data: bytes) -> Optional[bytes]:
    """A small JPEG of the image in ``data`` (PNG, JPEG, GIF or WebP; the
    first frame of an animation), metadata dropped, transparency on white.
    None for anything else, or for an image too large to decode."""
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data), formats=PILLOW_FORMATS) as im:
            width, height = im.size
            if width * height > MAX_PIXELS:
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
    """The bytes of an image uploaded to this dataset, if CKAN keeps them
    on its own filesystem (cloud storages aren't read yet)."""
    resource = model.Resource.get(resource_id)
    if resource is None or resource.state != "active" or resource.package_id != package_id:
        return None
    resource_dict = content.resource_dict(resource)
    if not is_image_upload(resource_dict):
        return None
    path = content.local_path(resource_dict)
    if not path or not os.path.isfile(path) or os.path.getsize(path) > MAX_UPLOAD_BYTES:
        return None
    with open(path, "rb") as f:
        return f.read(MAX_UPLOAD_BYTES + 1)


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


def after_save(view: dict[str, Any]) -> None:
    """Make (or drop) a saved view's copy. Never fails the save."""
    if view.get("view_type") != VIEW_TYPE:
        return
    try:
        source = source_for(view)
        resource = model.Resource.get(view.get("resource_id"))
        image = None
        if source and resource is not None:
            data = read_upload(view["thumbnail_resource"], resource.package_id)
            image = encode(data) if data else None
        if image:
            store(view["id"], source, image)
        else:
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


def _picked_active(config: dict[str, Any], pkg: Optional[dict[str, Any]] = None) -> bool:
    """Whether the image a view's copy was made from is still in its
    dataset: a deleted image's copy must stop showing at once, however it
    was deleted. The dataset page's own resource list answers it without a
    query."""
    picked = config.get("thumbnail_resource")
    if not picked:
        return False
    resources = (pkg or {}).get("resources")
    if resources is not None:
        return any(r.get("id") == picked for r in resources)
    image = model.Resource.get(picked)
    return image is not None and image.state == "active"


def refresh_from(resource_id: str) -> None:
    """Remake (or drop) every copy made from this image, after it was
    updated or deleted. Never fails the action."""
    try:
        with model.meta.engine.connect() as connection:
            view_ids = [row.view_id for row in connection.execute(
                sa.select(table.c.view_id).where(table.c.source == "resource:" + resource_id))]
    except sa.exc.SQLAlchemyError:
        log.warning("webpageviewplus: refreshing thumbnails failed", exc_info=True)
        return
    for view_id in view_ids:
        view = model.ResourceView.get(view_id)
        if view is None:
            continue
        after_save(dict(view.config or {}, id=view.id, view_type=view.view_type,
                        resource_id=view.resource_id))


def for_resource(pkg: dict[str, Any], res: dict[str, Any]) -> Optional[dict[str, Any]]:
    """What the resource list shows for ``res`` instead of CKAN's icon and
    format: ``src`` (an image, or None for the icon) and ``service`` (a
    name for the label, or None for the format). None leaves it as it is."""
    package_id = (pkg or {}).get("id") or res.get("package_id")
    if not package_id or not res.get("id"):
        return None
    views = _package_views(package_id).get(res["id"]) or []
    plus = next((v for v in views if v["view_type"] == VIEW_TYPE), None)
    if plus is None:
        return None
    config = plus["config"] or {}
    service = None
    if len(views) == 1:
        found = services.suggest(config.get("page_url") or res.get("url") or "")
        service = found.service if found else None
    src = None
    if plus["digest"] and plus["source"] == source_for(config) \
            and _picked_active(config, pkg):
        src = toolkit.url_for(
            "webpageviewplus_thumbnails.serve", view_id=plus["id"], v=plus["digest"][:16])
    elif service in services.LOGOS:
        src = toolkit.h.url_for_static(
            "/webpageviewplus/logos/{}.svg".format(services.LOGOS[service]))
    if not src and not service:
        return None
    return {"src": src, "service": service}


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
    if view is None or view.view_type != VIEW_TYPE:
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
    if row is None or row["source"] != source_for(view.config or {}) \
            or not _picked_active(view.config or {}):
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
