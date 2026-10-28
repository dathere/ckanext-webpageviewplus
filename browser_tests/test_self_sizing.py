"""Self-sizing and the qsv protocol, against the fixture in
ckanext/webpageviewplus/tests/fixtures/qsv_protocol.html.

Buttons inside the frame are clicked from the frame's own script: a
synthesized mouse click doesn't reach a cross-origin frame in every driver
(stock Firefox over WebDriver BiDi drops it).
"""
import re

import pytest

FRAME = "iframe.webpageviewplus-frame"
AUTO = ["Schematic auto", "Interactive auto"]
HEIGHT = "Math.round(document.querySelector('{}').getBoundingClientRect().height)".format(FRAME)


def _open(page, view_urls, title):
    page.goto(view_urls[title])
    child = page.frame_locator(FRAME)
    # The fixture prints the viewport height once the parent has sent it,
    # so by then both sides of the protocol are running.
    child.locator("#viewport").filter(has_text="px").wait_for()
    view = page.locator(FRAME)
    if view.get_attribute("data-height-mode") == "auto":
        # Its height reports are async too: wait until the view has applied
        # the content height the fixture measures, within the view's bounds.
        content = child.locator("body").evaluate(
            "b => { const s = getComputedStyle(b); return Math.ceil(b.offsetHeight"
            " + parseFloat(s.marginTop) + parseFloat(s.marginBottom)); }"
        )
        low = int(view.get_attribute("data-min-height"))
        high = int(view.get_attribute("data-max-height"))
        expected = min(max(content, low), high)
        _wait_height(page, expected - 1, expected + 1)
    return child


def _click(child, button):
    child.locator(button).evaluate("b => b.click()")


def _wait_height(page, low, high):
    page.wait_for_function(
        "([low, high]) => { const h = " + HEIGHT + "; return h >= low && h <= high; }",
        arg=[low, high],
    )
    return page.evaluate(HEIGHT)


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
