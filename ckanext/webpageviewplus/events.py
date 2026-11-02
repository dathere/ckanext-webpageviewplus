"""Structured, server-side event logs for site admins.

Each event is one record on the ``ckanext.webpageviewplus.events`` logger,
with its fields in ``record.webpageviewplus``, so a log handler or
formatter can pick them up. Never add who caused an event: no IP address,
user name or id, user agent or referrer.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("ckanext.webpageviewplus.events")


# Logged at DEBUG: one per view rendered, which CKAN's default logging
# config (ckanext at DEBUG) would otherwise turn into a line per page view.
QUIET = frozenset(["view_rendered"])


def emit(event: str, **fields: Any) -> None:
    data = dict(fields, event=event)
    log.log(
        logging.DEBUG if event in QUIET else logging.INFO,
        "%s %s",
        event,
        " ".join("{}={}".format(k, v) for k, v in sorted(fields.items())),
        extra={"webpageviewplus": data},
    )
