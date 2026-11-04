"""repo_view_plus: seed.py's Repository views, whose GitHub answers it writes
into CKAN's cache (GitHub is never asked). The page runs no scripts, so
Playwright reads and clicks it from its own world. Checked: the frame's
sandbox and the route's CSP, the inline stylesheet allowed by its hash,
links opening a new tab while in-page links scroll, Reload staying on
CKAN, the card for a missing repository, and the consent card naming
github.com.
"""
import re

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

from wvp_helpers import FRAME

TITLE = "Repository"
MISSING = "Missing repository"
ROUTE = "/webpageviewplus/repo/"


@pytest.fixture
def repo_urls(view_urls):
    if TITLE not in view_urls:
        pytest.skip("repo_view_plus isn't enabled on this site")
    return view_urls


def _open(page, url):
    with page.expect_response(lambda r: ROUTE in r.url) as answer:
        page.goto(url)
    child = page.frame_locator(FRAME)
    child.locator("h1, .repo-card").first.wait_for()
    return answer.value, child


def test_renders_without_scripts(page, repo_urls):
    response, child = _open(page, repo_urls[TITLE])
    frame = page.locator(FRAME)
    assert frame.get_attribute("sandbox") == "allow-popups allow-popups-to-escape-sandbox"
    assert ROUTE in frame.get_attribute("src")
    csp = response.headers["content-security-policy"]
    assert csp.startswith("sandbox allow-popups allow-popups-to-escape-sandbox;")
    assert "default-src 'none'" in csp and "script-src" not in csp
    expect(child.locator(".repo-head h1")).to_have_text("wvp-browser/example")
    expect(child.locator(".repo-files li")).to_have_text(["docs", "README.md"])
    expect(child.locator(".repo-readme h1")).to_have_text("Example")


def test_stylesheet_allowed_by_its_hash(page, repo_urls):
    # Blocked, the body would keep the browser's 8px margin.
    _, child = _open(page, repo_urls[TITLE])
    assert child.locator("body").evaluate("b => getComputedStyle(b).margin") == "0px"


def test_links_open_in_a_new_tab(page, repo_urls):
    _, child = _open(page, repo_urls[TITLE])
    with page.context.expect_page() as popup:
        child.get_by_role("link", name="example.com").click()
    popup.value.wait_for_url("https://example.com/**")
    assert page.url == repo_urls[TITLE]


def test_in_page_links_scroll(page, repo_urls):
    _, child = _open(page, repo_urls[TITLE])
    body = child.locator("body")
    assert body.evaluate("b => scrollY") == 0
    child.get_by_role("link", name="Usage").click()
    expect(child.locator("h2", has_text="Usage")).to_be_in_viewport()
    assert body.evaluate("b => location.hash") == "#user-content-usage"
    assert len(page.context.pages) == 1


def test_reload_stays_on_ckan(page, repo_urls):
    _open(page, repo_urls[TITLE])
    reload = page.locator('[data-webpageviewplus-action="reload"]')
    assert ROUTE in reload.get_attribute("data-reload-url")
    with page.expect_response(lambda r: ROUTE in r.url) as answer:
        reload.click()
    assert answer.value.status == 200
    expect(page.frame_locator(FRAME).locator(".repo-head h1")).to_have_text("wvp-browser/example")


def test_missing_repository_card(page, repo_urls):
    _, child = _open(page, repo_urls[MISSING])
    card = child.locator(".repo-card")
    expect(card).to_contain_text("This repository wasn't found, or it isn't public.")
    link = card.get_by_role("link", name="Open on GitHub")
    assert link.get_attribute("href") == "https://github.com/wvp-browser/missing"


def test_consent_card_names_github(page, repo_urls):
    page.set_extra_http_headers({"Sec-GPC": "1"})
    page.goto(repo_urls[TITLE])
    card = page.locator("[data-webpageviewplus-card]")
    expect(card).to_contain_text("This view loads content from github.com.")
    assert page.locator(FRAME).count() == 0
    page.get_by_role("button", name=re.compile(r"^Load( \(.+\))?$")).click()
    expect(page.frame_locator(FRAME).locator(".repo-head h1")).to_have_text("wvp-browser/example")


def test_axe(page, repo_urls):
    _open(page, repo_urls[TITLE])
    page.locator('[data-webpageviewplus-action="reload"]').wait_for()
    results = Axe().run(page, context=".webpageviewplus", options={
        "resultTypes": ["violations"], "iframes": False,
    })
    assert results.violations_count == 0, results.generate_report()
