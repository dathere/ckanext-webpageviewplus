"""What uploaded pages contact: Content-Security-Policy-Report-Only on the
content origin, with reports sent back to the content origin itself (so no
CORS), reduced to hosts per resource. Report-only never blocks anything.

Reports can be posted by anyone, so the hosts are a hint for admins, not
proof; the "This page connects to" disclosure is off by default.
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Iterable
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

import ckan.model as model

from ckanext.webpageviewplus import frame, settings

log = logging.getLogger(__name__)

PATH = "/webpageviewplus/report/<resource_id>"
PREFIX = "/webpageviewplus/report/"
ENDPOINT_NAME = "webpageviewplus"
MAX_BODY = 64 * 1024
MAX_HOSTS = 20
SCHEMES = {"http": "http", "https": "https", "ws": "http", "wss": "https"}

metadata = sa.MetaData()
table = sa.Table(
    "webpage_view_plus_connections",
    metadata,
    sa.Column("resource_id", sa.UnicodeText, primary_key=True),
    sa.Column("host", sa.UnicodeText, primary_key=True),
    sa.Column("reports", sa.Integer, nullable=False, server_default="0"),
    sa.Column("last_seen", sa.Date, nullable=False),
)


def report_path(resource_id: str) -> str:
    return PREFIX + resource_id


def policy(resource_id: str) -> str:
    """Report anything fetched from another host. Inline and eval are
    allowed so they don't drown the hosts in reports."""
    return (
        "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; "
        "form-action 'self'; "
        "report-uri {}; report-to {}".format(report_path(resource_id), ENDPOINT_NAME)
    )


def reporting_endpoints(resource_id: str) -> str:
    return '{}="{}{}"'.format(
        ENDPOINT_NAME, settings.content_origin(), report_path(resource_id))


def _blocked_urls(payload: Any) -> Iterable[str]:
    # report-to (application/reports+json): a list of reports.
    if isinstance(payload, list):
        for report in payload:
            if isinstance(report, dict) and isinstance(report.get("body"), dict):
                yield report["body"].get("blockedURL") or ""
    # report-uri (application/csp-report): one report.
    elif isinstance(payload, dict) and isinstance(payload.get("csp-report"), dict):
        yield payload["csp-report"].get("blocked-uri") or ""


def hosts_in(body: bytes, own_host: str) -> list[str]:
    """The distinct other hosts named in a report body, at most MAX_HOSTS.
    Anything that isn't an http(s) or ws(s) URL (inline, eval, data:) is
    skipped."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    hosts: list[str] = []
    for url in _blocked_urls(payload):
        if not isinstance(url, str):
            continue
        parts = urlsplit(url.strip())
        scheme = SCHEMES.get(parts.scheme.lower())
        if not scheme:
            continue
        host = frame.url_host(parts._replace(scheme=scheme).geturl())
        if host and host != own_host and host not in hosts:
            hosts.append(host)
        if len(hosts) >= MAX_HOSTS:
            break
    return hosts


def record(resource_id: str, hosts: list[str]) -> None:
    if not hosts:
        return
    today = datetime.date.today()
    stmt = insert(table).values(
        [{"resource_id": resource_id, "host": h, "reports": 1, "last_seen": today}
         for h in hosts])
    stmt = stmt.on_conflict_do_update(
        index_elements=["resource_id", "host"],
        set_={"reports": table.c.reports + 1, "last_seen": stmt.excluded.last_seen},
    )
    try:
        with model.meta.engine.begin() as connection:
            connection.execute(stmt)
    except sa.exc.ProgrammingError:
        log.warning("webpageviewplus: no connections table; run "
                    "ckan db upgrade -p webpage_view_plus")
    except sa.exc.IntegrityError:
        pass  # the resource was deleted meanwhile


def hosts_for(resource_id: str) -> list[dict[str, Any]]:
    query = (
        sa.select(table.c.host, table.c.reports, table.c.last_seen)
        .where(table.c.resource_id == resource_id)
        .order_by(table.c.reports.desc(), table.c.host)
    )
    try:
        with model.meta.engine.connect() as connection:
            return [
                dict(r._mapping, last_seen=r.last_seen.isoformat())
                for r in connection.execute(query)
            ]
    except sa.exc.ProgrammingError:
        return []


def purge(retention_days: int) -> int:
    cutoff = datetime.date.today() - datetime.timedelta(days=retention_days)
    with model.meta.engine.begin() as connection:
        return connection.execute(table.delete().where(table.c.last_seen < cutoff)).rowcount


def summary() -> list[dict[str, Any]]:
    """Site-wide: each host, how many resources contact it, total reports."""
    query = (
        sa.select(
            table.c.host,
            sa.func.count(table.c.resource_id).label("resources"),
            sa.func.sum(table.c.reports).label("reports"),
        )
        .group_by(table.c.host)
        .order_by(sa.func.count(table.c.resource_id).desc(), table.c.host)
    )
    with model.meta.engine.connect() as connection:
        return [dict(r._mapping) for r in connection.execute(query)]
