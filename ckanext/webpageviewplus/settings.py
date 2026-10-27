"""Typed accessors for the extension's site settings (config_declaration.yaml)."""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

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


def harden_downloads() -> bool:
    return toolkit.asbool(_get("harden_downloads"))


def editor_can_loosen() -> bool:
    return toolkit.asbool(_get("editor_can_loosen"))


def referrer_policy() -> str:
    return _get("referrer_policy")


def min_height() -> int:
    return int(_get("min_height"))


def max_height() -> int:
    return int(_get("max_height"))


def _hosts_setting(name: str) -> list[str]:
    return [frame.canonical_config_host(h) for h in toolkit.aslist(_get(name))]


def allowed_hosts() -> list[str]:
    return [h for h in _hosts_setting("allowed_hosts") if h]


def formats() -> list[str]:
    return [f.lower() for f in toolkit.aslist(_get("formats"))]


LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def content_origin() -> Optional[str]:
    """The content origin as ``scheme://host[:port]``, or None when unset."""
    value = (_get("content_origin") or "").strip().rstrip("/")
    return value or None


def signing_secret() -> str:
    return _get("signing_secret") or ""


def url_ttl() -> int:
    return int(_get("url_ttl"))


def isolating_content_origin() -> Optional[str]:
    """The content origin, if uploaded HTML can no longer run on CKAN's
    origin: served from the content origin, and CKAN's own copies sandboxed.
    Only then may frames let scripts keep a real origin, and only then do
    presets that need a content origin stop degrading."""
    return content_origin() if harden_downloads() else None


def _check_content_origin(origin: str) -> None:
    key = PREFIX + "content_origin"
    parts = urlsplit(origin)
    host = frame.url_host(origin)
    if parts.scheme not in ("http", "https") or not host or parts.path or parts.query:
        raise CkanConfigurationException(
            key + ": must be an origin like https://example-usercontent.org"
        )
    loopback = host in LOOPBACK_HOSTS or host.endswith(".localhost")
    if parts.scheme != "https" and not loopback:
        raise CkanConfigurationException(key + ": must use https")
    for ckan_host in ckan_hosts():
        related = (
            host == ckan_host
            or host.endswith("." + ckan_host)
            or ckan_host.endswith("." + host)
        )
        if related:
            raise CkanConfigurationException(
                key + ": must be a different site from CKAN's own host "
                + ckan_host + " (not the same host, a subdomain or a parent)"
            )
    if len(signing_secret()) < 32:
        raise CkanConfigurationException(
            PREFIX + "signing_secret: required with content_origin, "
            "at least 32 characters"
        )


def site_url() -> str:
    return toolkit.config.get("ckan.site_url") or ""


def ckan_hosts() -> list[str]:
    """Hosts CKAN is served from: ckan.site_url's, plus site_aliases (given as
    bare hosts or as URLs)."""
    hosts = [frame.url_host(site_url())] + _hosts_setting("site_aliases")
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
    for name in ("site_aliases", "allowed_hosts"):
        entries = toolkit.aslist(_get(name))
        bad = [e for e, h in zip(entries, _hosts_setting(name)) if not h]
        if bad:
            # Dropping an alias silently would switch off the same-host check
            # for it, so refuse to start instead.
            raise CkanConfigurationException(
                PREFIX + name + ": " + "Not valid hosts: " + " ".join(bad)
            )
    if url_ttl() < 30:
        raise CkanConfigurationException(
            PREFIX + "url_ttl: must be at least 30 seconds"
        )
    origin = content_origin()
    if origin:
        _check_content_origin(origin)
    if not 0 < min_height() <= max_height():
        raise CkanConfigurationException(
            PREFIX + "min_height" + ": " + "Must be positive and at most max_height"
        )
