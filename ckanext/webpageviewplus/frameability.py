"""Frameability check: does a page allow CKAN to frame it?

Off by default (ckanext.webpageviewplus.frameability_check): turning it on
makes the CKAN server request URLs that editors type. Guarded against SSRF:
only http(s) URLs whose host resolves entirely to public addresses are
fetched, redirects are re-checked hop by hop (at most 3), no body is read, and
every network or policy failure gives the same "unknown" verdict, so the
check can't be used to probe which hosts or ports exist.

Known limit: the host is resolved here and again by requests when it
connects, so a DNS answer that changes in between (rebinding) isn't caught.
The connection isn't pinned to the checked address, so that egress proxies a
deployment requires keep working.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from typing import Any, Mapping, Optional
from urllib.parse import urljoin, urlsplit

import requests

from ckanext.webpageviewplus import frame

log = logging.getLogger(__name__)

FRAMEABLE, BLOCKED, UNKNOWN = "frameable", "blocked", "unknown"
TIMEOUT = 5
MAX_REDIRECTS = 3
CACHE_SECONDS = 300
CACHE_SIZE = 256
USER_AGENT = "ckanext-webpageviewplus frameability check"

_cache: dict[str, tuple[float, tuple[str, str]]] = {}


# --- header rules -----------------------------------------------------------


def _frame_ancestors(csp_values: list[str]) -> list[list[str]]:
    """The frame-ancestors source lists, one per policy that has one. A
    header may carry several comma-separated policies; each is enforced."""
    lists = []
    for value in csp_values:
        for policy in value.split(","):
            for directive in policy.split(";"):
                parts = directive.strip().split()
                if parts and parts[0].lower() == "frame-ancestors":
                    lists.append([p for p in parts[1:]])
    return lists


_HOST_SOURCE = re.compile(
    r"^(?:(?P<scheme>[a-z][a-z0-9+.-]*)://)?"
    r"(?P<host>\*|(?:\*\.)?[a-z0-9.-]+)"
    r"(?::(?P<port>\d+|\*))?(?:/.*)?$"
)


def _source_allows(source: str, ckan: tuple[str, list[str], Optional[int]]) -> bool:
    scheme, hosts, port = ckan
    s = source.lower()
    if s == "*":
        return True
    if s in ("'none'", "'self'"):
        # 'self' is the framed page's own origin, never CKAN's here (pages on
        # CKAN's host aren't checked).
        return False
    if s.endswith(":") and "/" not in s:
        return s[:-1] == scheme or (s == "http:" and scheme == "https")
    m = _HOST_SOURCE.match(s)
    if not m:
        return False
    if m.group("scheme") and m.group("scheme") not in (scheme, "http" if scheme == "https" else scheme):
        return False
    src_port = m.group("port")
    if src_port and src_port != "*" and port is not None and int(src_port) != port:
        return False
    host = m.group("host")
    for ckan_host in hosts:
        if host == ckan_host or host == "*":
            return True
        if host.startswith("*.") and ckan_host.endswith(host[1:]):
            return True
    return False


def verdict(
    headers: Mapping[str, str],
    csp_values: list[str],
    ckan: tuple[str, list[str], Optional[int]],
) -> tuple[str, str]:
    """(verdict, reason) for a response's headers. ``ckan`` is CKAN's
    (scheme, hosts, port). As in browsers: a frame-ancestors directive
    overrides X-Frame-Options entirely; ALLOW-FROM is ignored."""
    ancestors = _frame_ancestors(csp_values)
    if ancestors:
        for sources in ancestors:
            if not any(_source_allows(src, ckan) for src in sources):
                return BLOCKED, "Content-Security-Policy: frame-ancestors " + " ".join(sources)
        return FRAMEABLE, ""
    xfo = (headers.get("X-Frame-Options") or "").lower()
    for value in (v.strip() for v in xfo.split(",")):
        if value in ("deny", "sameorigin"):
            return BLOCKED, "X-Frame-Options: " + value.upper()
    return FRAMEABLE, ""


# --- fetching ---------------------------------------------------------------


def _resolve(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _public(url: str) -> bool:
    """Whether every address the URL's host resolves to is public."""
    if not frame.is_http_url(url):
        return False
    parts = urlsplit(url)
    try:
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
        addresses = _resolve(parts.hostname or "", port)
    except (OSError, ValueError, UnicodeError):
        return False
    if not addresses:
        return False
    for address in addresses:
        try:
            if not ipaddress.ip_address(address.split("%")[0]).is_global:
                return False
        except ValueError:
            return False
    return True


def _fetch(url: str, ckan: tuple[str, list[str], Optional[int]]) -> tuple[str, str]:
    for _ in range(MAX_REDIRECTS + 1):
        if not _public(url):
            return UNKNOWN, ""
        try:
            response = requests.get(
                url,
                stream=True,
                timeout=TIMEOUT,
                allow_redirects=False,
                headers={"User-Agent": USER_AGENT},
            )
        except requests.RequestException:
            return UNKNOWN, ""
        try:
            location = response.headers.get("Location")
            if response.is_redirect and location:
                url = urljoin(url, location)
                continue
            # requests joins repeated headers with commas, which is also how
            # CSP separates several policies, each of them enforced.
            csp = response.headers.get("Content-Security-Policy")
            return verdict(response.headers, [csp] if csp else [], ckan)
        finally:
            response.close()
    return UNKNOWN, ""


def check(url: str, ckan: tuple[str, list[str], Optional[int]]) -> tuple[str, str]:
    now = time.time()
    cached = _cache.get(url)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    result = _fetch(url, ckan)
    if len(_cache) >= CACHE_SIZE:
        _cache.clear()
    _cache[url] = (now, result)
    return result


def ckan_identity(site_url: str, hosts: list[str]) -> tuple[str, list[str], Optional[int]]:
    parts = urlsplit(site_url)
    try:
        port = parts.port
    except ValueError:
        port = None
    return parts.scheme.lower() or "https", hosts, port


def summary(result: tuple[str, str]) -> dict[str, Any]:
    return {"verdict": result[0], "reason": result[1]}
