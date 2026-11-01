"""pdf_view_plus: seed.py's uploaded PDF, shown by the vendored PDF.js from
the content origin. Checked from inside the frame: the page renders, the
options wvp-viewer.js sets took effect (they're applied on PDF.js's
webviewerloaded event, which it sends to its own document only because the
CKAN page is another origin), and its download, print and editing buttons
are hidden. Linked PDFs are fetched by CKAN, which can't reach the internet
in CI; the unit tests cover them.
"""
import pytest
from axe_playwright_python.sync_playwright import Axe

from wvp_helpers import FRAME

AXE = Axe()
TITLE = "PDF fixture"


@pytest.fixture
def viewer(page, view_urls):
    if TITLE not in view_urls:
        pytest.skip("pdf_view_plus isn't enabled on this site")
    page.goto(view_urls[TITLE])
    child = page.frame_locator(FRAME)
    child.locator(".page canvas").first.wait_for()
    return page, child


def test_renders_from_content_origin(viewer):
    page, child = viewer
    frame = page.locator(FRAME)
    assert "/webpageviewplus/pdfjs/" in frame.get_attribute("src")
    assert frame.get_attribute("sandbox").startswith("allow-scripts allow-same-origin")
    child.locator(".textLayer", has_text="ckanext-webpageviewplus PDF fixture").wait_for()


def test_options_applied(viewer):
    _, child = viewer
    options = child.locator("body").evaluate("""() => ({
        scripting: PDFViewerApplicationOptions.get('enableScripting'),
        editor: PDFViewerApplicationOptions.get('annotationEditorMode'),
        range: PDFViewerApplicationOptions.get('disableRange'),
    })""")
    assert options == {"scripting": False, "editor": -1, "range": True}


@pytest.mark.parametrize("button", [
    "#downloadButton", "#printButton", "#secondaryOpenFile", "#editorModeButtons",
])
def test_buttons_hidden(viewer, button):
    _, child = viewer
    assert child.locator(button).is_hidden()


def test_reload_mints_a_fresh_viewer(viewer):
    page, child = viewer
    reload = page.locator('[data-webpageviewplus-action="reload"]')
    assert "/webpageviewplus/pdf-view/" in reload.get_attribute("data-reload-url")
    # CKAN's route re-checks access and redirects to a newly signed viewer.
    with page.expect_response(lambda r: "/webpageviewplus/pdf-view/" in r.url) as answer:
        reload.click()
    assert answer.value.status == 302
    assert "/webpageviewplus/pdfjs/" in answer.value.headers["location"]
    child.locator(".page canvas").first.wait_for()


def test_axe(viewer):
    page, _ = viewer
    page.locator('[data-webpageviewplus-action="reload"]').wait_for()
    results = AXE.run(page, context=".webpageviewplus", options={
        "resultTypes": ["violations"], "iframes": False,
    })
    assert results.violations_count == 0, results.generate_report()
