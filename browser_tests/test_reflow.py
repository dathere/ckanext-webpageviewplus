"""Reflow (WCAG 1.4.10): at a 320px-wide viewport the frame gets a tall
floor, and a view that hides its control bar still offers Open in new tab.

Playwright can't set browser zoom; a 320x200 viewport is what 400% zoom
gives on a 1280x800 screen.
"""
import pytest
from playwright.sync_api import expect

from wvp_helpers import FRAME

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
