"""The consent card: "Always load from <host>" is remembered in this browser,
later views from that host load without asking, and "Ask me again" forgets.
"""
import re

from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

from wvp_helpers import FRAME

TITLE = "Consent example.com"
CARD = "[data-webpageviewplus-card]"
NOTE = "[data-webpageviewplus-remembered]"
KEY = "webpageviewplus.consent"


def _fresh(page, view_urls):
    page.goto(view_urls[TITLE])
    page.evaluate("k => localStorage.removeItem(k)", KEY)
    page.reload()


def test_card_explains_and_has_no_frame(page, view_urls):
    _fresh(page, view_urls)
    card = page.locator(CARD)
    expect(card).to_contain_text("This view loads content from example.com.")
    expect(card).to_contain_text("it may recognise you if you are signed in there")
    assert page.locator(FRAME).count() == 0
    expect(page.locator(NOTE)).to_be_hidden()
    results = Axe().run(page, context=".webpageviewplus", options={
        "resultTypes": ["violations"], "iframes": False,
    })
    assert results.violations_count == 0, results.generate_report()


def test_load_once_is_not_remembered(page, view_urls):
    _fresh(page, view_urls)
    page.get_by_role("button", name=re.compile(r"^Load( \(.+\))?$")).click()
    expect(page.locator(FRAME)).to_have_count(1)
    assert page.evaluate("k => localStorage.getItem(k)", KEY) is None
    page.reload()
    expect(page.locator(CARD)).to_be_visible()


def test_always_is_remembered_until_forgotten(page, view_urls):
    _fresh(page, view_urls)
    page.get_by_role("button", name="Always load from example.com").click()
    expect(page.locator(CARD)).to_have_count(0)
    expect(page.locator(FRAME)).to_have_count(1)
    expect(page.locator(NOTE)).to_be_visible()

    page.reload()
    expect(page.locator(FRAME)).to_have_count(1)
    expect(page.locator(CARD)).to_have_count(0)
    note = page.locator(NOTE)
    expect(note).to_contain_text("loads without asking in this browser")

    note.get_by_role("button", name="Ask me again").click()
    expect(note).to_have_text("You will be asked again next time.")
    page.reload()
    expect(page.locator(CARD)).to_be_visible()
    assert page.locator(FRAME).count() == 0


def test_save_data_still_asks(page, view_urls):
    _fresh(page, view_urls)
    page.evaluate("k => localStorage.setItem(k, JSON.stringify(['example.com']))", KEY)
    page.set_extra_http_headers({"Save-Data": "on"})
    page.reload()
    expect(page.locator(CARD)).to_be_visible()
    assert page.locator(FRAME).count() == 0
