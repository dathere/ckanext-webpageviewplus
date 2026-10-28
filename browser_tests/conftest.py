"""Browser tests against a running CKAN with webpage_view_plus, a content
origin and the data from seed.py. Not part of the CKAN pytest run: they
drive real browsers with Playwright.

Environment:
  WVP_BASE_URL   CKAN's URL (default http://localhost:5001, the dev stack)
  WVP_BROWSERS   engines to run, space-separated (default: chromium firefox webkit)
  WVP_CHROMIUM_CHANNEL, WVP_FIREFOX_CHANNEL
                 an installed browser to use instead of Playwright's build,
                 e.g. "chrome" and "moz-firefox"
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
    """{view title: URL of the resource page showing that view}."""
    package = _api("package_show", id=DATASET)
    resource = package["resources"][0]
    return {
        view["title"]: "{}/dataset/{}/resource/{}?view_id={}".format(
            BASE_URL, DATASET, resource["id"], view["id"]
        )
        for view in _api("resource_view_list", id=resource["id"])
    }


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


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    # The dev site's Flask debug toolbar covers controls on narrow screens.
    context.add_init_script("""
        if (window.top === window) document.addEventListener("DOMContentLoaded", () => {
            const s = document.createElement("style");
            s.textContent = "#flDebug, #flDebugToolbarHandle { display: none !important; }";
            document.head.append(s);
        });
    """)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    yield page
    context.close()
    assert page.errors == []
