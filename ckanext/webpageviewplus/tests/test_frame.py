"""Unit tests for the attribute builder: no CKAN app or database needed."""
import pytest

from ckanext.webpageviewplus import frame

SITE = "https://ckan.example.org"
EXTERNAL = "https://pages.example.com/report.html"
OWN = SITE + "/dataset/x/resource/y/download/report.html"
DENIALS = "; ".join("{} 'none'".format(f) for f in frame.DENIED_FEATURES)


def build(url=EXTERNAL, preset="interactive", **kw):
    kw.setdefault("ckan_hosts", ["ckan.example.org"])
    return frame.build_frame(url, preset, **kw)


def test_strict_is_empty_sandbox_not_absent():
    f = build(preset="strict")
    assert f.sandbox == ""
    assert f.allow == DENIALS


def test_external_has_no_sandbox_attribute():
    f = build(preset="external")
    assert f.sandbox is None
    assert f.allow == "fullscreen; " + DENIALS


def test_interactive_tokens():
    f = build()
    assert f.sandbox == "allow-scripts allow-popups allow-downloads"
    assert f.preset == "interactive"


@pytest.mark.parametrize("url", [OWN, SITE + ":443/page.html", "HTTPS://CKAN.example.org/p"])
def test_external_refused_on_own_origin(url):
    f = build(url=url, preset="external")
    assert f.preset == "strict"
    assert f.sandbox == ""
    assert "same_origin_fallback" in f.notices


def test_upload_counts_as_own_origin_whatever_its_url():
    f = build(preset="external", is_upload=True)
    assert f.preset == "strict"


def test_scripts_plus_same_origin_refused_on_own_origin():
    f = build(url=OWN, sandbox_override="allow-scripts allow-same-origin")
    assert f.preset == "strict"
    assert f.sandbox == ""


def test_scripts_plus_same_origin_allowed_elsewhere():
    f = build(sandbox_override="allow-scripts allow-same-origin")
    assert f.sandbox == "allow-scripts allow-same-origin"


@pytest.mark.parametrize(
    "url",
    [
        "https://ckan.example.org:8443/p.html",
        "http://ckan.example.org/p.html",
        "https://ckan.example.org./p.html",
    ],
)
def test_other_port_or_scheme_on_ckan_host_counts_as_ckan(url):
    # Cookies aren't isolated by port or (usually) scheme.
    assert build(url=url, preset="external").preset == "strict"


def test_alias_host_counts_as_ckan():
    f = build(
        url="https://ckan-alias.example.net/p.html",
        preset="external",
        ckan_hosts=["ckan.example.org", "ckan-alias.example.net"],
    )
    assert f.preset == "strict"


def test_data_schematic_degrades_to_interactive_before_same_origin_check():
    # On an upload, data-schematic must become interactive (which passes the
    # same-origin rule), not fall all the way to strict.
    f = build(url=OWN, preset="data-schematic", is_upload=True)
    assert f.preset == "interactive"
    assert "allow-same-origin" not in f.sandbox
    assert f.notices == ["degraded"]


def test_data_schematic_keeps_same_origin_with_content_origin():
    f = build(preset="data-schematic", content_origin="https://usercontent.example.net")
    assert "allow-same-origin" in f.sandbox


def test_unknown_preset_is_strict():
    assert build(preset="bogus").preset == "strict"


def test_allow_override_never_grants_denied_features():
    f = build(allow_override="fullscreen; browsing-topics; clipboard-write")
    assert f.allow == "fullscreen; clipboard-write; " + DENIALS


def test_unknown_sandbox_tokens_dropped():
    assert build(sandbox_override="allow-scripts allow-everything").sandbox == "allow-scripts"


def test_bad_referrer_policy_falls_back():
    assert build(referrer_policy="evil").referrerpolicy == "strict-origin-when-cross-origin"


@pytest.mark.parametrize(
    "mode,value,expected_mode,css",
    [
        ("fixed", "700", "fixed", "height:700px"),
        ("viewport", "80", "viewport", "height:clamp(400px, 80vh, 4000px)"),
        ("aspect", "16/9", "aspect", "aspect-ratio:16 / 9;height:auto"),
        ("aspect", "4:3", "aspect", "aspect-ratio:4 / 3;height:auto"),
        ("auto", "", "auto", "height:600px"),
        ("fixed", "abc", "fixed", "height:600px"),
        ("viewport", "150", "fixed", "height:600px"),
        ("aspect", "0/9", "fixed", "height:600px"),
    ],
)
def test_height(mode, value, expected_mode, css):
    f = build(height_mode=mode, height_value=value)
    assert (f.height_mode, f.style) == (expected_mode, css)


def test_auto_height_fallback_is_clamped():
    f = build(height_mode="auto", min_height=800, max_height=4000)
    assert f.style == "height:800px"


def test_preset_default_height():
    assert build(preset="strict").style == "height:600px"
    assert build(preset="interactive").height_mode == "auto"


@pytest.mark.parametrize(
    "value,ok,bad",
    [
        ("fullscreen; clipboard-write", ["fullscreen", "clipboard-write"], []),
        ("Fullscreen, geolocation", ["fullscreen", "geolocation"], []),
        ("run-ad-auction", [], ["run-ad-auction"]),
        ("fullscreen 'self'", ["fullscreen"], ["'self'"]),
    ],
)
def test_parse_allow(value, ok, bad):
    assert frame.parse_allow(value) == (ok, bad)


@pytest.mark.parametrize(
    "url,host",
    [
        ("https://CKAN.example.org./p", "ckan.example.org"),
        ("http://[0:0::1]:8080/p", "[::1]"),
        ("https://%63kan.example.org/p", ""),
    ],
)
def test_url_host_is_canonical_or_empty(url, host):
    assert frame.url_host(url) == host


def test_ambiguous_host_counts_as_ckan():
    assert frame.is_ckan_host("https://%63kan.example.org/p", ["ckan.example.org"])
