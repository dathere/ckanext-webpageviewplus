"""Presets and the iframe attribute builder.

Pure functions: nothing here reads CKAN config or the database, so the
security rules can be unit-tested directly. Callers pass in site settings.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")

SANDBOX_TOKENS = frozenset(
    [
        "allow-downloads",
        "allow-forms",
        "allow-modals",
        "allow-orientation-lock",
        "allow-pointer-lock",
        "allow-popups",
        "allow-popups-to-escape-sandbox",
        "allow-presentation",
        "allow-same-origin",
        "allow-scripts",
        "allow-storage-access-by-user-activation",
        "allow-top-navigation",
        "allow-top-navigation-by-user-activation",
        "allow-top-navigation-to-custom-protocols",
    ]
)

# Ad and tracking features: always denied, on every preset, and never
# grantable through a per-view override.
DENIED_FEATURES = (
    "browsing-topics",
    "attribution-reporting",
    "join-ad-interest-group",
    "run-ad-auction",
)

FEATURE_RE = re.compile(r"^[a-z][a-z0-9-]*$")

REFERRER_POLICIES = (
    "no-referrer",
    "no-referrer-when-downgrade",
    "origin",
    "origin-when-cross-origin",
    "same-origin",
    "strict-origin",
    "strict-origin-when-cross-origin",
    "unsafe-url",
)

HEIGHT_MODES = ("fixed", "viewport", "aspect", "auto")

# Height used for "auto" until the frame reports its own (self-sizing is
# phase 2), and for fixed-height presets.
FALLBACK_HEIGHT = 600

ASPECT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[/:]\s*(\d+(?:\.\d+)?)\s*$")


@dataclass(frozen=True)
class Preset:
    # None means no sandbox attribute at all; () means sandbox="" (everything
    # restricted). Don't conflate the two.
    sandbox: Optional[tuple[str, ...]]
    allow: tuple[str, ...]
    height_mode: str
    height_value: str = ""
    needs_content_origin: bool = False


PRESETS: dict[str, Preset] = {
    "strict": Preset(
        sandbox=(), allow=(), height_mode="fixed",
        height_value=str(FALLBACK_HEIGHT),
    ),
    "interactive": Preset(
        sandbox=("allow-scripts", "allow-popups", "allow-downloads"),
        allow=("fullscreen",),
        height_mode="auto",
    ),
    # Unsandboxed, so the page could navigate itself to HTML uploaded to
    # CKAN and run it as CKAN: only safe once uploads have their own origin.
    "external": Preset(
        sandbox=None, allow=("fullscreen",), height_mode="fixed",
        height_value=str(FALLBACK_HEIGHT), needs_content_origin=True,
    ),
    "data-schematic": Preset(
        sandbox=(
            "allow-scripts",
            "allow-same-origin",
            "allow-popups",
            "allow-popups-to-escape-sandbox",
            "allow-downloads",
        ),
        allow=("fullscreen",),
        height_mode="auto",
        needs_content_origin=True,
    ),
}


_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME_RE = re.compile(r"^{0}(?:\.{0})*\.?$".format(_LABEL))
_IPV4_RE = re.compile(
    r"^(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$"
)
_AUTHORITY_RE = re.compile(r"^(\[[0-9a-f:.]+\]|[a-z0-9.-]+)(?::(\d{0,5}))?$")
_NUMERIC_LABEL_RE = re.compile(r"^(?:\d+|0x[0-9a-f]*)$")


def _canonical_host(netloc: str) -> Optional[str]:
    """The host a browser would use, or None if this parser and a browser's
    could disagree about it.

    Python's urlsplit doesn't follow the WHATWG URL parser: it doesn't
    percent-decode or IDNA-map hosts, and it reads IPv4 numbers literally. So
    only plain forms are accepted: ASCII hostnames whose last label isn't
    numeric, dotted-quad IPv4 and bracketed IPv6. Userinfo (``@``),
    percent-encoding and non-ASCII hosts are refused.
    """
    m = _AUTHORITY_RE.match(netloc.lower())
    if not m:
        return None
    host, port = m.group(1), m.group(2)
    if port and int(port) > 65535:
        return None
    if host.startswith("["):
        try:
            return "[{}]".format(ipaddress.IPv6Address(host[1:-1]).compressed)
        except ValueError:
            return None
    host = host.rstrip(".")  # a trailing dot names the same host
    if _IPV4_RE.match(host):
        return host
    if not host or not _HOSTNAME_RE.match(host):
        return None
    # A numeric last label makes a browser parse the whole host as IPv4.
    if _NUMERIC_LABEL_RE.match(host.rsplit(".", 1)[-1]):
        return None
    return host


def is_http_url(url: Any) -> bool:
    """True only for absolute http(s) URLs whose host is unambiguous.

    Anything else (``javascript:``, ``data:``, relative or scheme-less URLs)
    is refused, because an iframe ``src`` with a ``javascript:`` URL runs in
    CKAN's own origin. Backslashes, spaces and control characters are refused
    anywhere: browsers treat ``\\`` as ``/`` in http(s) URLs, so the host
    could differ from the one checked here.
    """
    if not isinstance(url, str) or not url:
        return False
    if "\\" in url or any(ord(c) <= 0x20 or ord(c) == 0x7F for c in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (
        parts.scheme.lower() in ALLOWED_SCHEMES
        and _canonical_host(parts.netloc) is not None
    )


def is_ckan_host(url: str, ckan_hosts: list[str]) -> bool:
    """True when the URL's host is one CKAN is served from.

    Compares hosts only, ignoring scheme and port: browsers share cookies
    across ports, and (without the Secure flag) across http and https, so a
    page on CKAN's host under another port or scheme can still act with the
    viewer's session. An unparseable URL counts as CKAN's, so a doubtful case
    gets the stricter treatment.
    """
    host = url_host(url)
    if not host:
        return True
    return host in ckan_hosts


def canonical_config_host(entry: str) -> str:
    """Canonicalize a host from a setting, given as a URL or a bare host
    (``example.org``, ``example.org:8443``, ``127.0.0.1``, ``::1`` or
    ``[::1]:8443``). Ports are ignored, like everywhere hosts are compared.
    Returns "" if it
    isn't a valid host."""
    entry = entry.strip()
    if "//" in entry:
        return url_host(entry)
    try:
        entry = "[{}]".format(ipaddress.IPv6Address(entry))  # bare IPv6
    except ValueError:
        pass  # a hostname or IPv4, optionally with :port
    return url_host("https://" + entry)


def serialize_origin(url: str) -> str:
    """The origin a browser gives a page at this URL (scheme://host[:port],
    default ports omitted), or "" if the URL isn't usable. Unlike url_host,
    which compares hosts, this keeps a trailing dot: browsers keep it in the
    origin, so messages from such a page carry it."""
    if not url_host(url):
        return ""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = "[{}]".format(ipaddress.IPv6Address(host).compressed)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        return ""
    default = {"http": 80, "https": 443}.get(scheme)
    if port and port != default:
        return "{}://{}:{}".format(scheme, host, port)
    return "{}://{}".format(scheme, host)


def url_host(url: str) -> str:
    """The URL's canonical host, or "" if it's ambiguous or unparseable."""
    try:
        return _canonical_host(urlsplit(url).netloc) or ""
    except ValueError:
        return ""


def parse_sandbox(value: str) -> tuple[list[str], list[str]]:
    """Split a sandbox override into (known tokens, unknown tokens)."""
    tokens = value.split()
    return (
        [t for t in tokens if t in SANDBOX_TOKENS],
        [t for t in tokens if t not in SANDBOX_TOKENS],
    )


def parse_allow(value: str) -> tuple[list[str], list[str]]:
    """Split an allow override into (valid feature names, rejected names).

    Accepts names separated by ``;``, ``,`` or whitespace. Per-feature
    allowlists aren't supported: a granted feature applies to the frame's
    own origin, which is the Permissions Policy default.
    """
    names = [n for n in re.split(r"[;,\s]+", value.strip().lower()) if n]
    ok = [n for n in names if FEATURE_RE.match(n) and n not in DENIED_FEATURES]
    bad = [n for n in names if n not in ok]
    return ok, bad


def must_isolate(on_ckan_host: bool, content_origin: Optional[str]) -> bool:
    """Whether the frame must not let scripts keep a real origin.

    Always for a page on CKAN's own host. Without a content origin, for every
    frame: uploaded HTML is then served from CKAN's origin, and a frame whose
    scripts keep their origin can navigate (or be redirected) to an upload,
    which would then run as CKAN inside the CKAN page. Checking the URL only
    when the view is saved or rendered can't catch that.
    """
    return on_ckan_host or not content_origin


def violates_same_origin_rule(
    sandbox: Optional[tuple[str, ...]], same_origin: bool
) -> bool:
    """The invariant: where ``same_origin`` (see ``must_isolate``) holds, a
    frame may never run scripts with its real origin. So it needs a sandbox,
    and the sandbox may not combine allow-scripts with allow-same-origin."""
    if not same_origin:
        return False
    if sandbox is None:
        return True
    return "allow-scripts" in sandbox and "allow-same-origin" in sandbox


def frame_on_ckan_host(
    url: str,
    ckan_hosts: list[str],
    is_upload: bool,
    content_origin: Optional[str],
) -> bool:
    """Whether the frame loads from CKAN's own host. An upload counts as
    CKAN's unless it is served from the content origin (its stored url is a
    bare filename at model level, and its download URL is on CKAN's host)."""
    if is_upload:
        return not (content_origin and url_host(url) == url_host(content_origin))
    return is_ckan_host(url, ckan_hosts)


def preset_for_upload(name: str) -> str:
    """Uploaded HTML is user content: it always gets a sandbox, so external
    (no sandbox) becomes interactive. data-schematic is the upload preset."""
    return "interactive" if name == "external" else name


def effective_preset_name(name: str, content_origin: Optional[str]) -> str:
    """Resolve presets that can't run as configured. Without a content origin
    (always, before phase 3) data-schematic and external degrade to
    interactive."""
    if name not in PRESETS:
        return "strict"
    if PRESETS[name].needs_content_origin and not content_origin:
        return "interactive"
    return name


@dataclass
class Frame:
    src: str
    preset: str
    sandbox: Optional[str]
    allow: str
    referrerpolicy: str
    style: str
    height_mode: str
    title: str = ""
    notices: list[str] = field(default_factory=list)
    # For the self-sizing script: the origin the framed page has, unless
    # opaque (sandboxed without allow-same-origin), when its messages arrive
    # with origin "null" and replies must be posted to "*".
    origin: str = ""
    opaque: bool = False
    min_height: int = 0
    max_height: int = 0
    credentialless: bool = False


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def height_value_valid(mode: str, value: str) -> bool:
    if mode == "fixed":
        return _int(value) > 0
    if mode == "viewport":
        return 0 < _int(value) <= 100
    if mode == "aspect":
        m = ASPECT_RE.match(value or "")
        return bool(m) and float(m.group(1)) > 0 and float(m.group(2)) > 0
    return mode == "auto"


def height_style(
    mode: str, value: str, min_height: int, max_height: int
) -> tuple[str, str]:
    """Return (mode, CSS) for a height setting, falling back to a fixed
    height for anything invalid. Only viewport and auto heights are clamped."""
    if not height_value_valid(mode, value):
        return "fixed", "height:{}px".format(FALLBACK_HEIGHT)
    if mode == "fixed":
        return mode, "height:{}px".format(_int(value))
    if mode == "viewport":
        return mode, "height:clamp({}px, {}vh, {}px)".format(
            min_height, _int(value), max_height
        )
    if mode == "aspect":
        m = ASPECT_RE.match(value)
        assert m
        return mode, "aspect-ratio:{} / {};height:auto".format(
            m.group(1), m.group(2)
        )
    px = min(max(FALLBACK_HEIGHT, min_height), max_height)
    return mode, "height:{}px".format(px)


def build_frame(
    url: str,
    preset_name: str,
    *,
    ckan_hosts: list[str],
    is_upload: bool = False,
    content_origin: Optional[str] = None,
    sandbox_override: str = "",
    allow_override: str = "",
    referrer_policy: str = "strict-origin-when-cross-origin",
    height_mode: str = "",
    height_value: str = "",
    min_height: int = 400,
    max_height: int = 4000,
    title: str = "",
    credentialless_presets: Sequence[str] = (),
) -> Frame:
    """Build the iframe attributes for one view.

    Order matters: first resolve presets that can't run here (data-schematic
    without a content origin), then apply overrides, then enforce the
    same-origin rule, falling back to ``strict`` if it fails.
    """
    notices: list[str] = []
    name = effective_preset_name(preset_name, content_origin)
    if name != preset_name and preset_name in PRESETS:
        notices.append("degraded")
    if is_upload and preset_for_upload(name) != name:
        name = preset_for_upload(name)
        notices.append("external_not_for_uploads")
    preset = PRESETS[name]

    sandbox: Optional[tuple[str, ...]] = preset.sandbox
    if sandbox_override:
        sandbox = tuple(parse_sandbox(sandbox_override)[0])
    allow = list(preset.allow)
    if allow_override:
        allow = parse_allow(allow_override)[0]

    on_ckan_host = frame_on_ckan_host(url, ckan_hosts, is_upload, content_origin)
    same_origin = must_isolate(on_ckan_host, content_origin)
    if violates_same_origin_rule(sandbox, same_origin):
        name = "strict"
        sandbox = PRESETS["strict"].sandbox
        allow = list(PRESETS["strict"].allow)
        notices.append("same_origin_fallback")

    mode, style = height_style(
        height_mode or preset.height_mode,
        height_value if height_mode else preset.height_value,
        min_height,
        max_height,
    )

    allow_attr = "; ".join(
        allow + ["{} 'none'".format(f) for f in DENIED_FEATURES]
    )
    opaque = sandbox is not None and "allow-same-origin" not in sandbox
    # Chromium only, so it may only add hardening: nothing above depends on
    # it. Decided on the preset the frame actually runs as. Third-party pages
    # only: uploads and CKAN's own pages load (or reload, via the download
    # URL) through CKAN, which needs the visitor's session for private ones.
    credentialless = (
        name in credentialless_presets and not is_upload and not on_ckan_host
    )
    if credentialless:
        notices.append("credentialless")
    return Frame(
        src=url,
        preset=name,
        sandbox=None if sandbox is None else " ".join(sandbox),
        allow=allow_attr,
        referrerpolicy=(
            referrer_policy
            if referrer_policy in REFERRER_POLICIES
            else "strict-origin-when-cross-origin"
        ),
        style=style,
        height_mode=mode,
        title=title,
        notices=notices,
        origin=serialize_origin(url),
        opaque=opaque,
        min_height=min_height,
        max_height=max_height,
        credentialless=credentialless,
    )
