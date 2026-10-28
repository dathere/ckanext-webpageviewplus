"""Helpers shared by the browser tests.

Buttons inside the frame are clicked from the frame's own script: a
synthesized mouse click doesn't reach a cross-origin frame in every driver
(stock Firefox over WebDriver BiDi drops it).
"""
FRAME = "iframe.webpageviewplus-frame"
HEIGHT = "Math.round(document.querySelector('{}').getBoundingClientRect().height)".format(FRAME)


def open_view(page, view_urls, title):
    page.goto(view_urls[title])
    child = page.frame_locator(FRAME)
    # The fixture prints the viewport height once the parent has sent it,
    # so by then both sides of the protocol are running.
    child.locator("#viewport").filter(has_text="px").wait_for()
    view = page.locator(FRAME)
    if view.get_attribute("data-height-mode") == "auto":
        wait_content_height(page, child)
    return child


def wait_content_height(page, child):
    """Wait until an auto view has applied the content height the fixture
    measures, within the view's bounds: height reports are async."""
    view = page.locator(FRAME)
    content = child.locator("body").evaluate(
        "b => { const s = getComputedStyle(b); return Math.ceil(b.offsetHeight"
        " + parseFloat(s.marginTop) + parseFloat(s.marginBottom)); }"
    )
    low = int(view.get_attribute("data-min-height"))
    high = int(view.get_attribute("data-max-height"))
    expected = min(max(content, low), high)
    return wait_height(page, expected - 1, expected + 1)


def click_in_frame(child, selector):
    child.locator(selector).evaluate("b => b.click()")


def wait_height(page, low, high):
    page.wait_for_function(
        "([low, high]) => { const h = " + HEIGHT + "; return h >= low && h <= high; }",
        arg=[low, high],
    )
    return page.evaluate(HEIGHT)
