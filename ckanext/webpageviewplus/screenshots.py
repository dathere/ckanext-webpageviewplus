"""Screenshots of third-party pages that refuse to be framed (the
webpage_view_plus_thumbnails plugin, with screenshot_backend set).

A Website (Plus) view whose page sends X-Frame-Options or frame-ancestors
shows a card instead of a frame (health.refused). This module gives that
card a picture of the page, and the resource list a thumbnail when nothing
else provides one. Screenshots are taken in the background
(`ckan webpageviewplus screenshots`), never while a page renders, by one of
three backends:

- cloudflare: Cloudflare Browser Run's /screenshot quick action;
- microlink: the Microlink API;
- gotenberg: a Gotenberg 9.5+ container the site runs.

The hosted two are sent the page's URL, so they only take public datasets'
pages. Gotenberg runs a browser on the site's own network, so it's used only
after it has refused two private addresses (gotenberg_safe): its
CHROMIUM_DENY_PRIVATE_IPS setting is what keeps that browser off the site's
network.

Each backend's answer is decoded by Pillow and re-encoded: a card-sized JPEG
and a thumbnail, stored per view in their own table with the URL they were
taken for, so a screenshot stops counting once the view frames something
else, the page becomes frameable, or the view stops qualifying.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import io
import json
import logging
import time
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import requests
import sqlalchemy as sa
from flask import Blueprint, Response, abort, g, has_app_context, request
from sqlalchemy.dialects.postgresql import insert

import ckan.model as model
import ckan.plugins.toolkit as toolkit
from ckan.common import current_user

from ckanext.webpageviewplus import (
    content, events, frame, frameability, health, repos, settings, thumbnails,
)

log = logging.getLogger(__name__)

VIEW_TYPE = "webpage_view_plus"
SOURCE_PREFIX = "screenshot:"
# Every backend captures this viewport (CSS px, scale 1), top of the page
# only: the shape the card shows.
VIEWPORT = (1350, 940)
CARD_QUALITY = 82
CAPTURE_QUALITY = 85
# The most read from a backend's answer, and the most pixels decoded.
MAX_ANSWER_BYTES = 20 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_PIXELS = 40_000_000
# Each backend answers only once the page has loaded.
SECONDS = {"cloudflare": 60, "microlink": 60, "gotenberg": 45}
CONNECT_SECONDS = 10
USER_AGENT = "ckanext-webpageviewplus screenshot"
# Why an attempt failed, as stored and shown (translated) in the view form.
KINDS = ("page_failed", "rate_limited", "backend_error", "no_image", "unsafe_backend")
# Gotenberg must refuse these, or it's never used.
CANARIES = ("http://127.0.0.1/", "http://169.254.169.254/")
SAFE_SECONDS = 60 * 60
# The page re-checked before a hosted capture (capture()).
RECHECK_SECONDS = 10
UNSAFE_SECONDS = 5 * 60
_REDIS_PREFIX = "webpageviewplus:screenshots:"
# Common cookie-consent banners, hidden on backends that don't block them.
BANNER_CSS = (
    "#onetrust-consent-sdk,#CybotCookiebotDialog,#usercentrics-root,"
    "#didomi-host,.cc-window,#cookie-law-info-bar,#cookiescript_injected,"
    ".qc-cmp2-container,#truste-consent-track,.osano-cm-window,"
    "#cmpbox,#cmpbox2,.fc-consent-root{display:none!important}"
)

PATH = "/webpageviewplus/screenshot/<view_id>"

metadata = sa.MetaData()
table = sa.Table(
    "webpage_view_plus_screenshots",
    metadata,
    sa.Column("view_id", sa.UnicodeText, primary_key=True),
    sa.Column("source", sa.UnicodeText, nullable=False),
    sa.Column("backend", sa.UnicodeText, nullable=False),
    sa.Column("final_host", sa.UnicodeText, nullable=False, server_default=""),
    sa.Column("image", sa.LargeBinary),
    sa.Column("thumb", sa.LargeBinary),
    sa.Column("digest", sa.UnicodeText),
    sa.Column("taken_at", sa.DateTime),
    sa.Column("attempted_at", sa.DateTime, nullable=False),
    sa.Column("last_error", sa.UnicodeText, nullable=False, server_default=""),
)

screenshot = Blueprint("webpageviewplus_screenshots", __name__)


class Failure(Exception):
    """Why a screenshot wasn't taken: one of KINDS, and for rate_limited,
    when to try again (a time since 1970). Never carries a URL or a secret:
    the message is the kind."""

    def __init__(self, kind: str, until: float = 0.0):
        super().__init__(kind)
        self.kind = kind
        self.until = until


@dataclasses.dataclass(frozen=True)
class Shot:
    data: bytes
    final_url: str = ""


@dataclasses.dataclass(frozen=True)
class Answer:
    status: int
    headers: dict[str, str]  # names in lower case
    body: bytes


def enabled() -> bool:
    return settings.screenshot_backend() != "none"


def hosted() -> bool:
    return settings.screenshot_backend() in settings.HOSTED_SCREENSHOT_BACKENDS


# --- Sending ------------------------------------------------------------


def _send(method: str, url: str, *, guarded: bool, seconds: float, max_bytes: int,
          **kwargs: Any) -> Answer:
    """One request, redirects never followed (a token must not travel to
    another host), at most ``max_bytes`` of the answer read. ``guarded``:
    frameability's guards (public addresses only, each connection's peer
    checked, the whole of it cut at the deadline), for the hosted APIs.
    Unguarded only for gotenberg_url, the site's own service. Any failure
    to get an answer is backend_error; its text, which can hold the URL,
    is never logged."""
    deadline = time.monotonic() + seconds
    kwargs.setdefault("headers", {}).setdefault("User-Agent", USER_AGENT)
    kwargs.update(allow_redirects=False, stream=True,
                  timeout=(CONNECT_SECONDS, seconds))
    try:
        if guarded:
            with frameability._guarded(deadline):
                if frameability._address_kind(url, deadline) != "public":
                    raise Failure("backend_error")
                return _read(frameability._http_request(method, url, **kwargs),
                             max_bytes, deadline)
        with requests.Session() as session:
            session.trust_env = False  # never through a proxy from the environment
            return _read(session.request(method, url, **kwargs), max_bytes, deadline)
    except Failure:
        raise
    except Exception:
        log.debug("webpageviewplus: screenshot request failed (%s)", method)
        raise Failure("backend_error")


def _read(response: requests.Response, max_bytes: int, deadline: float) -> Answer:
    try:
        data = bytearray()
        if max_bytes:
            for chunk in response.iter_content(64 * 1024):
                data += chunk
                if len(data) > max_bytes or time.monotonic() > deadline:
                    raise Failure("no_image")
        return Answer(response.status_code,
                      {k.lower(): v for k, v in response.headers.items()}, bytes(data))
    finally:
        response.close()


def _is_image(answer: Answer) -> bool:
    return answer.headers.get("content-type", "").split(";")[0].strip().lower() \
        in thumbnails.IMAGE_MIMETYPES


def _next_utc_day() -> float:
    now = datetime.datetime.now(datetime.timezone.utc)
    tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.timestamp()


def _retry_at(answer: Answer, fallback: float) -> float:
    return repos._rate_limit(429, answer.headers) or (time.time() + fallback)


# --- Backends -----------------------------------------------------------


def _cloudflare(url: str) -> Shot:
    endpoint = "https://api.cloudflare.com/client/v4/accounts/{}/browser-run/screenshot".format(
        settings.cloudflare_account_id())
    body = {
        "url": url,
        "viewport": {"width": VIEWPORT[0], "height": VIEWPORT[1], "deviceScaleFactor": 1},
        "screenshotOptions": {"type": "jpeg", "quality": CAPTURE_QUALITY},
        "gotoOptions": {"waitUntil": "networkidle2", "timeout": 45000},
        "addStyleTag": [{"content": BANNER_CSS}],
    }
    answer = _send(
        "POST", endpoint, guarded=True, seconds=SECONDS["cloudflare"],
        max_bytes=MAX_ANSWER_BYTES,
        headers={"Authorization": "Bearer " + settings.cloudflare_api_token()},
        # A query parameter, not a body field (the body refuses unknown
        # keys); 0 turns off Cloudflare's own cache of the answer.
        params={"cacheTTL": "0"},
        json=body,
    )
    if answer.status == 200 and _is_image(answer):
        return Shot(answer.body)
    if answer.status == 429:
        # Workers Free: 10 browser minutes a UTC day, and 1 request every
        # 10 seconds; only the daily limit says "today".
        daily = b"today" in answer.body[:2048].lower()
        raise Failure("rate_limited", _next_utc_day() if daily else _retry_at(answer, 10))
    # 400: Cloudflare refused the request's body, which is ours to fix;
    # a page that can't load is a 422.
    if answer.status in (400, 401, 403, 404) or answer.status >= 500:
        raise Failure("backend_error")
    if answer.status == 200:
        raise Failure("no_image")
    raise Failure("page_failed")


def _microlink_host(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == "microlink.io" or host.endswith(".microlink.io"))


def _microlink(url: str) -> Shot:
    key = settings.microlink_api_key()
    endpoint = "https://pro.microlink.io/" if key else "https://api.microlink.io/"
    params = {
        "url": url,
        "screenshot": "true",
        "meta": "false",
        "viewport.width": str(VIEWPORT[0]),
        "viewport.height": str(VIEWPORT[1]),
        "viewport.deviceScaleFactor": "1",
        "screenshot.type": "jpeg",
        "adblock": "true",
    }
    answer = _send(
        "GET", endpoint, guarded=True, seconds=SECONDS["microlink"],
        max_bytes=MAX_JSON_BYTES, params=params,
        headers={"x-api-key": key} if key else {},
    )
    if answer.status == 429:
        raise Failure("rate_limited", _retry_at(answer, 24 * 60 * 60))
    if answer.status in (401, 403) or answer.status >= 500:
        raise Failure("backend_error")
    try:
        found = json.loads(answer.body)
    except ValueError:
        raise Failure("backend_error")
    if not isinstance(found, dict):
        raise Failure("backend_error")
    data = found.get("data")
    if answer.status != 200 or found.get("status") != "success" or not isinstance(data, dict):
        raise Failure("page_failed")
    shot_url = ((data.get("screenshot") or {}) if isinstance(data.get("screenshot"), dict)
                else {}).get("url") or ""
    # From an untrusted answer: only Microlink's own https hosts, guarded,
    # and no redirect followed (one would reach a host never checked).
    if not isinstance(shot_url, str) or not _microlink_host(shot_url):
        raise Failure("no_image")
    image = _send("GET", shot_url, guarded=True, seconds=SECONDS["microlink"],
                  max_bytes=MAX_ANSWER_BYTES)
    if image.status != 200:
        raise Failure("no_image")
    final = data.get("url") if isinstance(data.get("url"), str) else ""
    return Shot(image.body, final)


def _gotenberg_form(url: str) -> dict[str, tuple[None, str]]:
    fields = {
        "url": url,
        "width": str(VIEWPORT[0]),
        "height": str(VIEWPORT[1]),
        "clip": "true",  # the viewport only, not the whole page
        "format": "jpeg",
        "quality": str(CAPTURE_QUALITY),
        # Wait until at most two connections are open, not for full network
        # idle, which pages that poll never reach before Gotenberg's timeout.
        "skipNetworkAlmostIdleEvent": "false",
    }
    return {k: (None, v) for k, v in fields.items()}


def _gotenberg_post(url: str, max_bytes: int) -> Answer:
    return _send(
        "POST", settings.gotenberg_url() + "/forms/chromium/screenshot/url",
        guarded=False, seconds=SECONDS["gotenberg"], max_bytes=max_bytes,
        files=_gotenberg_form(url), auth=settings.gotenberg_auth(),
    )


def _gotenberg(url: str) -> Shot:
    if not gotenberg_safe():
        raise Failure("unsafe_backend")
    answer = _gotenberg_post(url, MAX_ANSWER_BYTES)
    if answer.status == 200 and _is_image(answer):
        return Shot(answer.body)
    if answer.status == 429:
        raise Failure("rate_limited", time.time() + 60)
    # 403: its outbound policy refused the page (a private address); 409:
    # the page answered 4xx or 5xx; 503: the page didn't finish in time.
    if answer.status in (403, 409, 503):
        raise Failure("page_failed")
    if answer.status == 200:
        raise Failure("no_image")
    raise Failure("backend_error")


BACKENDS: dict[str, Callable[[str], Shot]] = {
    "cloudflare": _cloudflare,
    "microlink": _microlink,
    "gotenberg": _gotenberg,
}


# --- Gotenberg's outbound policy ------------------------------------------


def _redis():
    from ckan.lib.redis import connect_to_redis
    return connect_to_redis()


def check_gotenberg() -> bool:
    """Whether Gotenberg refuses every canary address (exactly 403, its
    outbound policy's answer). Nothing else counts: a 404, a timeout or an
    image means it reached, or may have reached, the site's own network.
    The answers are never read, since one could be a metadata service's."""
    for canary in CANARIES:
        try:
            if _gotenberg_post(canary, 0).status != 403:
                return False
        except Failure:
            return False
    return True


def gotenberg_safe(force: bool = False) -> bool:
    """check_gotenberg, remembered in Redis: a pass for SAFE_SECONDS, a
    failure for UNSAFE_SECONDS. Without Redis it never passes."""
    key = _REDIS_PREFIX + "gotenberg-safe:" + hashlib.sha256(
        settings.gotenberg_url().encode()).hexdigest()[:16]
    try:
        redis = _redis()
        if not force:
            cached = redis.get(key)
            if cached is not None:
                return cached in (b"1", "1")
        safe = check_gotenberg()
        redis.setex(key, SAFE_SECONDS if safe else UNSAFE_SECONDS, "1" if safe else "0")
    except Exception:
        log.warning("webpageviewplus: can't check Gotenberg's outbound policy without Redis")
        return False
    if not safe:
        events.emit("screenshot_backend_unsafe", backend="gotenberg")
    return safe


# --- Rate limits ------------------------------------------------------------


def limited_until(backend: str) -> float:
    try:
        value = _redis().get(_REDIS_PREFIX + "rate-limited:" + backend)
        return float(value) if value is not None else 0.0
    except Exception:
        return 0.0


def _remember_limit(backend: str, until: float) -> None:
    seconds = int(until - time.time()) + 1
    if seconds <= 0:
        return
    try:
        _redis().setex(_REDIS_PREFIX + "rate-limited:" + backend, seconds, str(until))
    except Exception:
        # Not remembered: the next run asks the backend and is refused again.
        log.debug("webpageviewplus: screenshot limit not remembered")


def take(url: str) -> Shot:
    """A screenshot of ``url`` from the configured backend, or Failure."""
    backend = settings.screenshot_backend()
    if backend not in BACKENDS:
        raise Failure("backend_error")
    until = limited_until(backend)
    if until > time.time():
        raise Failure("rate_limited", until)
    try:
        return BACKENDS[backend](url)
    except Failure as e:
        if e.kind == "rate_limited":
            _remember_limit(backend, e.until)
        raise
    except Exception:
        # An answer no branch expected: one view's failure, never the end of
        # a cron run. Its text may hold the URL, so it isn't logged.
        log.warning("webpageviewplus: unexpected answer from screenshot backend %s", backend)
        raise Failure("backend_error")


# --- Which views ------------------------------------------------------------


def target(view_id: Optional[str], view_type: str, config: dict[str, Any],
           resource_url: str, url_type: str, private: bool,
           verdict: Optional[str] = None) -> Optional[str]:
    """The page a view's screenshot is of, if it qualifies: a Website (Plus)
    view of a third-party page whose stored check found it refusing to be
    framed, that hasn't opted out of automatic thumbnails or picked one, in
    a public dataset unless the backend is the site's own. ``verdict``, if
    known, saves looking up the stored check."""
    if not enabled() or not view_id or view_type != VIEW_TYPE:
        return None
    if toolkit.asbool(config.get("no_auto_thumbnail")) or config.get("thumbnail_resource"):
        return None
    if private and hosted():
        return None
    url = health.third_party_url(config.get("page_url") or "", resource_url, url_type)
    if not url or health.mixed_content(url):
        return None
    if verdict is None:
        row = health.stored(view_id, url)
        verdict = row["verdict"] if row else None
    return url if verdict == frameability.BLOCKED else None


def source(url: str) -> str:
    return SOURCE_PREFIX + url


# --- Storing ----------------------------------------------------------------


def encode(data: bytes) -> Optional[tuple[bytes, bytes]]:
    """The card image (the top of the page, at most VIEWPORT, JPEG) and the
    list thumbnail made from it, or None if ``data`` isn't an image Pillow
    may decode."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data), formats=("JPEG", "PNG", "WEBP")) as im:
            width, height = im.size
            if width * height > MAX_PIXELS or min(width, height) < thumbnails.MIN_SIDE:
                return None
            im = im.convert("RGB")
            # A full-page answer: keep its top, in the viewport's shape.
            tall = round(width * VIEWPORT[1] / VIEWPORT[0])
            if height > tall:
                im = im.crop((0, 0, width, tall))
            im.thumbnail(VIEWPORT, Image.Resampling.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=CARD_QUALITY, optimize=True)
            card = out.getvalue()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None
    thumb = thumbnails.encode(card)
    return (card, thumb) if thumb else None


def _upsert(row: dict[str, Any]) -> None:
    stmt = insert(table).values(row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["view_id"],
        set_={k: stmt.excluded[k] for k in row if k != "view_id"},
    )
    try:
        with model.meta.engine.begin() as connection:
            connection.execute(stmt)
    except sa.exc.ProgrammingError:
        log.warning("webpageviewplus: no screenshots table; run "
                    "ckan db upgrade -p webpage_view_plus")
    except sa.exc.IntegrityError:
        pass  # the view was deleted meanwhile


def store(view_id: str, url: str, backend: str, card: bytes, thumb: bytes,
          final_host: str = "") -> None:
    now = datetime.datetime.utcnow()
    _upsert({
        "view_id": view_id,
        "source": source(url),
        "backend": backend,
        "final_host": final_host,
        "image": card,
        "thumb": thumb,
        "digest": hashlib.sha256(card).hexdigest(),
        "taken_at": now,
        "attempted_at": now,
        "last_error": "",
    })


def store_failure(view_id: str, url: str, backend: str, kind: str) -> None:
    """Record a failed attempt. A screenshot already taken of the same page
    is kept; one of another page is dropped, since it no longer counts."""
    now = datetime.datetime.utcnow()
    existing = stored_meta(view_id)
    if existing and existing["source"] == source(url):
        try:
            with model.meta.engine.begin() as connection:
                connection.execute(table.update().where(table.c.view_id == view_id)
                                   .values(attempted_at=now, last_error=kind))
        except sa.exc.SQLAlchemyError:
            log.warning("webpageviewplus: couldn't record a failed screenshot", exc_info=True)
        return
    _upsert({
        "view_id": view_id, "source": source(url), "backend": backend, "final_host": "",
        "image": None, "thumb": None, "digest": None, "taken_at": None,
        "attempted_at": now, "last_error": kind,
    })


def stored(view_id: str) -> Optional[dict[str, Any]]:
    try:
        with model.meta.engine.connect() as connection:
            row = connection.execute(sa.select(table).where(table.c.view_id == view_id)).first()
    except sa.exc.SQLAlchemyError:
        return None
    return dict(row._mapping) if row else None


def stored_meta(view_id: str) -> Optional[dict[str, Any]]:
    """stored() without the image bytes, for decisions and links."""
    columns = [c for c in table.c if c.name not in ("image", "thumb")]
    try:
        with model.meta.engine.connect() as connection:
            row = connection.execute(
                sa.select(*columns).where(table.c.view_id == view_id)).first()
    except sa.exc.SQLAlchemyError:
        return None
    return dict(row._mapping) if row else None


def delete(view_id: str) -> None:
    try:
        with model.meta.engine.begin() as connection:
            connection.execute(table.delete().where(table.c.view_id == view_id))
    except sa.exc.SQLAlchemyError:
        # No table yet (migration not run): nothing to delete.
        log.debug("webpageviewplus: screenshot not deleted")


def after_save(view: dict[str, Any]) -> None:
    """The editor's opt-out deletes the view's screenshot. Changes that
    aren't the editor's choice (the page becomes frameable, the dataset
    stops being public, the backend is turned off) only hide it, until the
    view qualifies again."""
    if view.get("view_type") == VIEW_TYPE and toolkit.asbool(view.get("no_auto_thumbnail")):
        delete(view["id"])


def _view_target(view: model.ResourceView) -> Optional[str]:
    resource = model.Resource.get(view.resource_id)
    if resource is None or resource.state != "active":
        return None
    package = model.Package.get(resource.package_id)
    if package is None:
        return None
    # A hosted backend is sent the URL: only for what anyone may see, judged
    # as content.py judges it (drafts and permission-label plugins too), not
    # only by the private flag.
    if hosted() and not content.is_public(resource.package_id):
        return None
    return target(view.id, view.view_type, view.config or {}, resource.url or "",
                  resource.url_type or "", bool(package.private))


def capture(view_id: str) -> str:
    """Take and store a screenshot for one view: "taken", "skipped" (it
    doesn't qualify) or the Failure kind."""
    view = model.ResourceView.get(view_id)
    url = _view_target(view) if view is not None else None
    if not url:
        return "skipped"
    backend = settings.screenshot_backend()
    if hosted():
        # Cloudflare and Microlink screenshot error pages (a 404 or 500
        # comes back as an image), so check the page ourselves first:
        # still refusing framing, and not answering with an error.
        check = health.check_view(view_id, url, time.monotonic() + RECHECK_SECONDS)
        if check.problem:  # gone, http-error, unreachable, private
            store_failure(view_id, url, backend, "page_failed")
            events.emit("screenshot_failed", kind="page_failed", backend=backend,
                        host=frame.url_host(url))
            return "page_failed"
        if check.verdict != frameability.BLOCKED:
            return "skipped"
    try:
        shot = take(url)
        encoded = encode(shot.data)
        if encoded is None:
            raise Failure("no_image")
    except Failure as e:
        store_failure(view_id, url, backend, e.kind)
        events.emit("screenshot_failed", kind=e.kind, backend=backend, host=frame.url_host(url))
        return e.kind
    final = frame.url_host(shot.final_url) if frame.is_http_url(shot.final_url) else ""
    # The view may have been saved (opted out, say) while the backend
    # worked: store only what it still asks for.
    model.Session.refresh(view)
    if _view_target(view) != url:
        return "skipped"
    store(view_id, url, backend, encoded[0], encoded[1],
          final if final and final != frame.url_host(url) else "")
    return "taken"


# --- Showing ----------------------------------------------------------------


def _counts(row: Optional[dict[str, Any]], url: Optional[str]) -> bool:
    return bool(row and url and row["digest"] and row["source"] == source(url))


def _withheld(package_id: Optional[str]) -> bool:
    """Whether serve() would refuse the dataset's screenshots for a hosted
    backend (not public to anonymous visitors, as _view_target judges), so
    pages don't show a broken image instead of their fallback."""
    return hosted() and not (package_id and content.is_public(package_id))


def for_card(view_id: Optional[str], config: dict[str, Any], resource: dict[str, Any],
             package: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The card's screenshot of a refused page: ``src``, ``host`` (where the
    page landed) and ``taken`` (a datetime), or None."""
    if not view_id:
        return None
    url = target(view_id, VIEW_TYPE, config or {}, resource.get("url") or "",
                 resource.get("url_type") or "", bool((package or {}).get("private")))
    row = stored_meta(view_id) if url else None
    if not _counts(row, url) or _withheld((package or {}).get("id") or resource.get("package_id")):
        return None
    return {
        "src": toolkit.url_for("webpageviewplus_screenshots.serve", view_id=view_id,
                               v=row["digest"][:16]),
        "host": row["final_host"] or frame.url_host(url),
        "taken": row["taken_at"],
    }


def _rows(package_id: str) -> dict[str, dict[str, Any]]:
    """Every screenshot of the dataset's views that has an image, with the
    stored frameability check for the URL it was taken of: one query per
    dataset page, no image bytes."""
    shots = table.alias("shots")
    checks = health.table.alias("checks")
    rv = model.ResourceView
    query = (
        sa.select(shots.c.view_id, shots.c.source, shots.c.digest, checks.c.verdict)
        .join(rv, rv.id == shots.c.view_id)
        .join(model.Resource, model.Resource.id == rv.resource_id)
        .outerjoin(checks, sa.and_(checks.c.view_id == shots.c.view_id,
                                   sa.literal(SOURCE_PREFIX) + checks.c.url == shots.c.source))
        .where(model.Resource.package_id == package_id, shots.c.digest.isnot(None))
    )
    try:
        rows = model.Session.execute(query).all()
    except sa.exc.ProgrammingError:
        # No screenshots table yet: the list still renders.
        model.Session.rollback()
        return {}
    return {row.view_id: dict(row._mapping) for row in rows}


def _package_rows(package_id: str) -> dict[str, dict[str, Any]]:
    if not has_app_context():
        return _rows(package_id)
    cache = g.setdefault("webpageviewplus_screenshots", {})
    if package_id not in cache:
        cache[package_id] = _rows(package_id)
    return cache[package_id]


def for_list(view: dict[str, Any], res: dict[str, Any], pkg: dict[str, Any]) -> Optional[str]:
    """The resource list's screenshot thumbnail for ``view`` (a row of
    thumbnails._views), as a URL, or None."""
    if not enabled() or view.get("view_type") != VIEW_TYPE:
        return None
    package_id = (pkg or {}).get("id") or res.get("package_id")
    row = _package_rows(package_id).get(view["id"]) if package_id else None
    if not row:
        return None
    url = target(view["id"], VIEW_TYPE, view.get("config") or {}, res.get("url") or "",
                 res.get("url_type") or "", bool((pkg or {}).get("private")), row["verdict"] or "")
    if not _counts(row, url) or _withheld(package_id):
        return None
    return toolkit.url_for("webpageviewplus_screenshots.serve", view_id=view["id"],
                           size="thumb", v=row["digest"][:16])


@screenshot.route(PATH)
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
    if not _counts(row, _view_target(view)):
        abort(404)
    image = row["thumb"] if request.args.get("size") == "thumb" else row["image"]
    if not image:
        abort(404)
    if content.is_public(resource.package_id):
        request.environ.pop("__no_cache__", None)
        request.environ[content.REVALIDATE_ENVIRON_KEY] = True
    else:
        request.environ["__no_cache__"] = True
    response = Response(image, mimetype=thumbnails.MIMETYPE)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.set_etag(hashlib.sha256(image).hexdigest())
    return response.make_conditional(request)


# --- Backfill (ckan webpageviewplus screenshots) ------------------------------


def due(refresh: bool = False) -> list[tuple[str, str]]:
    """(view id, page URL) of every view that qualifies and has no current
    screenshot, or one older than screenshot_seconds (every one with
    ``refresh``)."""
    if not enabled():
        return []
    rv = model.ResourceView
    query = (
        sa.select(rv.id, rv.view_type, rv.config, model.Resource.url, model.Resource.url_type,
                  model.Package.private, health.table.c.url.label("checked_url"),
                  health.table.c.verdict)
        .join(model.Resource, model.Resource.id == rv.resource_id)
        .join(model.Package, model.Package.id == model.Resource.package_id)
        .join(health.table, health.table.c.view_id == rv.id)
        .where(rv.view_type == VIEW_TYPE, model.Resource.state == "active",
               model.Package.state == "active",
               health.table.c.verdict == frameability.BLOCKED)
        .order_by(rv.id)
    )
    oldest = datetime.datetime.utcnow() - datetime.timedelta(seconds=settings.screenshot_seconds())
    found = []
    for row in model.Session.execute(query).all():
        url = target(row.id, row.view_type, row.config or {}, row.url or "", row.url_type or "",
                     bool(row.private), row.verdict)
        # The stored check counts only for the URL the view frames now.
        if not url or url != row.checked_url:
            continue
        shot = stored_meta(row.id)
        if refresh or not _counts(shot, url) or shot["taken_at"] < oldest:
            found.append((row.id, url))
    return found
