"""The management interface for thumbnails and screenshots (owned by the
webpage_view_plus_thumbnails plugin): the four actions behind the dataset's
Thumbnails page and the admin tab, which API clients can call too.

- ``webpageviewplus_thumbnail_status`` (side-effect free): one row per
  Website (Plus) and PDF (Plus) view, for a dataset (``package_update``) or,
  without ``id``, for the whole site (sysadmins), filtered and paged.
- ``webpageviewplus_screenshot_request`` with ``ids`` (``request``): the
  view form's request for many views (actions.py keeps the one-view form).
- ``webpageviewplus_thumbnail_refresh``: remakes one view's thumbnail at
  once, or queues a remake per view for several.
- ``webpageviewplus_thumbnail_delete``: removes the stored thumbnail, the
  screenshot's image, or both.

Every change takes view ids and checks ``resource_view_update`` per view;
an id the caller may not update answers ``not_found``, as one that doesn't
exist does. Nothing here fetches a page or calls a screenshot backend
except a single-view refresh, which costs what a view save costs.
"""
from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any, Iterable, Optional

import sqlalchemy as sa
from flask import Blueprint, current_app, g, request

import ckan.authz as authz
import ckan.model as model
import ckan.plugins.toolkit as toolkit
from ckan.common import current_user

from ckanext.webpageviewplus import events, frameability, health, screenshots, settings, \
    thumbnails

log = logging.getLogger(__name__)

# The most view ids one request may name, and the admin listing's page size.
MAX_IDS = 500
PAGE_SIZE = 50
FILTERS = ("all", "failed", "stale", "blocked", "skipped", "opted_out")
REMOVE = ("thumbnail", "screenshot", "both")
REFRESH_PENDING_SECONDS = 15 * 60

# A stored thumbnail's source prefix, in the words the pages use.
SOURCE_KINDS = {
    "resource": "picked",
    "page": "page",
    "service": "service",
    "office": "office",
    "pdf-page": "first_page",
}


# --- Rows ------------------------------------------------------------------------


def _candidates(package_id: Optional[str] = None, q: str = "") -> list[Any]:
    rv = model.ResourceView
    query = (
        sa.select(rv.id, rv.title, rv.view_type, rv.config, rv.resource_id,
                  model.Resource.name.label("resource_name"), model.Resource.url,
                  model.Resource.url_type, model.Resource.last_modified,
                  model.Package.id.label("package_id"), model.Package.name.label("dataset"),
                  model.Package.title.label("dataset_title"), model.Package.private)
        .join(model.Resource, model.Resource.id == rv.resource_id)
        .join(model.Package, model.Package.id == model.Resource.package_id)
        .where(rv.view_type.in_(thumbnails.VIEW_TYPES), model.Resource.state == "active",
               model.Package.state == "active")
        .order_by(model.Package.name, model.Resource.position, rv.order)
    )
    if package_id:
        query = query.where(model.Package.id == package_id)
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(sa.or_(model.Package.name.ilike(pattern, escape="\\"),
                                   model.Package.title.ilike(pattern, escape="\\")))
    return model.Session.execute(query).all()


def _by_view(table: sa.Table, columns: list[Any], view_ids: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    try:
        with model.meta.engine.connect() as connection:
            for start in range(0, len(view_ids), 1000):
                chunk = view_ids[start:start + 1000]
                for row in connection.execute(
                        sa.select(table.c.view_id, *columns)
                        .where(table.c.view_id.in_(chunk))):
                    found[row.view_id] = dict(row._mapping)
    except sa.exc.SQLAlchemyError:
        # A table not made yet (ckan db upgrade -p webpage_view_plus): no rows.
        log.debug("webpageviewplus: %s not read", table.name)
    return found


def _metas(view_ids: list[str]) -> tuple[dict, dict, dict]:
    thumbs = _by_view(thumbnails.table, [thumbnails.table.c.source,
                                         thumbnails.table.c.created_at], view_ids)
    shot = screenshots.table.c
    shots = _by_view(screenshots.table, [shot.source, shot.digest, shot.taken_at,
                                         shot.attempted_at, shot.last_error], view_ids)
    checks = _by_view(health.table, [health.table.c.url, health.table.c.verdict], view_ids)
    return thumbs, shots, checks


def _flags(c: Any, thumb: Optional[dict], shot: Optional[dict],
           check: Optional[dict], oldest: datetime.datetime) -> set[str]:
    """The admin listing's filters a row matches, from the tables alone
    (no Redis): failed, stale, blocked (refuses framing, no screenshot of
    it), skipped (a private dataset with a hosted backend), opted_out."""
    config = c.config or {}
    flags = set()
    if toolkit.asbool(config.get("no_auto_thumbnail")):
        flags.add("opted_out")
    if c.view_type != screenshots.VIEW_TYPE:
        return flags
    url = health.third_party_url(config.get("page_url") or "", c.url or "", c.url_type or "")
    verdict = check["verdict"] if check and url and check["url"] == url else ""
    has_shot = bool(shot and url and shot["digest"] and shot["source"] == screenshots.source(url))
    if shot and shot["last_error"] and (
            not shot["digest"] or (shot["taken_at"] and shot["attempted_at"] > shot["taken_at"])):
        flags.add("failed")
    if has_shot and shot["taken_at"] and shot["taken_at"] < oldest:
        flags.add("stale")
    if screenshots.target(c.id, c.view_type, config, c.url or "", c.url_type or "",
                          bool(c.private), verdict) and not has_shot:
        flags.add("blocked")
    if verdict == frameability.BLOCKED and c.private and screenshots.hosted() \
            and "opted_out" not in flags and not config.get("thumbnail_resource"):
        flags.add("skipped")
    return flags


def _when(value: Any) -> Optional[str]:
    return value.isoformat() if value else None


def _row(c: Any, thumb: Optional[dict], flags: set[str]) -> dict[str, Any]:
    config = c.config or {}
    resource = {"id": c.resource_id, "package_id": c.package_id, "url": c.url or "",
                "url_type": c.url_type or "", "last_modified": c.last_modified}
    source = thumbnails.source_for(config, resource["url"], resource["url_type"],
                                   c.view_type, c.last_modified)
    stored = None
    if thumb:
        stored = {
            "kind": SOURCE_KINDS.get(thumb["source"].partition(":")[0], "other"),
            "created": _when(thumb["created_at"]),
            "in_use": bool(source and thumb["source"] == source
                           and thumbnails._source_active(source)),
        }
    # What the resource list shows: only the resource's first Plus view
    # decides it, as in thumbnails.for_resource.
    views = thumbnails._package_views(c.package_id).get(c.resource_id) or []
    first = next((v for v in views if v["view_type"] in thumbnails.VIEW_TYPES), None)
    shows = None
    if first is not None and first["id"] == c.id:
        found = thumbnails.for_resource({"id": c.package_id}, resource)
        if not found:
            shows = {"kind": "format"}
        elif found["src"] and found["logo"]:
            shows = {"kind": "logo", "src": found["src"], "service": found["service"]}
        elif found["src"]:
            kind = "thumbnail" if stored and stored["in_use"] else "screenshot"
            shows = {"kind": kind, "src": found["src"]}
        else:
            shows = {"kind": "name", "service": found["service"]}
    shot = None
    if c.view_type == screenshots.VIEW_TYPE:
        panel = screenshots.panel(c.id, config, resource, {"private": bool(c.private)})
        if panel:
            shot = {key: _when(value) if isinstance(value, datetime.datetime) else value
                    for key, value in panel.items()}
    return {
        "view_id": c.id,
        "view_type": c.view_type,
        "title": c.title or "",
        "resource_id": c.resource_id,
        "resource_name": c.resource_name or c.resource_id,
        "dataset": c.dataset,
        "dataset_title": c.dataset_title or c.dataset,
        "private": bool(c.private),
        "no_auto_thumbnail": toolkit.asbool(config.get("no_auto_thumbnail")),
        "thumbnail_resource": config.get("thumbnail_resource") or "",
        "thumbnail": stored,
        "shows": shows,
        "screenshot": shot,
        "flags": sorted(flags),
    }


def status_auth(context: dict[str, Any], data_dict: dict[str, Any]) -> dict[str, Any]:
    """A dataset's rows: anyone who may edit the dataset. The site-wide
    listing: sysadmins (who pass every auth function)."""
    if not data_dict.get("id"):
        return {"success": False}
    try:
        toolkit.check_access("package_update", dict(context), {"id": data_dict["id"]})
    except (toolkit.NotAuthorized, toolkit.ObjectNotFound):
        return {"success": False}
    return {"success": True}


def _page_number(value: Any) -> int:
    try:
        page = int(value or 1)
    except (TypeError, ValueError):
        raise toolkit.ValidationError({"page": [toolkit._("Must be a whole number.")]})
    if page < 1:
        raise toolkit.ValidationError({"page": [toolkit._("Must be a whole number.")]})
    return page


@toolkit.side_effect_free
def webpageviewplus_thumbnail_status(
    context: dict[str, Any], data_dict: dict[str, Any]
) -> dict[str, Any]:
    """Thumbnails and screenshots, one row per Website (Plus) and PDF (Plus)
    view. With ``id`` (a dataset's name or id): that dataset's rows. Without
    it (sysadmins): every view, with ``filter`` (one of FILTERS), ``q`` (in
    the dataset's name or title) and ``page`` (PAGE_SIZE rows each), and
    the backend's status."""
    toolkit.check_access("webpageviewplus_thumbnail_status", context, data_dict)
    oldest = datetime.datetime.utcnow() - datetime.timedelta(
        seconds=settings.screenshot_seconds())
    if data_dict.get("id"):
        package = model.Package.get(data_dict["id"])
        if package is None or package.state != "active":
            raise toolkit.ObjectNotFound()
        found = _candidates(package_id=package.id)
        thumbs, shots, checks = _metas([c.id for c in found])
        return {"dataset": package.name, "rows": [
            _row(c, thumbs.get(c.id), _flags(c, thumbs.get(c.id), shots.get(c.id),
                                             checks.get(c.id), oldest))
            for c in found]}
    wanted = data_dict.get("filter") or "all"
    if wanted not in FILTERS:
        raise toolkit.ValidationError({"filter": [toolkit._("Unknown filter.")]})
    page = _page_number(data_dict.get("page"))
    found = _candidates(q=(data_dict.get("q") or "").strip())
    thumbs, shots, checks = _metas([c.id for c in found])
    matched = []
    for c in found:
        flags = _flags(c, thumbs.get(c.id), shots.get(c.id), checks.get(c.id), oldest)
        if wanted == "all" or wanted in flags:
            matched.append((c, flags))
    shown = matched[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    return {
        "rows": [_row(c, thumbs.get(c.id), flags) for c, flags in shown],
        "count": len(matched),
        "page": page,
        "page_size": PAGE_SIZE,
        "filter": wanted,
        "ids": [c.id for c, _ in matched][:MAX_IDS],
        "backend": _backend(),
    }


def _backend() -> dict[str, Any]:
    status = screenshots.backend_status()
    failure = status["last_failure"]
    return dict(
        status, limited_until=_when(status["limited_until"]),
        last_success=_when(status["last_success"]),
        last_failure={"kind": failure["kind"], "at": _when(failure["at"])} if failure else None)


# --- Changes ---------------------------------------------------------------------


def _ids(data_dict: dict[str, Any]) -> list[str]:
    raw = data_dict.get("ids")
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, (list, tuple)) or not raw:
        raise toolkit.ValidationError({"ids": [toolkit._("Missing value")]})
    ids = list(dict.fromkeys(str(i) for i in raw if i))
    if not ids:
        raise toolkit.ValidationError({"ids": [toolkit._("Missing value")]})
    if len(ids) > MAX_IDS:
        raise toolkit.ValidationError({"ids": [toolkit._(
            "At most {count} views at a time.").format(count=MAX_IDS)]})
    return ids


def change_auth(context: dict[str, Any], data_dict: dict[str, Any]) -> dict[str, Any]:
    """Any logged-in user: each view is checked in the action
    (resource_view_update), and the ones they can't update are skipped."""
    return {"success": not authz.auth_is_anon_user(context)}


def _authorized(view_id: str, context: dict[str, Any],
                types: Iterable[str]) -> Optional[model.ResourceView]:
    """The view, if it's one of ``types`` on an active resource and the
    caller may update it; else None, whichever it was."""
    view = model.ResourceView.get(view_id)
    if view is None or view.view_type not in types:
        return None
    resource = model.Resource.get(view.resource_id)
    if resource is None or resource.state != "active":
        return None
    # A fresh context per view: auth functions keep the objects they load
    # in it, which mustn't carry over to the next view.
    fresh = {key: context[key] for key in ("user", "auth_user_obj", "ignore_auth", "model")
             if key in context}
    try:
        toolkit.check_access("resource_view_update", fresh,
                             {"id": view.id, "resource_id": view.resource_id})
    except (toolkit.NotAuthorized, toolkit.ObjectNotFound, toolkit.ValidationError):
        return None
    return view


def request_screenshots(context: dict[str, Any], data_dict: dict[str, Any]) -> dict[str, Any]:
    """webpageviewplus_screenshot_request with ``ids``: a job per view,
    within the retake limit; a sysadmin's request skips the cooldown, as
    the CLI does. Answers how many were queued and each view's outcome:
    queued, pending, cooldown, no_target, not_found or error."""
    ids = _ids(data_dict)
    if not screenshots.jobs_on():
        raise toolkit.ValidationError(
            {"ids": [toolkit._("This site takes screenshots on a schedule, not on request.")]})
    skip = authz.is_sysadmin(context.get("user"))
    results: dict[str, str] = {}
    broken = False
    for view_id in ids:
        view = _authorized(view_id, context, (screenshots.VIEW_TYPE,))
        if view is None:
            results[view_id] = "not_found"
            continue
        url = screenshots._view_target(view)
        if not url:
            results[view_id] = "no_target"
            continue
        if broken:
            results[view_id] = "error"
            continue
        try:
            results[view_id] = screenshots.request_job(view.id, url, skip_cooldown=skip)
        except Exception as e:
            # Redis or the queue is down: the rest would fail the same way.
            log.warning("webpageviewplus: couldn't queue a screenshot (%s)", type(e).__name__)
            results[view_id] = "error"
            broken = True
    return {"queued": sum(1 for r in results.values() if r == "queued"), "results": results}


def _thumbnail_meta(view_id: str) -> Optional[dict[str, Any]]:
    return _by_view(thumbnails.table, [thumbnails.table.c.source,
                                       thumbnails.table.c.created_at], [view_id]).get(view_id)


def _source(view: model.ResourceView) -> Optional[str]:
    resource = model.Resource.get(view.resource_id)
    return thumbnails.source_for(view.config or {}, resource.url or "", resource.url_type or "",
                                 view.view_type, resource.last_modified)


def refresh_now(view: model.ResourceView) -> str:
    """Remake one view's thumbnail now, keeping the copy if remaking it
    fails: "remade", "kept", "none" (nothing to make one from) or
    "first_page" (drawn in the view form, never here)."""
    source = _source(view)
    if source and source.startswith("pdf-page:"):
        return "first_page"
    before = _thumbnail_meta(view.id)
    thumbnails.after_save(dict(view.config or {}, id=view.id, view_type=view.view_type,
                               resource_id=view.resource_id), keep=True)
    after = _thumbnail_meta(view.id)
    if not after:
        return "none"
    if before and before["created_at"] == after["created_at"]:
        return "kept"
    return "remade"


def _refresh_key(view_id: str) -> str:
    return screenshots._REDIS_PREFIX + "refresh-pending:" + view_id


def refresh_job(view_id: str, token: Optional[str] = None) -> None:
    """The queued remake. Never raises; clears only its own mark."""
    try:
        view = model.ResourceView.get(view_id)
        if view is not None and view.view_type in thumbnails.VIEW_TYPES:
            refresh_now(view)
    except Exception as e:
        log.warning("webpageviewplus: a thumbnail remake failed (%s)", type(e).__name__)
        model.Session.rollback()
    finally:
        try:
            redis = screenshots._redis()
            held = redis.get(_refresh_key(view_id))
            held = held.decode() if isinstance(held, bytes) else held
            if token is None or held == token:
                redis.delete(_refresh_key(view_id))
        except Exception:
            log.debug("webpageviewplus: remake's pending mark not cleared")


def _queue_refresh(view_id: str) -> str:
    redis = screenshots._redis()
    token = uuid.uuid4().hex
    if not redis.set(_refresh_key(view_id), token, nx=True, ex=REFRESH_PENDING_SECONDS):
        return "pending"
    try:
        toolkit.enqueue_job(refresh_job, [view_id, token], title="webpageviewplus thumbnail",
                            queue=screenshots.QUEUE)
    except Exception:
        redis.delete(_refresh_key(view_id))
        raise
    return "queued"


def webpageviewplus_thumbnail_refresh(
    context: dict[str, Any], data_dict: dict[str, Any]
) -> dict[str, Any]:
    """Remake the thumbnails of the views in ``ids`` (re-fetch or re-read
    each one's source). One view is remade at once; several are queued on
    the webpageviewplus queue, one job per view at a time. Each view's
    outcome: remade, kept, none, first_page (one view); queued, pending,
    first_page (several); not_found or error."""
    toolkit.check_access("webpageviewplus_thumbnail_refresh", context, data_dict)
    ids = _ids(data_dict)
    views = {view_id: _authorized(view_id, context, thumbnails.VIEW_TYPES) for view_id in ids}
    results = {view_id: "not_found" for view_id, view in views.items() if view is None}
    found = [view for view in views.values() if view is not None]
    if len(ids) == 1 and found:
        view = found[0]
        results[view.id] = refresh_now(view)
        events.emit("thumbnail_refresh_requested", view_id=view.id, queued=False)
        return {"queued": 0, "results": results}
    remakes = []
    for view in found:
        source = _source(view)
        if source and source.startswith("pdf-page:"):
            results[view.id] = "first_page"
        else:
            remakes.append(view)
    if remakes and screenshots.queue_has_worker() is False:
        raise toolkit.ValidationError({"ids": [toolkit._(
            "No worker reads the {queue} queue, so remakes can't be queued. Remake one view "
            "at a time, or run ckan webpageviewplus check-views.").format(
                queue=screenshots.QUEUE)]})
    broken = False
    for view in remakes:
        if broken:
            results[view.id] = "error"
            continue
        try:
            results[view.id] = _queue_refresh(view.id)
        except Exception as e:
            log.warning("webpageviewplus: couldn't queue a thumbnail remake (%s)",
                        type(e).__name__)
            results[view.id] = "error"
            broken = True
            continue
        if results[view.id] == "queued":
            events.emit("thumbnail_refresh_requested", view_id=view.id, queued=True)
    return {"queued": sum(1 for r in results.values() if r == "queued"), "results": results}


def webpageviewplus_thumbnail_delete(
    context: dict[str, Any], data_dict: dict[str, Any]
) -> dict[str, Any]:
    """Remove what's stored for the views in ``ids``: ``remove`` is
    thumbnail, screenshot or both (the default). A screenshot keeps its
    last attempt's time, so the cooldown still counts from it. Unless a
    view also turns automatic thumbnails off, a copy is made again at its
    next save or refresh. Each view's outcome: removed, none or not_found."""
    toolkit.check_access("webpageviewplus_thumbnail_delete", context, data_dict)
    ids = _ids(data_dict)
    remove = data_dict.get("remove") or "both"
    if remove not in REMOVE:
        raise toolkit.ValidationError({"remove": [toolkit._("Unknown value.")]})
    results: dict[str, str] = {}
    for view_id in ids:
        view = _authorized(view_id, context, thumbnails.VIEW_TYPES)
        if view is None:
            results[view_id] = "not_found"
            continue
        removed = []
        if remove in ("thumbnail", "both") and _thumbnail_meta(view.id):
            thumbnails.delete(view.id)
            removed.append("thumbnail")
        if remove in ("screenshot", "both") and screenshots.clear(view.id):
            removed.append("screenshot")
        results[view_id] = "removed" if removed else "none"
        if removed:
            events.emit("thumbnail_removed", view_id=view.id, removed=",".join(removed))
    return {"removed": sum(1 for r in results.values() if r == "removed"), "results": results}


# --- The dataset's Thumbnails page ----------------------------------------------

blueprint = Blueprint("webpageviewplus_manage", __name__)
DATASET_PATH = "/dataset/<id>/thumbnails"


def _context() -> dict[str, Any]:
    return {"user": current_user.name, "auth_user_obj": current_user, "for_view": True}


def _editable_dataset(id: str) -> dict[str, Any]:
    try:
        toolkit.check_access("package_update", _context(), {"id": id})
    except toolkit.ObjectNotFound:
        toolkit.abort(404, toolkit._("Dataset not found"))
    except toolkit.NotAuthorized:
        toolkit.abort(403, toolkit._("Not authorized to see this page"))
    try:
        return toolkit.get_action("package_show")(_context(), {"id": id})
    except (toolkit.ObjectNotFound, toolkit.NotAuthorized):
        toolkit.abort(404, toolkit._("Dataset not found"))


def protect_form() -> None:
    """CSRF for a form posted to this extension's pages. CKAN exempts
    extensions' blueprints by default (ckan.csrf_protection.ignore_extensions),
    so the check is made here, as CKAN makes it: not for requests
    authenticated by an API token, and not when CSRF protection is off."""
    if g.get("login_via_auth_header") or not current_app.config.get("WTF_CSRF_ENABLED", True):
        return
    from ckan.config.middleware.flask_app import csrf
    csrf.protect()


@blueprint.route(DATASET_PATH, methods=["GET"])
def dataset(id: str) -> str:
    pkg_dict = _editable_dataset(id)
    status = toolkit.get_action("webpageviewplus_thumbnail_status")(
        _context(), {"id": pkg_dict["id"]})
    g.pkg_dict = pkg_dict
    return toolkit.render("webpageviewplus/manage_dataset.html", {
        "pkg_dict": pkg_dict,
        "dataset_type": pkg_dict.get("type") or "dataset",
        "rows": status["rows"],
        "choices": thumbnails.choices(pkg_dict),
        "first_page": thumbnails.FIRST_PAGE,
        "screenshots_on": screenshots.enabled(),
    })


@blueprint.route(DATASET_PATH, methods=["POST"])
def dataset_change(id: str) -> Any:
    protect_form()
    pkg_dict = _editable_dataset(id)
    view = model.ResourceView.get(request.form.get("view_id") or "")
    if view is None or view.view_type not in thumbnails.VIEW_TYPES or view.resource_id not in {
            r["id"] for r in pkg_dict.get("resources") or []}:
        toolkit.abort(404, toolkit._("View not found"))
    message, ok = _change(view, request.form.get("do") or "", request.form, pkg_dict)
    (toolkit.h.flash_success if ok else toolkit.h.flash_error)(message)
    return toolkit.redirect_to("webpageviewplus_manage.dataset", id=pkg_dict["name"])


def _errors(error: toolkit.ValidationError) -> str:
    found = error.error_dict if isinstance(error.error_dict, dict) else {}
    return " ".join(str(m) for key, messages in found.items() if key != "seconds_left"
                    for m in (messages if isinstance(messages, list) else [messages]))


def _update_view(view: model.ResourceView, changes: dict[str, Any]) -> tuple[str, bool]:
    try:
        data = toolkit.get_action("resource_view_show")(_context(), {"id": view.id})
        data.update(changes)
        toolkit.get_action("resource_view_update")(_context(), data)
    except toolkit.NotAuthorized:
        toolkit.abort(403, toolkit._("Not authorized to see this page"))
    except toolkit.ValidationError as e:
        return _errors(e), False
    return "", True


def _change(view: model.ResourceView, do: str, form: Any,
            pkg_dict: dict[str, Any]) -> tuple[str, bool]:
    _ = toolkit._
    if do == "screenshot":
        try:
            outcome = toolkit.get_action("webpageviewplus_screenshot_request")(
                _context(), {"ids": [view.id]})["results"][view.id]
        except toolkit.ValidationError as e:
            return _errors(e), False
        if outcome in ("queued", "pending"):
            return _("A screenshot will be taken in the background. Reload this page later "
                     "to see it."), True
        if outcome == "cooldown":
            url = screenshots._view_target(view)
            left = max(1, screenshots.cooldown_left(view.id, url) if url else 1)
            return toolkit.ungettext(
                "You can take a new screenshot in {seconds} second.",
                "You can take a new screenshot in {seconds} seconds.", left,
            ).format(seconds=left), False
        if outcome == "no_target":
            return _("This view's page doesn't get a screenshot."), False
        return _("The screenshot couldn't be queued. Try again later."), False
    if do == "refresh":
        outcome = toolkit.get_action("webpageviewplus_thumbnail_refresh")(
            _context(), {"ids": [view.id]})["results"][view.id]
        return {
            "remade": (_("The thumbnail was remade."), True),
            "kept": (_("The thumbnail couldn't be remade, so the last one is kept."), False),
            "none": (_("There's nothing to make a thumbnail from."), False),
            "first_page": (_("Draw the first page again in the view's form."), False),
        }.get(outcome, (_("The thumbnail couldn't be remade. Try again later."), False))
    if do == "remove":
        outcome = toolkit.get_action("webpageviewplus_thumbnail_delete")(
            _context(), {"ids": [view.id]})["results"][view.id]
        if outcome == "removed":
            return _("Removed. Unless automatic thumbnails are off, a new one is made at the "
                     "next save or refresh."), True
        return _("There was nothing to remove."), False
    if do == "pick":
        picked = form.get("thumbnail_resource") or ""
        allowed = {c["id"] for c in thumbnails.choices(pkg_dict)}
        current = (view.config or {}).get("thumbnail_resource") or ""
        if picked and picked not in allowed and picked != current:
            return _("Pick one of this dataset's images."), False
        message, ok = _update_view(view, {"thumbnail_resource": picked})
        return (_("The thumbnail choice is saved."), True) if ok else (message, False)
    if do in ("auto_off", "auto_on"):
        message, ok = _update_view(view, {"no_auto_thumbnail": "true" if do == "auto_off" else ""})
        if not ok:
            return message, False
        return (_("Automatic thumbnails are off for this view.") if do == "auto_off"
                else _("Automatic thumbnails are on for this view.")), True
    toolkit.abort(400, _("Unknown action."))
