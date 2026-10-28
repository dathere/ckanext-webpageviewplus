"""Opt-in view counting: per-view, per-day totals.

Counting needs two opt-ins, the site's ``view_counting`` setting and the
view's ``count_views`` field. Nothing about viewers is kept: no IP address,
user, user agent or referrer. Impressions are counted when the view is
rendered; the other counts arrive in one beacon per page load. Browsers
sending Global Privacy Control, and obvious bots, are never counted.
"""
from __future__ import annotations

import datetime
import logging
import re
from typing import Any, Optional

import sqlalchemy as sa
from flask import Blueprint, request
from sqlalchemy.dialects.postgresql import insert

import ckan.model as model
import ckan.plugins.toolkit as toolkit

from ckanext.webpageviewplus import settings

log = logging.getLogger(__name__)

# Its own MetaData, so CKAN's create_all (in tests) never creates it: the
# table comes from the plugin's migration.
metadata = sa.MetaData()
FLAGS = ("seen", "loaded", "engaged", "failed")
COUNTS = ("impressions",) + FLAGS
table = sa.Table(
    "webpage_view_plus_view_counts",
    metadata,
    sa.Column("view_id", sa.UnicodeText, primary_key=True),
    sa.Column("day", sa.Date, primary_key=True),
    *[sa.Column(name, sa.Integer, nullable=False, server_default="0") for name in COUNTS],
)

BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|preview|fetch|monitor|headless|curl|wget|python|scrapy|http",
    re.IGNORECASE,
)


def is_bot(user_agent: str) -> bool:
    """Obvious bots only, by user agent: anything without one, or naming
    itself as a crawler, script or headless browser."""
    return not user_agent or bool(BOT_RE.search(user_agent))


def countable(resource_view: dict[str, Any]) -> bool:
    return settings.view_counting() and toolkit.asbool(resource_view.get("count_views"))


def viewer_opts_out() -> bool:
    return request.headers.get("Sec-GPC", "").strip() == "1" or is_bot(
        request.headers.get("User-Agent", ""))


def add(view_id: str, **counts: int) -> None:
    """Add to today's totals for a view, in its own transaction, so it never
    commits anything else pending in CKAN's session."""
    counts = {k: v for k, v in counts.items() if k in COUNTS and v}
    if not counts:
        return
    stmt = insert(table).values(view_id=view_id, day=datetime.date.today(), **counts)
    stmt = stmt.on_conflict_do_update(
        index_elements=["view_id", "day"],
        set_={k: table.c[k] + stmt.excluded[k] for k in counts},
    )
    try:
        with model.meta.engine.begin() as connection:
            connection.execute(stmt)
    except sa.exc.ProgrammingError:
        # Counting turned on before ckan db upgrade -p webpage_view_plus:
        # never break the page for it.
        log.warning("webpageviewplus: no view counts table; run "
                    "ckan db upgrade -p webpage_view_plus")


def totals(view_id: str, days: int) -> dict[str, Any]:
    since = datetime.date.today() - datetime.timedelta(days=days - 1)
    query = (
        sa.select(table)
        .where(table.c.view_id == view_id, table.c.day >= since)
        .order_by(table.c.day)
    )
    with model.meta.engine.connect() as connection:
        rows = [dict(r._mapping) for r in connection.execute(query)]
    return {
        "days": [dict(r, day=r["day"].isoformat()) for r in rows],
        "totals": {k: sum(r[k] for r in rows) for k in COUNTS},
    }


def totals_or_none(view_id: str, days: int) -> Optional[dict[str, Any]]:
    """totals, or None before the plugin's migration has run."""
    try:
        return totals(view_id, days)
    except sa.exc.ProgrammingError:
        return None


def purge(retention_days: int) -> int:
    cutoff = datetime.date.today() - datetime.timedelta(days=retention_days)
    with model.meta.engine.begin() as connection:
        return connection.execute(table.delete().where(table.c.day < cutoff)).rowcount


def summary() -> list[dict[str, Any]]:
    """Site-wide totals per view over the retention period, busiest first."""
    query = (
        sa.select(table.c.view_id, *[sa.func.sum(table.c[k]).label(k) for k in COUNTS])
        .group_by(table.c.view_id)
        .order_by(sa.func.sum(table.c.impressions).desc())
    )
    with model.meta.engine.connect() as connection:
        return [dict(r._mapping) for r in connection.execute(query)]


def _view(view_id: str) -> Optional[model.ResourceView]:
    return model.Session.query(model.ResourceView).get(view_id)


beacon = Blueprint("webpageviewplus_beacon", __name__)


@beacon.route("/webpageviewplus/count/<view_id>", methods=["POST"])
def count(view_id: str):
    """The beacon a counted view sends once per page load, as form fields
    seen, loaded, engaged and failed (each 0 or 1). Always 204, so it says
    nothing about which views exist or are counted."""
    view = _view(view_id)
    if (
        view is not None
        and view.view_type == "webpage_view_plus"
        and countable(view.config or {})
        and not viewer_opts_out()
    ):
        add(view_id, **{
            flag: 1 for flag in FLAGS if request.form.get(flag, "").strip() == "1"
        })
    return "", 204
