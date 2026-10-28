"""Reflow (WCAG 1.4.10): at a 320px-wide viewport the frame gets a tall
floor, and a view that hides its control bar still offers Open in new tab.

Playwright can't set browser zoom; a 320x200 viewport is what 400% zoom
gives on a 1280x800 screen.
"""
import pytest
from playwright.sync_api import expect

from wvp_helpers import FRAME, click_in_frame

FLOOR = 600
NARROW = {"width": 320, "height": 200}


def _frame_height(page):
    return page.locator(FRAME).bounding_box()["height"]


@pytest.mark.parametrize("title", ["Aspect 16/9", "Fixed 300 no bar", "Interactive fixed 300"])
def test_tall_floor_when_narrow(page, view_urls, title):
    page.set_viewport_size(NARROW)
    page.goto(view_urls[title])
    assert _frame_height(page) >= FLOOR - 1


def test_no_floor_when_wide(page, view_urls):
    page.goto(view_urls["Fixed 300 no bar"])
    assert abs(_frame_height(page) - 300) <= 1


def test_open_link_when_bar_hidden(page, view_urls):
    page.goto(view_urls["Fixed 300 no bar"])
    link = page.locator(".webpageviewplus-narrow-open")
    expect(link).to_be_hidden()
    page.set_viewport_size(NARROW)
    expect(link).to_be_visible()
    assert link.get_attribute("target") == "_blank"
    assert "/download/" in link.get_attribute("href")


def test_no_extra_link_with_bar(page, view_urls):
    page.set_viewport_size(NARROW)
    page.goto(view_urls["Interactive fixed 300"])
    assert page.locator(".webpageviewplus-narrow-open").count() == 0
    expect(page.locator(".webpageviewplus-controls a")).to_be_visible()


NO_ANCHORING = """
if (window.top === window) {
  const supports = CSS.supports.bind(CSS);
  CSS.supports = (p, v) => p === "overflow-anchor" ? false : supports(p, v);
  document.addEventListener("DOMContentLoaded", () => {
    const s = document.createElement("style");
    s.textContent = "html, body { overflow-anchor: none; }";
    document.head.append(s);
  });
}
"""


@pytest.mark.parametrize("anchoring", [True, False], ids=["anchoring", "no-anchoring"])
def test_scroll_kept_when_frame_above_shrinks_under_floor(page, view_urls, anchoring):
    """A frame above the reader that reports a height under the floor stays
    600px tall, and the text below must stay put. Where the browser anchors
    scrolling it does this itself; otherwise the script scrolls, by the
    height actually rendered. The second case stands in for a browser
    without anchoring by turning it off and hiding it from the script.

    Heights are posted from the frame's own script: Chromium stops the
    fixture's ResizeObserver while a cross-origin frame is offscreen."""
    if not anchoring:
        page.add_init_script(NO_ANCHORING)
    page.set_viewport_size(NARROW)
    page.goto(view_urls["Interactive auto"])
    # Lazy: 200px tall, the frame starts below the fold.
    page.locator(FRAME).scroll_into_view_if_needed()
    child = page.frame_locator(FRAME)
    child.locator("#viewport").filter(has_text="px").wait_for()

    def report(height):
        """Make the fixture's content `height` tall and post it. The fixture
        also reports its own height (on resize, and on timers), so the two
        must agree or its reports would undo the test's."""
        return child.locator("body").evaluate("""(b, h) => {
            const f = document.getElementById('filler');
            const measure = () => { const s = getComputedStyle(b);
                return Math.ceil(b.offsetHeight + parseFloat(s.marginTop)
                                 + parseFloat(s.marginBottom)); };
            f.style.height = Math.max(0, f.offsetHeight + h - measure()) + 'px';
            const got = measure();
            parent.postMessage({qsvVizHeight: got}, '*');
            return got;
        }""", height)

    tall = report(1000)
    assert tall > 900
    page.wait_for_function(
        "t => Math.abs(document.querySelector('%s').getBoundingClientRect().height - t) <= 1"
        % FRAME, arg=tall)
    # Something to read below the frame, and room to scroll past it.
    page.evaluate("""() => {
        const s = document.createElement('div');
        s.id = 'below'; s.style.height = '3000px';
        document.querySelector('.webpageviewplus').after(s);
        const f = document.querySelector('%s').getBoundingClientRect();
        // Instant: Bootstrap makes the root scroll smoothly.
        window.scrollTo({top: window.scrollY + f.bottom + 50, behavior: 'instant'});
    }""" % FRAME)
    top = page.evaluate("document.getElementById('below').getBoundingClientRect().top")
    short = report(450)
    assert short < FLOOR - 50
    page.wait_for_function(
        "s => Math.abs(parseFloat(document.querySelector('%s').style.height) - s) <= 1"
        % FRAME, arg=short)
    assert abs(_frame_height(page) - FLOOR) <= 1
    after = page.evaluate("document.getElementById('below').getBoundingClientRect().top")
    assert abs(after - top) <= 2, after - top
