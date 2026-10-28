"""Self-sizing and the qsv protocol, against the fixture in
ckanext/webpageviewplus/tests/fixtures/qsv_protocol.html."""
import re

import pytest

from wvp_helpers import FRAME, HEIGHT, click_in_frame as _click, open_view as _open, wait_height as _wait_height

AUTO = ["Schematic auto", "Interactive auto"]


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
    # differ from the view's. Every height the script sets is recorded,
    # because the view's own reports would quickly undo a wrong resize (a
    # MutationObserver sees each assignment; a ResizeObserver could miss one
    # undone within the same frame).
    page.evaluate("""() => {
        const view = document.querySelector('%s');
        window.wvpHeights = [];
        new MutationObserver(() => wvpHeights.push(parseFloat(view.style.height)))
            .observe(view, {attributes: true, attributeFilter: ['style']});
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
