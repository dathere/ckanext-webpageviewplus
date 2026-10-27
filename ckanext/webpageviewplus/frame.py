"""Presets and the iframe attribute builder.

Pure functions: nothing here reads CKAN config or the database, so the
security rules can be unit-tested directly. Callers pass in site settings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional
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
    "external": Preset(
        sandbox=None, allow=("fullscreen",), height_mode="fixed",
        height_value=str(FALLBACK_HEIGHT),
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


def is_http_url(url: Any) -> bool:
    """True only for absolute http(s) URLs with a host.

    Anything else (``javascript:``, ``data:``, relative or scheme-less URLs)
    is refused, because an iframe ``src`` with a ``javascript:`` URL runs in
    CKAN's own origin.
    """
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in ALLOWED_SCHEMES and bool(parts.netloc)


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


def url_host(url: str) -> str:
    try:
        # A trailing dot names the same host to a browser.
        return (urlsplit(url).hostname or "").lower().rstrip(".")
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


def violates_same_origin_rule(
    sandbox: Optional[tuple[str, ...]], same_origin: bool
) -> bool:
    """The invariant: a page on CKAN's own origin may never run scripts with
    its real origin. So there it needs a sandbox, and the sandbox may not
    combine allow-scripts with allow-same-origin."""
    if not same_origin:
        return False
    if sandbox is None:
        return True
    return "allow-scripts" in sandbox and "allow-same-origin" in sandbox


def effective_preset_name(name: str, content_origin: Optional[str]) -> str:
    """Resolve presets that can't run as configured. Without a content origin
    (always, before phase 3) data-schematic degrades to interactive."""
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
    preset = PRESETS[name]

    sandbox: Optional[tuple[str, ...]] = preset.sandbox
    if sandbox_override:
        sandbox = tuple(parse_sandbox(sandbox_override)[0])
    allow = list(preset.allow)
    if allow_override:
        allow = parse_allow(allow_override)[0]

    same_origin = is_upload or is_ckan_host(url, ckan_hosts)
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
    )
