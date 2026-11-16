"""Browser tests against a running CKAN with webpage_view_plus, a content
origin and the data from seed.py. Not part of the CKAN pytest run: they
drive real browsers with Playwright.

Environment:
  WVP_BASE_URL   CKAN's URL (default http://localhost:5001, the dev stack)
  WVP_BROWSERS   engines to run, space-separated (default: chromium firefox webkit)
  WVP_CHROMIUM_CHANNEL, WVP_FIREFOX_CHANNEL
                 an installed browser to use instead of Playwright's build,
                 e.g. "chrome" and "moz-firefox"
  WVP_TEST_PASSWORD
                 the password seed.py gave its test editor and sysadmin; tests
                 needing a login skip without it (CI sets a random one)
"""
import json
import os
import urllib.parse
import urllib.request

import pytest
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("WVP_BASE_URL", "http://localhost:5001").rstrip("/")
DATASET = "wvp-browser"
ENGINES = os.environ.get("WVP_BROWSERS", "chromium firefox webkit").split()


def _api(action, **params):
    url = "{}/api/3/action/{}?{}".format(BASE_URL, action, urllib.parse.urlencode(params))
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)["result"]


@pytest.fixture(scope="session")
def view_urls():
    """{view title: URL of the resource page showing that view}, across the
    dataset's resources (seed.py keeps titles unique)."""
    package = _api("package_show", id=DATASET)
    return {
        view["title"]: "{}/dataset/{}/resource/{}?view_id={}".format(
            BASE_URL, DATASET, resource["id"], view["id"]
        )
        for resource in package["resources"]
        for view in _api("resource_view_list", id=resource["id"])
    }


@pytest.fixture(scope="session")
def dataset_url():
    """The seeded dataset's page, with its resource list."""
    return "{}/dataset/{}".format(BASE_URL, DATASET)


@pytest.fixture(scope="session")
def playwright():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session", params=ENGINES)
def browser(request, playwright):
    engine = request.param
    channel = os.environ.get("WVP_{}_CHANNEL".format(engine.upper()))
    browser = getattr(playwright, engine).launch(**({"channel": channel} if channel else {}))
    yield browser
    browser.close()


HIDE_DEBUG_TOOLBAR = """
    if (window.top === window) document.addEventListener("DOMContentLoaded", () => {
        const s = document.createElement("style");
        s.textContent = "#flDebug, #flDebugToolbarHandle { display: none !important; }";
        document.head.append(s);
    });
"""


def _page(browser, **context_options):
    context = browser.new_context(**context_options)
    # The dev site's Flask debug toolbar covers controls on narrow screens.
    context.add_init_script(HIDE_DEBUG_TOOLBAR)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    return context, page


@pytest.fixture
def page(browser):
    context, page = _page(browser, viewport={"width": 1280, "height": 800})
    yield page
    context.close()
    assert page.errors == []


@pytest.fixture
def touch_page(browser):
    """A phone-sized page with touch. Playwright's Firefox has no isMobile,
    so tests check (pointer: coarse) and skip where it doesn't match."""
    options = {"viewport": {"width": 390, "height": 844}, "has_touch": True}
    if browser.browser_type.name != "firefox":
        options["is_mobile"] = True
    context, page = _page(browser, **options)
    yield page
    context.close()
    assert page.errors == []


@pytest.fixture
def login(page):
    """Log ``page`` in as seed.py's test editor ("editor") or sysadmin
    ("sysadmin") through CKAN's login form."""
    password = os.environ.get("WVP_TEST_PASSWORD")
    if not password:
        pytest.skip("WVP_TEST_PASSWORD isn't set: seed.py made no test logins")

    def log_in(role):
        page.goto(BASE_URL + "/user/login")
        page.fill("#field-login", "wvp-test-" + role)
        page.fill("#field-password", password)
        with page.expect_navigation():
            page.locator("form button[type=submit].btn-primary").click()
        return page
    return log_in
