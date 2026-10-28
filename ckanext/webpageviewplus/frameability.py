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
import threading
import time
from dataclasses import dataclass
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
    header may carry several comma-separated policies, each enforced; within
    one policy, browsers keep only the first frame-ancestors directive."""
    lists = []
    for value in csp_values:
        for policy in value.split(","):
            for directive in policy.split(";"):
                parts = directive.strip().split()
                if parts and parts[0].lower() == "frame-ancestors":
                    lists.append(parts[1:])
                    break
    return lists


DEFAULT_PORTS = {"http": 80, "https": 443}


_HOST_SOURCE = re.compile(
    r"^(?:(?P<scheme>[a-z][a-z0-9+.-]*)://)?"
    r"(?P<host>\*|(?:\*\.)?[a-z0-9.-]+)"
    r"(?::(?P<port>\d+|\*))?(?:/.*)?$"
)


def _schemes_match(source_scheme: str, scheme: str) -> bool:
    # A source scheme also allows its secure upgrade (http: allows https).
    return source_scheme == scheme or (source_scheme == "http" and scheme == "https")


def _source_allows(
    source: str, ckan: tuple[str, list[str], Optional[int]], page_scheme: str
) -> bool:
    """Whether one frame-ancestors source matches CKAN's origin: ``ckan`` is
    (scheme, [host], port) with the port resolved to its default."""
    scheme, hosts, port = ckan
    s = source.lower()
    if s == "*":
        return True
    if s in ("'none'", "'self'"):
        # 'self' is the framed page's own origin, never CKAN's here (pages on
        # CKAN's host aren't checked).
        return False
    if s.endswith(":") and "/" not in s:
        return _schemes_match(s[:-1], scheme)
    m = _HOST_SOURCE.match(s)
    if not m:
        return False
    # A host-source without a scheme takes the framed page's scheme.
    source_scheme = m.group("scheme") or page_scheme
    if not _schemes_match(source_scheme, scheme):
        return False
    # Without a port, a source matches only its scheme's default port.
    src_port = m.group("port")
    if src_port != "*":
        expected = int(src_port) if src_port else DEFAULT_PORTS.get(source_scheme)
        if src_port is None and scheme != source_scheme:
            expected = DEFAULT_PORTS.get(scheme)  # an http->https upgrade
        if expected != port:
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
    page_scheme: str = "https",
) -> tuple[str, str]:
    """(verdict, reason) for a response's headers. ``ckan`` is CKAN's origin
    as (scheme, [host], port); ``page_scheme`` is the framed page's. As in
    browsers: a frame-ancestors directive overrides X-Frame-Options
    entirely; ALLOW-FROM is ignored."""
    ancestors = _frame_ancestors(csp_values)
    if ancestors:
        for sources in ancestors:
            if not any(_source_allows(src, ckan, page_scheme) for src in sources):
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


def _resolve_within(host: str, port: int, timeout: Optional[float]) -> list[str]:
    """_resolve, giving up after ``timeout`` seconds: getaddrinfo can't be
    interrupted, so it runs in a daemon thread that's left to finish."""
    if timeout is None:
        return _resolve(host, port)
    if timeout <= 0:
        raise OSError("deadline passed")
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["addresses"] = _resolve(host, port)
        except Exception as e:  # handed back to the caller below
            result["error"] = e

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    if "addresses" in result:
        return result["addresses"]
    raise OSError("lookup failed or timed out") from result.get("error")


def _address_kind(url: str, deadline: Optional[float] = None) -> str:
    """"public" if every address the URL's host resolves to is public,
    "unresolved" if it doesn't resolve (or not before ``deadline``), else
    "private"."""
    if not frame.is_http_url(url):
        return "private"
    parts = urlsplit(url)
    try:
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
        timeout = None if deadline is None else deadline - time.monotonic()
        addresses = _resolve_within(parts.hostname or "", port, timeout)
    except (OSError, ValueError, UnicodeError):
        return "unresolved"
    if not addresses:
        return "unresolved"
    for address in addresses:
        try:
            if not ipaddress.ip_address(address.split("%")[0]).is_global:
                return "private"
        except ValueError:
            return "private"
    return "public"


def _public(url: str) -> bool:
    """Whether every address the URL's host resolves to is public."""
    return _address_kind(url) == "public"


@dataclass(frozen=True)
class Probe:
    """What fetching a page found. ``problem`` is "" when the page answered
    below 400, else "gone" (404, 410), "http-error" (any other 4xx/5xx),
    "unreachable" (no DNS answer, network failure, timeout, too many
    redirects) or "private" (not a public address, never fetched)."""

    verdict: str
    reason: str = ""
    problem: str = ""
    status: Optional[int] = None
    # Every URL requested, redirects included (http:// in a redirect chain
    # is blocked as mixed content on an https:// site, like the first URL).
    urls: tuple[str, ...] = ()


def probe(
    url: str, ckan: tuple[str, list[str], Optional[int]], deadline: Optional[float] = None
) -> Probe:
    """Fetch ``url``'s headers (no body) and judge them. ``deadline`` is a
    time.monotonic() value bounding the whole check, redirects included."""
    try:
        return _probe(url, ckan, deadline)
    except Exception:  # malformed redirects etc.: one answer for every failure
        log.debug("webpageviewplus: frameability check failed", exc_info=True)
        return Probe(UNKNOWN, problem="unreachable")


def _probe(
    url: str, ckan: tuple[str, list[str], Optional[int]], deadline: Optional[float]
) -> Probe:
    urls: list[str] = []
    for _ in range(MAX_REDIRECTS + 1):
        urls.append(url)
        kind = _address_kind(url, deadline)
        if kind != "public":
            problem = "private" if kind == "private" else "unreachable"
            return Probe(UNKNOWN, problem=problem, urls=tuple(urls))
        timeout: float = TIMEOUT
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
            if timeout <= 0:
                return Probe(UNKNOWN, problem="unreachable", urls=tuple(urls))
        try:
            response = requests.get(
                url,
                stream=True,
                timeout=timeout,
                allow_redirects=False,
                headers={"User-Agent": USER_AGENT},
            )
        except requests.RequestException:
            return Probe(UNKNOWN, problem="unreachable", urls=tuple(urls))
        try:
            location = response.headers.get("Location")
            if response.is_redirect and location:
                url = urljoin(url, location)
                continue
            # requests joins repeated headers with commas, which is also how
            # CSP separates several policies, each of them enforced.
            csp = response.headers.get("Content-Security-Policy")
            page_scheme = urlsplit(url).scheme.lower()
            result, reason = verdict(response.headers, [csp] if csp else [], ckan, page_scheme)
            status = response.status_code
            problem = ""
            if status in (404, 410):
                problem = "gone"
            elif status >= 400:
                problem = "http-error"
            return Probe(result, reason, problem, status, tuple(urls))
        finally:
            response.close()
    return Probe(UNKNOWN, problem="unreachable", urls=tuple(urls))


def _fetch(url: str, ckan: tuple[str, list[str], Optional[int]]) -> tuple[str, str]:
    # The form's check: every failure is the same "unknown", so editors can't
    # use it to probe which hosts or ports exist.
    result = probe(url, ckan)
    return result.verdict, result.reason


def check(url: str, ckan: tuple[str, list[str], Optional[int]]) -> tuple[str, str]:
    now = time.time()
    key = repr((url, ckan))
    cached = _cache.get(key)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    result = _fetch(url, ckan)
    if len(_cache) >= CACHE_SIZE:
        _cache.clear()
    _cache[key] = (now, result)
    return result


def ckan_identity(site_url: str) -> tuple[str, list[str], Optional[int]]:
    """CKAN's origin, the parent the framed page will see: ckan.site_url's
    scheme, host and effective port. Not the aliases: a page permitting only
    an alias would still refuse the site's own address."""
    parts = urlsplit(site_url)
    scheme = parts.scheme.lower() or "https"
    try:
        port = parts.port or DEFAULT_PORTS.get(scheme)
    except ValueError:
        port = DEFAULT_PORTS.get(scheme)
    return scheme, [frame.url_host(site_url)], port


def summary(result: tuple[str, str]) -> dict[str, Any]:
    return {"verdict": result[0], "reason": result[1]}
