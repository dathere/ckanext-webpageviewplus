"""Views whose page can't be shown: a stored frameability result per view,
and the checks behind the "can't be embedded" card and `check-views`.

Visitors never make CKAN fetch anything: the page is checked when the view
is saved (with frameability_check on) and by `ckan webpageviewplus
check-views`, and rendering only reads the stored result. A result counts
only for the URL it was taken for.
"""
from __future__ import annotations

import datetime
import logging
import time
from typing import Any, Optional
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

import ckan.model as model

from ckanext.webpageviewplus import events, frame, frameability, settings

log = logging.getLogger(__name__)

# The whole check when a view is saved, redirects included: the editor
# waits for it.
SAVE_SECONDS = 5

metadata = sa.MetaData()
table = sa.Table(
    "webpage_view_plus_frameability",
    metadata,
    sa.Column("view_id", sa.UnicodeText, primary_key=True),
    sa.Column("url", sa.UnicodeText, nullable=False),
    sa.Column("verdict", sa.UnicodeText, nullable=False),
    sa.Column("reason", sa.UnicodeText, nullable=False, server_default=""),
    sa.Column("problem", sa.UnicodeText, nullable=False, server_default=""),
    sa.Column("status", sa.Integer),
    sa.Column("checked_at", sa.DateTime, nullable=False),
)


def third_party_url(page_url: str, resource_url: str, url_type: str) -> Optional[str]:
    """The URL a webpage_view_plus view frames when it's another site's page,
    else None: uploads, CKAN's own pages, non-http(s) URLs and hosts outside
    allowed_hosts are never fetched."""
    url = page_url or resource_url
    if not page_url and url_type == "upload":
        return None
    if not frame.is_http_url(url) or frame.is_ckan_host(url, settings.ckan_hosts()):
        return None
    allowed = settings.allowed_hosts()
    if allowed and frame.url_host(url) not in allowed:
        return None
    return url


def mixed_content(url: str) -> bool:
    """Whether browsers would refuse to frame ``url`` on this site because
    it's http:// and the site is https:// (loopback hosts are exempt)."""
    try:
        site_scheme = urlsplit(settings.site_url()).scheme.lower()
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return False
    host = frame.url_host(url)
    loopback = host in settings.LOOPBACK_HOSTS or host.endswith(".localhost")
    return site_scheme == "https" and scheme == "http" and not loopback


def store(view_id: str, url: str, result: frameability.Probe) -> None:
    row = {
        "view_id": view_id,
        "url": url,
        "verdict": result.verdict,
        "reason": result.reason,
        "problem": result.problem,
        "status": result.status,
        "checked_at": datetime.datetime.utcnow(),
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
        log.warning("webpageviewplus: no frameability table; run "
                    "ckan db upgrade -p webpage_view_plus")
    except sa.exc.IntegrityError:
        pass  # the view was deleted meanwhile


def stored(view_id: str, url: str) -> Optional[dict[str, Any]]:
    """The stored result for this view, if it was taken for ``url``."""
    query = sa.select(table).where(table.c.view_id == view_id, table.c.url == url)
    try:
        with model.meta.engine.connect() as connection:
            row = connection.execute(query).first()
    except sa.exc.ProgrammingError:
        return None
    return dict(row._mapping) if row else None


def refused(view_id: Optional[str], url: str) -> Optional[str]:
    """Why a third-party page can't be shown, from what's already known:
    "mixed" (http on an https site) or "blocked" (its stored check found it
    refusing to be framed). None means show the frame."""
    if mixed_content(url):
        return "mixed"
    if view_id and settings.frameability_check():
        row = stored(view_id, url)
        if row and row["verdict"] == frameability.BLOCKED:
            return "blocked"
    return None


def check_view(view_id: str, url: str, deadline: Optional[float] = None) -> frameability.Probe:
    result = frameability.probe(url, frameability.ckan_identity(settings.site_url()), deadline)
    store(view_id, url, result)
    if result.verdict == frameability.BLOCKED:
        events.emit("frameability_blocked", host=frame.url_host(url))
    return result


def after_save(view: dict[str, Any]) -> None:
    """Check a webpage_view_plus view's page once it's saved. Never fails
    the save."""
    if view.get("view_type") != "webpage_view_plus" or not settings.frameability_check():
        return
    try:
        resource = model.Resource.get(view.get("resource_id"))
        if resource is None:
            return
        url = third_party_url(view.get("page_url") or "", resource.url or "", resource.url_type or "")
        if url and not mixed_content(url):
            check_view(view["id"], url, time.monotonic() + SAVE_SECONDS)
    except Exception:
        log.warning("webpageviewplus: checking a saved view failed", exc_info=True)
