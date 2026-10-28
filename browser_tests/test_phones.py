"""Phones and tablets, control bar: Expand (where there's no element
fullscreen, as on iPhone Safari), Share, and the landscape lock in
fullscreen. Browser APIs a desktop engine has (or lacks) are replaced by
init scripts in the top window only.
"""
import re

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

from wvp_helpers import FRAME, click_in_frame, open_view, wait_content_height

NARROW = {"width": 320, "height": 200}
NO_FULLSCREEN = """
if (window.top === window) {
  Object.defineProperty(Document.prototype, "fullscreenEnabled",
                        {get: () => false, configurable: true});
  delete Element.prototype.requestFullscreen;
}
"""
SHARE = """
if (window.top === window) {
  window.wvpShared = [];
  Object.defineProperty(Navigator.prototype, "share", {configurable: true,
    value: data => { wvpShared.push(data); return Promise.resolve(); }});
}
"""
NO_SHARE = """
if (window.top === window) delete Navigator.prototype.share;
"""
# On the prototype: WebKit can hand back a new screen.orientation object.
ORIENTATION = """
if (window.top === window && window.ScreenOrientation) {
  window.wvpLocks = [];
  ScreenOrientation.prototype.lock = type => { wvpLocks.push(type); return Promise.resolve(); };
  ScreenOrientation.prototype.unlock = () => { wvpLocks.push("unlock"); };
}
"""
EXPAND = '[data-webpageviewplus-action="fullscreen"]'
CLOSE = '[data-webpageviewplus-action="close"]'
SHARE_BUTTON = '[data-webpageviewplus-action="share"]'


def _frame_box(page):
    return page.locator(FRAME).bounding_box()


def test_expand_without_fullscreen(page, view_urls):
    page.add_init_script(NO_FULLSCREEN)
    child = open_view(page, view_urls, "Interactive fixed 300")
    # A state the frame would lose if it were reloaded (moved in the DOM).
    click_in_frame(child, "#grow")
    filler = child.locator("#filler")
    expect(filler).to_have_css("height", "500px")

    expand = page.locator(EXPAND)
    expect(expand).to_have_text("Expand")
    assert expand.get_attribute("aria-pressed") is None
    close = page.locator(CLOSE)
    expect(close).to_be_hidden()

    expand.click()
    wrapper = page.locator(".webpageviewplus")
    expect(wrapper).to_have_attribute("role", "dialog")
    expect(close).to_be_focused()
    box = _frame_box(page)
    assert box["y"] < 20 and box["height"] > 800 - 120  # the viewport, less the bar
    expect(filler).to_have_css("height", "500px")
    # Bootstrap's buttons fade between colours; measure the settled ones.
    page.emulate_media(reduced_motion="reduce")
    results = Axe().run(page, context=".webpageviewplus",
                        options={"resultTypes": ["violations"], "iframes": False})
    assert results.violations_count == 0, results.generate_report()

    page.keyboard.press("Escape")
    expect(wrapper).not_to_have_attribute("role", "dialog")
    expect(close).to_be_hidden()
    expect(expand).to_be_focused()
    assert abs(_frame_box(page)["height"] - 300) <= 1
    expect(filler).to_have_css("height", "500px")


def test_expand_close_button_and_narrow_floor(page, view_urls):
    """At 320px the 600px reflow floor must not apply inside Expand."""
    page.add_init_script(NO_FULLSCREEN)
    page.set_viewport_size(NARROW)
    page.goto(view_urls["Interactive fixed 300"])
    page.locator(EXPAND).click()
    box = _frame_box(page)
    assert box["height"] < NARROW["height"]
    # The whole control bar stays on the (scroll-locked) screen.
    for button in page.locator(".webpageviewplus-controls > *").all():
        if button.is_visible():
            b = button.bounding_box()
            assert b["y"] >= 0 and b["y"] + b["height"] <= NARROW["height"] + 1
    page.locator(CLOSE).click()
    expect(page.locator(CLOSE)).to_be_hidden()
    assert _frame_box(page)["height"] >= 599


def test_share(page, view_urls):
    page.add_init_script(SHARE)
    open_view(page, view_urls, "Interactive fixed 300")
    button = page.locator(SHARE_BUTTON)
    expect(button).to_be_visible()
    button.click()
    page.wait_for_function("() => wvpShared.length === 1")
    shared = page.evaluate("wvpShared[0]")
    assert shared["title"] == "Interactive fixed 300"
    # The CKAN page showing this view, not the frame's URL.
    assert shared["url"] == view_urls["Interactive fixed 300"]


def test_share_hidden_without_api(page, view_urls):
    page.add_init_script(NO_SHARE)
    open_view(page, view_urls, "Interactive fixed 300")
    page.locator('[data-webpageviewplus-action="reload"]').wait_for()
    expect(page.locator(SHARE_BUTTON)).to_be_hidden()


@pytest.mark.parametrize("title,viewport,wide", [
    ("Interactive fixed 300", None, True),
    # At 320px the frame is 600px tall (the reflow floor): not wide.
    ("Aspect 16/9", NARROW, False),
])
def test_landscape_lock_in_fullscreen(page, view_urls, title, viewport, wide):
    page.add_init_script(ORIENTATION)
    if viewport:
        page.set_viewport_size(viewport)
    page.goto(view_urls[title])
    if not page.evaluate("document.fullscreenEnabled && !!window.wvpLocks"):
        pytest.skip("no element fullscreen or screen.orientation in this browser")
    page.locator(FRAME).scroll_into_view_if_needed()
    button = page.locator(EXPAND)
    button.click()
    page.wait_for_function("() => document.fullscreenElement !== null")
    page.wait_for_timeout(200)
    button.click()
    page.wait_for_function("() => document.fullscreenElement === null")
    page.wait_for_timeout(200)
    locks = page.evaluate("wvpLocks")
    assert locks == (["landscape", "unlock"] if wide else [])


SHIELD = "[data-webpageviewplus-shield]"


def _coarse(page):
    if not page.evaluate("matchMedia('(pointer: coarse)').matches"):
        pytest.skip("no coarse pointer in this browser's touch emulation")


def test_tap_to_interact(touch_page, view_urls):
    page = touch_page
    page.goto(view_urls["Interactive fixed 700"])
    _coarse(page)
    shield = page.locator(SHIELD)
    expect(shield).to_be_visible()
    assert shield.get_attribute("aria-label") == "Interact with Interactive fixed 700"
    frame, box = page.locator(FRAME).bounding_box(), shield.bounding_box()
    assert abs(box["y"] - frame["y"]) <= 1 and abs(box["height"] - frame["height"]) <= 1
    results = Axe().run(page, context=".webpageviewplus",
                        options={"resultTypes": ["violations"], "iframes": False})
    assert results.violations_count == 0, results.generate_report()

    shield.tap()
    expect(shield).to_be_hidden()

    # Scrolled fully away and back: the shield returns.
    page.evaluate("""() => {
        const s = document.createElement('div');
        s.style.height = '3000px';
        document.querySelector('.webpageviewplus').after(s);
        const f = document.querySelector('%s').getBoundingClientRect();
        window.scrollTo({top: window.scrollY + f.bottom + 100, behavior: 'instant'});
    }""" % FRAME)
    expect(shield).to_be_visible()


def test_no_shield_on_short_frames(touch_page, view_urls):
    page = touch_page
    page.goto(view_urls["Interactive fixed 300"])
    _coarse(page)
    page.locator('[data-webpageviewplus-action="reload"]').wait_for()
    expect(page.locator(SHIELD)).to_be_hidden()


def test_no_shield_with_a_mouse(page, view_urls):
    page.goto(view_urls["Interactive fixed 700"])
    page.locator('[data-webpageviewplus-action="reload"]').wait_for()
    expect(page.locator(SHIELD)).to_be_hidden()


def test_shield_follows_threshold_but_not_after_a_tap(touch_page, view_urls):
    """Turning the phone sideways makes a 300px frame taller than half the
    screen; turning it back takes the shield away. A tap is remembered
    across resizes until the frame leaves the viewport."""
    page = touch_page
    page.goto(view_urls["Interactive fixed 300"])
    _coarse(page)
    page.locator('[data-webpageviewplus-action="reload"]').wait_for()
    page.locator(FRAME).scroll_into_view_if_needed()
    shield = page.locator(SHIELD)
    expect(shield).to_be_hidden()

    page.set_viewport_size({"width": 844, "height": 390})
    page.locator(FRAME).scroll_into_view_if_needed()
    expect(shield).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    expect(shield).to_be_hidden()

    page.set_viewport_size({"width": 844, "height": 390})
    page.locator(FRAME).scroll_into_view_if_needed()
    expect(shield).to_be_visible()
    shield.tap()
    expect(shield).to_be_hidden()
    page.set_viewport_size({"width": 845, "height": 391})
    page.wait_for_timeout(300)
    expect(shield).to_be_hidden()


def test_load_on_tap_with_save_data(page, view_urls):
    """With Save-Data: on, a card stands in for the frame until the reader
    asks; the loaded frame then works like any other (here: auto height)."""
    page.set_extra_http_headers({"Save-Data": "on"})
    page.goto(view_urls["Interactive auto"])
    card = page.locator("[data-webpageviewplus-card]")
    expect(card).to_be_visible()
    assert page.locator(FRAME).count() == 0
    load = page.locator("[data-webpageviewplus-load]")
    expect(load).to_have_text(re.compile(r"^Load \(.+\)$"))
    # Nothing to act on yet: Fullscreen and Reload wait for the frame.
    page.locator('[data-webpageviewplus-action="share"], .webpageviewplus-controls a').first.wait_for()
    for action in ("fullscreen", "reload"):
        expect(page.locator('[data-webpageviewplus-action="%s"]' % action)).to_be_hidden()

    load.click()
    expect(card).to_have_count(0)
    expect(page.locator('[data-webpageviewplus-action="reload"]')).to_be_visible()
    child = page.frame_locator(FRAME)
    child.locator("#viewport").filter(has_text="px").wait_for()
    wait_content_height(page, child)
    expect(page.locator(FRAME)).to_be_focused()


@pytest.mark.parametrize("how", ["load on tap", "reload"])
@pytest.mark.parametrize("title", ["Interactive fixed 300", "Schematic auto"])
def test_fullscreen_permission_survives_download_redirect(page, view_urls, how, title):
    """Load on tap and Reload frame an upload through CKAN's download URL,
    which redirects to the content origin: the frame's allow must name that
    origin, or the page loses fullscreen there."""
    if title.startswith("Interactive") and page.context.browser.browser_type.name != "chromium":
        pytest.skip("only Chromium gives fullscreen to opaque-origin frames, even before any redirect")
    if how == "load on tap":
        page.set_extra_http_headers({"Save-Data": "on"})
        page.goto(view_urls[title])
        page.locator("[data-webpageviewplus-load]").click()
    else:
        child = open_view(page, view_urls, title)
        assert child.locator("body").evaluate("() => document.fullscreenEnabled")
        click_in_frame(child, "#grow")
        expect(child.locator("#filler")).to_have_css("height", "500px")
        page.locator('[data-webpageviewplus-action="reload"]').click()
        expect(page.frame_locator(FRAME).locator("#filler")).to_have_css("height", "200px")
    child = page.frame_locator(FRAME)
    child.locator("#viewport").filter(has_text="px").wait_for()
    assert "/webpageviewplus/content/" in page.frames[1].url
    assert child.locator("body").evaluate("() => document.fullscreenEnabled")


RECORD_EVENTS = """
if (window.top === window) {
  window.wvpEvents = [];
  for (const name of ["loaded", "expanded", "failed"]) {
    document.addEventListener("webpageviewplus:" + name,
      e => wvpEvents.push([name, e.detail.title, e.target.className]));
  }
}
"""


def test_dom_events(page, view_urls):
    """loaded and expanded bubble from the view's wrapper for a site's own
    analytics; the extension sends them nowhere."""
    page.add_init_script(RECORD_EVENTS)
    page.add_init_script(NO_FULLSCREEN)
    open_view(page, view_urls, "Interactive fixed 300")
    page.evaluate("wvpEvents.length = 0")
    page.locator('[data-webpageviewplus-action="reload"]').click()
    page.wait_for_function("() => wvpEvents.some(e => e[0] === 'loaded')")
    page.locator(EXPAND).click()
    page.wait_for_function("() => wvpEvents.some(e => e[0] === 'expanded')")
    events = page.evaluate("wvpEvents")
    assert ["loaded", "Interactive fixed 300", "webpageviewplus"] in [
        [n, t, c.split()[0]] for n, t, c in events]
    assert not any(e[0] == "failed" for e in events)


def test_failed_event_on_a_slow_load(page, view_urls):
    """failed: a load the script started (here Load) that outlasts the
    timeout. The timeout is shortened through the wrapper's test hook."""
    page.add_init_script(RECORD_EVENTS)
    page.set_extra_http_headers({"Save-Data": "on"})
    page.goto(view_urls["Interactive fixed 300"])
    page.evaluate("document.querySelector('.webpageviewplus').setAttribute('data-load-timeout', '1')")
    page.locator("[data-webpageviewplus-load]").click()
    page.wait_for_function("() => wvpEvents.some(e => e[0] === 'failed')")
    # It still loads in the end, and says so.
    page.wait_for_function("() => wvpEvents.some(e => e[0] === 'loaded')")


def test_counting_beacon(page, view_urls):
    """One beacon when the page is left, with what happened to the view.
    Where the site has counting off (the dev site), the beacon URL is added
    to the wrapper before the script runs, so the script is tested anyway;
    the CI site counts this view for real."""
    url = view_urls["Counted fixed 300"]
    view_id = url.rsplit("view_id=", 1)[1]
    page.add_init_script("""
        if (window.top === window) {
            new MutationObserver((records, observer) => {
                const view = document.querySelector('.webpageviewplus');
                if (!view) return;
                observer.disconnect();
                if (!view.hasAttribute('data-count-url'))
                    view.setAttribute('data-count-url', '/webpageviewplus/count/%s');
            }).observe(document, {childList: true, subtree: true});
        }
    """ % view_id)
    # Not every engine shows a beacon's body to Playwright: record it.
    page.add_init_script("""
        if (window.top === window) {
            window.wvpBeacons = [];
            const send = navigator.sendBeacon.bind(navigator);
            navigator.sendBeacon = (url, body) => { wvpBeacons.push([url, String(body)]); return send(url, body); };
        }
    """)
    child = open_view(page, view_urls, "Counted fixed 300")
    page.locator(FRAME).scroll_into_view_if_needed()
    page.wait_for_timeout(1300)  # seen: half on screen for 1 s
    page.locator(".webpageviewplus-controls a").first.evaluate("a => a.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}))")
    hide = """() => {
        Object.defineProperty(document, 'visibilityState', {get: () => 'hidden', configurable: true});
        document.dispatchEvent(new Event('visibilitychange'));
    }"""
    # The page being hidden (switching tabs, locking a phone) is when it's
    # sent; leaving the page sends it too, but a page that's gone can't be
    # observed reliably.
    with page.expect_request(lambda r: "/webpageviewplus/count/" in r.url) as info:
        page.evaluate(hide)
    request = info.value
    assert request.method == "POST"
    beacons = page.evaluate("wvpBeacons")
    assert len(beacons) == 1 and beacons[0][0].endswith("/webpageviewplus/count/" + view_id)
    body = dict(p.split("=") for p in beacons[0][1].split("&"))
    assert body["seen"] == "1" and body["engaged"] == "1"
    assert set(body) == {"seen", "loaded", "engaged", "failed"}
    assert request.response().status == 204
    # Once per page load.
    page.evaluate(hide)
    page.wait_for_timeout(300)
    assert len(page.evaluate("wvpBeacons")) == 1


def test_counting_load_is_engagement(page, view_urls):
    """Load (here behind Save-Data) counts as using the view, and the frame
    it inserts as loaded."""
    url = view_urls["Counted fixed 300"]
    view_id = url.rsplit("view_id=", 1)[1]
    page.add_init_script("""
        if (window.top === window) {
            new MutationObserver((records, observer) => {
                const view = document.querySelector('.webpageviewplus');
                if (!view) return;
                observer.disconnect();
                if (!view.hasAttribute('data-count-url'))
                    view.setAttribute('data-count-url', '/webpageviewplus/count/%s');
            }).observe(document, {childList: true, subtree: true});
            window.wvpBeacons = [];
            const send = navigator.sendBeacon.bind(navigator);
            navigator.sendBeacon = (u, b) => { wvpBeacons.push(String(b)); return send(u, b); };
        }
    """ % view_id)
    page.set_extra_http_headers({"Save-Data": "on"})
    page.goto(url)
    page.locator("[data-webpageviewplus-load]").click()
    page.frame_locator(FRAME).locator("#viewport").filter(has_text="px").wait_for()
    page.evaluate("""() => {
        Object.defineProperty(document, 'visibilityState', {get: () => 'hidden', configurable: true});
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    page.wait_for_function("() => wvpBeacons.length === 1")
    body = dict(p.split("=") for p in page.evaluate("wvpBeacons[0]").split("&"))
    assert body["engaged"] == "1" and body["loaded"] == "1"


def test_uploads_report_other_hosts_without_blocking(page, view_urls):
    """The content origin sends Content-Security-Policy-Report-Only: a
    fetch from another host is reported (to CKAN), never blocked."""
    child = open_view(page, view_urls, "Interactive fixed 300")
    src = page.locator(FRAME).get_attribute("src")
    if "content-security-policy-report-only" not in page.request.get(src).headers:
        pytest.skip("this site has connection_reports off (the default; CI turns it on)")
    result = child.locator("body").evaluate("""() => new Promise(resolve => {
        document.addEventListener('securitypolicyviolation', e => resolve({
            disposition: e.disposition, directive: e.effectiveDirective,
            blocked: e.blockedURI}), {once: true});
        const img = new Image();
        img.src = 'https://tiles.example.invalid/1.png';
        setTimeout(() => resolve(null), 3000);
    })""")
    assert result is not None
    assert result["disposition"] == "report"
    assert result["blocked"].startswith("https://tiles.example.invalid")
    # Firefox also logs the (report-only) violation as a page error.
    page.errors[:] = [e for e in page.errors if "Report-Only" not in e]
