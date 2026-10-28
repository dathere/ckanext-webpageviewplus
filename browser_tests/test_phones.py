"""Phones and tablets, control bar: Expand (where there's no element
fullscreen, as on iPhone Safari), Share, and the landscape lock in
fullscreen. Browser APIs a desktop engine has (or lacks) are replaced by
init scripts in the top window only.
"""
import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

from wvp_helpers import FRAME, click_in_frame, open_view

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
