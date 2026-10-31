"""Self-sizing and the qsv protocol, against the fixtures in
ckanext/webpageviewplus/tests/fixtures/: qsv_protocol.html, and
resize_message.html for the generic message the README documents."""
import re

import pytest

from wvp_helpers import (
    FRAME, HEIGHT, click_in_frame as _click, open_view as _open, post_from_frame,
    record_heights, wait_content_height, wait_height as _wait_height,
)

AUTO = ["Schematic auto", "Interactive auto"]
# Opaque (interactive) and real (data-schematic) origins: both trust checks.
GENERIC = ["Generic auto", "Generic schematic auto"]
# frame.FALLBACK_HEIGHT: an auto view's height until its page reports one.
FALLBACK_HEIGHT = 600


def _bounds(page):
    view = page.locator(FRAME)
    return int(view.get_attribute("data-min-height")), int(view.get_attribute("data-max-height"))


def _wait_recorded(page, height):
    page.wait_for_function("h => wvpHeights.includes(h)", arg=height)


@pytest.mark.parametrize("title, opaque", [
    ("Schematic auto", False),
    ("Interactive auto", True),
    ("Interactive fixed 300", True),
])
def test_frame_origin(page, view_urls, title, opaque):
    child = _open(page, view_urls, title)
    frame_origin = page.locator(FRAME).get_attribute("data-frame-origin")
    expected = "null" if opaque else frame_origin
    assert child.locator("#origin").inner_text() == expected
    assert page.locator(FRAME).get_attribute("data-opaque") == ("true" if opaque else "false")


@pytest.mark.parametrize("title", AUTO + ["Interactive fixed 300"])
def test_viewport_reply(page, view_urls, title):
    child = _open(page, view_urls, title)
    assert child.locator("#viewport").inner_text() == "800px"


@pytest.mark.parametrize("title", AUTO)
def test_auto_height_follows_content(page, view_urls, title):
    child = _open(page, view_urls, title)
    start = page.evaluate(HEIGHT)
    _click(child, "#grow")
    grown = _wait_height(page, start + 295, start + 305)
    _click(child, "#shrink")
    _wait_height(page, start - 5, start + 5)
    assert grown > start


def test_fixed_height_ignores_reports(page, view_urls):
    child = _open(page, view_urls, "Interactive fixed 300")
    _click(child, "#grow")
    page.wait_for_timeout(500)
    assert page.evaluate(HEIGHT) == 300


@pytest.mark.parametrize("title", AUTO)
def test_messages_from_other_frames_ignored(page, view_urls, title):
    _open(page, view_urls, title)
    start = page.evaluate(HEIGHT)
    # A second copy of the page, same src and sandbox, so its messages
    # carry the origin the view trusts: only event.source tells them apart.
    # It's narrow, so the heights it reports (on load and shortly after)
    # differ from the view's.
    record_heights(page)
    page.evaluate("""() => {
        const view = document.querySelector('%s');
        const f = document.createElement('iframe');
        f.id = 'foreign';
        f.style.width = '100px';
        if (view.hasAttribute('sandbox')) f.setAttribute('sandbox', view.getAttribute('sandbox'));
        f.src = view.src;
        document.body.appendChild(f);
    }""" % FRAME)
    page.frame_locator("#foreign").locator("#origin").filter(has_text=re.compile(r"\S")).wait_for()
    page.wait_for_timeout(1200)
    assert all(abs(h - start) <= 1 for h in page.evaluate("wvpHeights"))


@pytest.mark.parametrize("title", AUTO)
def test_reveal(page, view_urls, title):
    child = _open(page, view_urls, title)
    start = page.evaluate(HEIGHT)
    _click(child, "#grow")
    _click(child, "#grow")
    _wait_height(page, start + 595, start + 605)

    page.evaluate("window.scrollTo(0, 0)")
    _click(child, "#reveal-bottom")
    page.wait_for_function(
        "() => document.querySelector('{}').getBoundingClientRect().bottom"
        " <= window.innerHeight + 1".format(FRAME)
    )

    _click(child, "#reveal-top")
    # The fixture asks for its point 150px down; the view keeps 80px of
    # context above it.
    page.wait_for_function(
        "() => Math.abs(document.querySelector('{}').getBoundingClientRect().top"
        " + 70) <= 2".format(FRAME)
    )


@pytest.mark.parametrize("title", GENERIC)
def test_generic_message_follows_content(page, view_urls, title):
    child = _open(page, view_urls, title, qsv=False)
    start = page.evaluate(HEIGHT)
    assert start != FALLBACK_HEIGHT
    _click(child, "#grow")
    grown = wait_content_height(page, child)
    _click(child, "#shrink")
    shrunk = wait_content_height(page, child)
    assert abs(grown - start - 300) <= 2
    assert abs(shrunk - start) <= 2


def test_generic_message_ignored_on_fixed_height(page, view_urls):
    child = _open(page, view_urls, "Interactive fixed 300")
    post_from_frame(child, {"type": "webpageviewplus:resize", "height": 900})
    page.wait_for_timeout(500)
    assert page.evaluate(HEIGHT) == 300


@pytest.mark.parametrize("title", AUTO)
def test_reports_clamped_to_bounds(page, view_urls, title):
    child = _open(page, view_urls, title)
    low, high = _bounds(page)
    content = page.evaluate(HEIGHT)
    assert content > low + 1, "the fixture must be taller than min_height"
    record_heights(page)
    post_from_frame(child, {"qsvVizHeight": high * 10})
    _wait_recorded(page, high)
    # The frame resized, so the fixture re-reports its own height.
    _wait_height(page, content - 1, content + 1)
    post_from_frame(child, {"type": "webpageviewplus:resize", "height": 1})
    _wait_recorded(page, low)
    assert all(low <= h <= high for h in page.evaluate("wvpHeights"))


@pytest.mark.parametrize("title", AUTO)
def test_malformed_heights_ignored(page, view_urls, title):
    child = _open(page, view_urls, title)
    record_heights(page)
    for bad in ["tall", "300px", 0, -250, float("nan"), float("inf"), None, {}]:
        post_from_frame(child, {"qsvVizHeight": bad})
        post_from_frame(child, {"type": "webpageviewplus:resize", "height": bad})
    post_from_frame(child, {"type": "webpageviewplus:resize"})
    post_from_frame(child, "qsvVizHeight")
    post_from_frame(child, None)
    page.wait_for_timeout(800)
    assert page.evaluate("wvpHeights") == []


@pytest.mark.parametrize("title", AUTO)
def test_viewport_resent_when_window_resizes(page, view_urls, title):
    child = _open(page, view_urls, title)
    page.set_viewport_size({"width": 1280, "height": 600})
    child.locator("#viewport").filter(has_text="600px").wait_for()


def test_auto_without_reports_keeps_fallback(page, view_urls):
    # Strict runs no scripts, so the page never reports, like a schematic
    # from a qsv without self-sizing.
    page.goto(view_urls["Strict auto"])
    view = page.locator(FRAME)
    assert view.get_attribute("data-height-mode") == "auto"
    page.frame_locator(FRAME).locator("#origin").wait_for(state="attached")
    page.wait_for_timeout(1000)
    low, high = _bounds(page)
    assert page.evaluate(HEIGHT) == min(max(FALLBACK_HEIGHT, low), high)
