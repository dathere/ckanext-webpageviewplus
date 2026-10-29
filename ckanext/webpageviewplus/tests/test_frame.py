"""Unit tests for the attribute builder: no CKAN app or database needed."""
import pytest

from ckanext.webpageviewplus import frame

SITE = "https://ckan.example.org"
EXTERNAL = "https://pages.example.com/report.html"
OWN = SITE + "/dataset/x/resource/y/download/report.html"
# Simulates phase 3, where uploads are served from their own origin.
CO = "https://usercontent.example.net"
DENIALS = "; ".join("{} 'none'".format(f) for f in frame.DENIED_FEATURES)


def build(url=EXTERNAL, preset="interactive", **kw):
    kw.setdefault("ckan_hosts", ["ckan.example.org"])
    return frame.build_frame(url, preset, **kw)


def test_strict_is_empty_sandbox_not_absent():
    f = build(preset="strict")
    assert f.sandbox == ""
    assert f.allow == DENIALS


def test_external_has_no_sandbox_attribute_with_content_origin():
    f = build(content_origin=CO, preset="external")
    assert f.sandbox is None
    assert f.allow == "fullscreen; " + DENIALS


def test_presets_with_scripts_allow_forms():
    # A form in the frame (Google Forms posts one) would otherwise be dropped.
    for name in ("interactive", "data-schematic"):
        assert "allow-forms" in frame.PRESETS[name].sandbox
    assert "allow-forms" not in frame.PRESETS["strict"].sandbox


def test_interactive_tokens():
    f = build()
    assert f.sandbox == "allow-scripts allow-forms allow-popups allow-downloads"
    assert f.preset == "interactive"


@pytest.mark.parametrize("url", [OWN, SITE + ":443/page.html", "HTTPS://CKAN.example.org/p"])
def test_external_refused_on_own_origin(url):
    f = build(content_origin=CO, url=url, preset="external")
    assert f.preset == "strict"
    assert f.sandbox == ""
    assert "same_origin_fallback" in f.notices


def test_external_never_applies_to_uploads():
    f = build(content_origin=CO, url=CO + "/x/a.html", preset="external", is_upload=True)
    assert f.preset == "interactive"
    assert "external_not_for_uploads" in f.notices


def test_upload_not_on_content_origin_counts_as_ckan():
    # e.g. the CKAN-origin download URL: scripts may not keep their origin.
    f = build(
        content_origin=CO,
        url="https://pages.example.com/a.html",
        is_upload=True,
        sandbox_override="allow-scripts allow-same-origin",
    )
    assert f.preset == "strict"


def test_data_schematic_on_upload_from_content_origin_keeps_same_origin():
    f = build(content_origin=CO, url=CO + "/x/s.html", preset="data-schematic", is_upload=True)
    assert f.preset == "data-schematic"
    assert "allow-same-origin" in f.sandbox


def test_scripts_plus_same_origin_refused_on_own_origin():
    f = build(url=OWN, sandbox_override="allow-scripts allow-same-origin")
    assert f.preset == "strict"
    assert f.sandbox == ""


def test_scripts_plus_same_origin_allowed_elsewhere_with_content_origin():
    f = build(content_origin=CO, sandbox_override="allow-scripts allow-same-origin")
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
    assert build(content_origin=CO, url=url, preset="external").preset == "strict"


def test_alias_host_counts_as_ckan():
    f = build(content_origin=CO, 
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


@pytest.mark.parametrize(
    "entry,host",
    [
        ("::1", "[::1]"),
        ("[0::1]", "[::1]"),
        ("https://CKAN.example.org:8443/", "ckan.example.org"),
        ("ckan.example.org.", "ckan.example.org"),
        ("127.0.0.1", "127.0.0.1"),
        ("ckan.example.org:8443", "ckan.example.org"),
        ("127.0.0.1:5000", "127.0.0.1"),
        ("[::1]:8443", "[::1]"),
        ("%63kan.example.org", ""),
        ("not a host", ""),
    ],
)
def test_canonical_config_host(entry, host):
    assert frame.canonical_config_host(entry) == host


def test_external_degrades_to_interactive_without_content_origin():
    # An unsandboxed page could navigate itself to HTML uploaded to CKAN.
    f = build(preset="external")
    assert f.preset == "interactive"
    assert f.sandbox == "allow-scripts allow-forms allow-popups allow-downloads"
    assert f.notices == ["degraded"]


def test_scripts_plus_same_origin_refused_everywhere_without_content_origin():
    f = build(sandbox_override="allow-scripts allow-same-origin")
    assert f.preset == "strict"
    assert f.sandbox == ""


def test_same_origin_without_scripts_allowed_without_content_origin():
    f = build(sandbox_override="allow-same-origin allow-forms")
    assert f.sandbox == "allow-same-origin allow-forms"


@pytest.mark.parametrize(
    "url,origin",
    [
        ("https://Pages.Example.com/a.html", "https://pages.example.com"),
        ("https://pages.example.com:443/a", "https://pages.example.com"),
        ("http://127.0.0.1:5001/x", "http://127.0.0.1:5001"),
        ("http://[::1]:8080/x", "http://[::1]:8080"),
        ("https://%63kan.example.org/", ""),
        ("https://pages.example.com./a.html", "https://pages.example.com."),
    ],
)
def test_serialize_origin(url, origin):
    assert frame.serialize_origin(url) == origin


def test_opaque_frames():
    assert build(preset="strict").opaque
    assert build(preset="interactive").opaque
    assert not build(preset="external", content_origin=CO).opaque
    f = build(content_origin=CO, url=CO + "/x/s.html", preset="data-schematic", is_upload=True)
    assert not f.opaque
    assert f.origin == CO


def test_credentialless_off_by_default():
    f = build()
    assert not f.credentialless
    assert "credentialless" not in f.notices


def test_credentialless_by_preset():
    f = build(preset="interactive", credentialless_presets=["interactive"])
    assert f.credentialless
    assert "credentialless" in f.notices
    assert not build(preset="strict", credentialless_presets=["interactive"]).credentialless


def test_credentialless_follows_the_preset_the_frame_runs_as():
    # data-schematic runs as interactive without a content origin.
    assert build(preset="data-schematic", credentialless_presets=["interactive"]).credentialless
    assert not build(preset="data-schematic", credentialless_presets=["data-schematic"]).credentialless
    # A frame refused by the same-origin rule runs as strict.
    f = build(sandbox_override="allow-scripts allow-same-origin",
              credentialless_presets=["strict"])
    assert f.preset == "strict" and f.credentialless


@pytest.mark.parametrize("kw", [
    # CKAN's own pages and uploads load (and reload, via the download URL)
    # through CKAN, which needs the visitor's session for private resources.
    {"url": OWN},
    {"url": OWN, "is_upload": True},
    {"url": CO + "/x/s.html", "is_upload": True, "content_origin": CO},
])
def test_credentialless_only_for_third_party_pages(kw):
    presets = list(frame.PRESETS)
    assert not build(preset="interactive", credentialless_presets=presets, **kw).credentialless


def test_credentialless_never_loosens_the_sandbox():
    plain = build(preset="external", content_origin=CO)
    hardened = build(preset="external", content_origin=CO,
                     credentialless_presets=["external"])
    assert hardened.credentialless
    assert hardened.sandbox == plain.sandbox
    assert hardened.allow == plain.allow


def test_content_origin_upload_grants_features_to_the_content_origin():
    """Reload and load on tap go through CKAN's download URL, which
    redirects to the content origin; a bare feature would be granted to the
    src's origin (CKAN's) only."""
    f = build(content_origin=CO, url=CO + "/x/s.html", preset="data-schematic",
              is_upload=True, allow_override="fullscreen clipboard-write")
    assert f.allow == "fullscreen 'src' {0}; clipboard-write 'src' {0}; ".format(CO) + DENIALS


def test_other_frames_keep_bare_features():
    f = build(content_origin=CO, url="https://pages.example.com/a.html",
              preset="interactive")
    assert f.allow == "fullscreen; " + DENIALS
