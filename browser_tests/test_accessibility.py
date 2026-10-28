"""Accessibility of what the extension draws: axe-core on the view's wrapper,
the skip link and the live region.

axe runs on the wrapper only, with iframes off: CKAN's own page chrome isn't
the extension's, and the framed page is its author's responsibility (and
can't be inspected when it's cross-origin anyway).
"""
import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

from wvp_helpers import FRAME

VIEWS = ["Schematic auto", "Interactive auto", "Interactive fixed 300", "Strict"]
AXE = Axe()


def _goto(page, view_urls, title):
    page.goto(view_urls[title])
    # The script unhides Reload (and Fullscreen, where supported) once it has
    # run; axe skips hidden elements, so wait for that first.
    page.locator('[data-webpageviewplus-action="reload"]').wait_for()


@pytest.mark.parametrize("title", VIEWS)
def test_axe(page, view_urls, title):
    _goto(page, view_urls, title)
    results = AXE.run(page, context=".webpageviewplus", options={
        "resultTypes": ["violations"], "iframes": False,
    })
    assert results.violations_count == 0, results.generate_report()


@pytest.mark.parametrize("title", ["Interactive auto", "Strict"])
def test_skip_link(page, view_urls, title):
    _goto(page, view_urls, title)
    skip = page.locator(".webpageviewplus-skip")
    assert skip.bounding_box()["width"] <= 1  # hidden until focused
    skip.focus()
    assert skip.bounding_box()["width"] > 1
    target = skip.get_attribute("href").lstrip("#")

    page.keyboard.press("Enter")
    page.wait_for_function("id => document.activeElement && document.activeElement.id === id",
                           arg=target)
    # Past the frame, before the control bar: the next stop is Open in new tab.
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.textContent.trim()") == "Open in new tab"


def test_live_region_announces_reload(page, view_urls):
    _goto(page, view_urls, "Interactive auto")
    status = page.locator("[data-webpageviewplus-status]")
    assert status.get_attribute("role") == "status"
    title = page.locator(FRAME).get_attribute("title")
    # Whether the first load is announced depends on whether the frame beat
    # the script, so test the Reload, after marking the region.
    status.evaluate("s => { s.textContent = 'marker'; }")
    page.locator('[data-webpageviewplus-action="reload"]').click()
    expect(status).to_have_text("{} loaded".format(title))
