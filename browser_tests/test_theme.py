"""Theme follows CKAN: with the site's color_scheme set to light (CI does;
the default is auto), the frame's CSS color-scheme is what the framed page
sees as prefers-color-scheme, not the viewer's OS. Only Firefox honours it
for these frames: Chrome ignored it even without emulation (checked
2026-10-28), and WebKit lacks it (WebKit bug 284973).

A look-alike frame (same src and sandbox, no color-scheme) is the control:
it must follow the emulated dark OS, which shows the emulation reached the
frames and that the property made the difference.
"""
import pytest

from wvp_helpers import FRAME


@pytest.mark.parametrize("title", ["Schematic auto", "Interactive fixed 300"])
def test_frame_follows_site_not_os(page, view_urls, title):
    if page.context.browser.browser_type.name != "firefox":
        pytest.skip("only Firefox passes an iframe's color-scheme on to its page")
    page.emulate_media(color_scheme="dark")
    page.goto(view_urls[title])
    view = page.locator(FRAME)
    if view.evaluate("f => getComputedStyle(f).colorScheme") != "light":
        pytest.skip("this site's color_scheme isn't light")
    view.scroll_into_view_if_needed()
    page.evaluate("""() => {
        const view = document.querySelector('%s');
        const f = document.createElement('iframe');
        f.id = 'control';
        if (view.hasAttribute('sandbox')) f.setAttribute('sandbox', view.getAttribute('sandbox'));
        f.src = view.src;
        view.after(f);
    }""" % FRAME)
    scheme = page.frame_locator(FRAME).locator("#scheme")
    control = page.frame_locator("#control").locator("#scheme")
    control.filter(has_text="dark").wait_for()
    scheme.filter(has_text="light").wait_for(timeout=5000)
