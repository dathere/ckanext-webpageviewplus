"""pdf_view_plus with pdf_without_content_origin: PDF.js served from CKAN's
own host, in a frame with no origin of its own.

Runs against a second CKAN on the same database (WVP_NO_ORIGIN_URL; in CI,
ci_no_origin.ini on port 5002), with no content origin and the setting on,
and skips without one. seed.py's "PDF fixture" view is in a public dataset.
Checked: the PDF renders, the frame and the viewer opened on its own both
have no origin (cookies, storage and the parent page out of reach), links
open in tabs that escape the sandbox, and the view form's first-page drawing
works through its frame with no origin.
"""
import base64
import json
import os
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from axe_playwright_python.sync_playwright import Axe

from conftest import BASE_URL, _page
from wvp_helpers import FRAME

AXE = Axe()
TITLE = "PDF fixture"
HOST_URL = os.environ.get("WVP_NO_ORIGIN_URL", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not HOST_URL, reason="needs a CKAN with pdf_without_content_origin (WVP_NO_ORIGIN_URL)")

PROBE = """() => {
    const out = {origin: self.origin};
    try { out.cookie = document.cookie; } catch (e) { out.cookie = 'throws'; }
    try { localStorage.getItem('x'); out.storage = 'readable'; } catch (e) { out.storage = 'throws'; }
    try { out.parent = window.parent === window ? 'top' : String(!!window.parent.document); }
    catch (e) { out.parent = 'throws'; }
    return out;
}"""


# PDF.js's worker can't start in a frame with no origin (Chrome refuses a
# module worker there, Firefox says the worker file "may not load data"), so
# it runs on the page instead: expected here, any other error isn't.
WORKER_REFUSED = re.compile(r"may not load data from \S+/pdf\.worker\.mjs")


@pytest.fixture
def page(browser):
    context, page = _page(browser, viewport={"width": 1280, "height": 800})
    yield page
    context.close()
    assert [e for e in page.errors if not WORKER_REFUSED.search(e)] == []


@pytest.fixture
def view_url(view_urls):
    if TITLE not in view_urls:
        pytest.skip("pdf_view_plus isn't enabled on this site")
    return HOST_URL + view_urls[TITLE][len(BASE_URL):]


@pytest.fixture
def viewer(page, view_url):
    page.goto(view_url)
    child = page.frame_locator(FRAME)
    child.locator(".page canvas").first.wait_for()
    return page, child


def test_renders_with_no_origin(viewer):
    page, child = viewer
    frame = page.locator(FRAME)
    assert frame.get_attribute("src").startswith("/webpageviewplus/pdfjs/")
    assert "allow-same-origin" not in frame.get_attribute("sandbox")
    assert "allow-popups-to-escape-sandbox" in frame.get_attribute("sandbox")
    child.locator(".textLayer", has_text="ckanext-webpageviewplus PDF fixture").wait_for()
    assert child.locator("body").evaluate(PROBE) == {
        "origin": "null", "cookie": "throws", "storage": "throws", "parent": "throws"}


def test_options_applied(viewer):
    _, child = viewer
    options = child.locator("body").evaluate("""() => ({
        scripting: PDFViewerApplicationOptions.get('enableScripting'),
        editor: PDFViewerApplicationOptions.get('annotationEditorMode'),
        range: PDFViewerApplicationOptions.get('disableRange'),
        links: PDFViewerApplicationOptions.get('externalLinkTarget'),
    })""")
    assert options == {"scripting": False, "editor": -1, "range": True, "links": 2}


def test_viewer_opened_alone_has_no_origin(page, viewer):
    # The header sandbox, not the frame's: anyone can open the viewer's URL.
    src = viewer[0].locator(FRAME).get_attribute("src")
    alone = page.context.new_page()
    alone.goto(HOST_URL + src)
    alone.locator(".page canvas").first.wait_for()
    assert alone.evaluate(PROBE) == {
        "origin": "null", "cookie": "throws", "storage": "throws", "parent": "top"}


def test_links_open_outside_the_sandbox(viewer):
    page, child = viewer
    with page.expect_popup() as popup:
        child.locator("body").evaluate("""() => {
            const a = document.createElement('a');
            a.href = location.origin + '/api/3/action/status_show';
            a.target = '_blank';
            document.body.appendChild(a);
            a.click();
        }""")
    tab = popup.value
    tab.wait_for_load_state()
    assert tab.evaluate("self.origin") == HOST_URL


def test_reload_stays_on_ckan_host(viewer):
    page, child = viewer
    reload = page.locator('[data-webpageviewplus-action="reload"]')
    with page.expect_response(lambda r: "/webpageviewplus/pdf-view/" in r.url) as answer:
        reload.click()
    assert answer.value.status == 302
    assert urlsplit(answer.value.headers["location"]).path.startswith("/webpageviewplus/pdfjs/")
    child.locator(".page canvas").first.wait_for()


FIRST_PAGE = """<select id="field-thumbnail_resource"><option value="pdf:first-page" selected>First page</option></select>
<p data-webpageviewplus-first-page data-view-id="{view_id}" data-ready="false"
   data-open-url="/webpageviewplus/pdf-view/{view_id}" data-content-origin="" data-opaque="true"
   data-api="/wvp-test/pdf-thumbnail" data-working="WORKING" data-done="DONE"
   data-failed="FAILED" data-save-first="SAVE"></p>"""


def test_form_draws_the_first_page(page, view_url):
    # The view form's markup (pdf_form.html) added to the public view page
    # before the extension's script runs, as the form needs a login; the
    # action that stores the picture is stubbed.
    view_id = parse_qs(urlsplit(view_url).query)["view_id"][0]
    sent = []

    def add_form(route):
        response = route.fetch()
        html = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + FIRST_PAGE.format(view_id=view_id),
                      response.text(), count=1)
        route.fulfill(response=response, body=html)

    def answer(route):
        if route.request.method == "POST":
            # Stock Firefox over WebDriver BiDi doesn't give Playwright the body.
            body = route.request.post_data_buffer
            sent.append(json.loads(body) if body else None)
        route.fulfill(json={"success": True, "result": {}})

    page.route(view_url, add_form)
    page.route("**/wvp-test/pdf-thumbnail", answer)
    page.goto(view_url)
    status = page.locator("[data-webpageviewplus-first-page]")
    status.filter(has_text="DONE").wait_for(timeout=30000)
    assert sent, "the picture was never posted"
    if sent[0] is not None:
        assert sent[0]["id"] == view_id
        assert base64.b64decode(sent[0]["image"])[1:4] == b"PNG"


def test_axe(viewer):
    page, _ = viewer
    page.locator('[data-webpageviewplus-action="reload"]').wait_for()
    results = AXE.run(page, context=".webpageviewplus", options={
        "resultTypes": ["violations"], "iframes": False,
    })
    assert results.violations_count == 0, results.generate_report()
