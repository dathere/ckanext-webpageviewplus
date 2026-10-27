"""Typed accessors for the extension's site settings (config_declaration.yaml)."""
from __future__ import annotations

from typing import Optional

import ckan.plugins.toolkit as toolkit
from ckan.exceptions import CkanConfigurationException

from ckanext.webpageviewplus import frame

PREFIX = "ckanext.webpageviewplus."


def _get(name: str):
    return toolkit.config.get(PREFIX + name)


def allowed_presets() -> list[str]:
    return toolkit.aslist(_get("presets"))


def default_preset() -> str:
    return _get("default_preset")


def editor_can_loosen() -> bool:
    return toolkit.asbool(_get("editor_can_loosen"))


def referrer_policy() -> str:
    return _get("referrer_policy")


def min_height() -> int:
    return int(_get("min_height"))


def max_height() -> int:
    return int(_get("max_height"))


def allowed_hosts() -> list[str]:
    return [h.lower() for h in toolkit.aslist(_get("allowed_hosts"))]


def formats() -> list[str]:
    return [f.lower() for f in toolkit.aslist(_get("formats"))]


def content_origin() -> Optional[str]:
    # Phase 3 adds ckanext.webpageviewplus.content_origin; until then there
    # is never one, and presets that need it degrade.
    return None


def site_url() -> str:
    return toolkit.config.get("ckan.site_url") or ""


def ckan_hosts() -> list[str]:
    """Hosts CKAN is served from: ckan.site_url's, plus site_aliases (given as
    bare hosts or as URLs)."""
    hosts = [frame.url_host(site_url())]
    for alias in toolkit.aslist(_get("site_aliases")):
        hosts.append(
            frame.url_host(alias if "//" in alias else "https://" + alias)
        )
    return [h for h in hosts if h]


def check() -> None:
    """Refuse unsafe or inconsistent settings at startup, naming the key."""
    unknown = [p for p in allowed_presets() if p not in frame.PRESETS]
    if unknown:
        raise CkanConfigurationException(
            PREFIX + "presets" + ": " + "Unknown presets: " + " ".join(unknown)
        )
    if not allowed_presets():
        raise CkanConfigurationException(
            PREFIX + "presets" + ": " + "At least one preset must be allowed"
        )
    if default_preset() not in allowed_presets():
        raise CkanConfigurationException(
            PREFIX + "default_preset" + ": " + "Must be one of " + PREFIX + "presets"
        )
    if referrer_policy() not in frame.REFERRER_POLICIES:
        raise CkanConfigurationException(
            PREFIX + "referrer_policy" + ": " + "Unknown referrer policy"
        )
    if not 0 < min_height() <= max_height():
        raise CkanConfigurationException(
            PREFIX + "min_height" + ": " + "Must be positive and at most max_height"
        )
