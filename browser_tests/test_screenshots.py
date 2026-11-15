"""Screenshots of pages that refuse framing, on seed.py's "Refuses framing"
view: its page's "blocked" check and screenshot are stored by the seed, so
nothing is fetched and no backend is called. The view form's "Take a new
screenshot" needs a login, so its script is checked on a public view page
the test adds the button's markup to (as form.html renders it), with the
action's answers stubbed.
"""
import re

import pytest
from axe_playwright_python.sync_playwright import Axe

AXE = Axe()
TITLE = "Refuses framing"
COLOUR = (0, 110, 130)  # seed.SCREENSHOT_COLOUR
ACTION = "**/api/3/action/webpageviewplus_screenshot_request"


def _decoded(img):
    img.scroll_into_view_if_needed()
    return img.evaluate("i => i.decode().then(() => [i.naturalWidth, i.naturalHeight])")


def _centre(img):
    return img.evaluate("""i => {
        const c = document.createElement("canvas");
        c.width = i.naturalWidth; c.height = i.naturalHeight;
        const g = c.getContext("2d"); g.drawImage(i, 0, 0);
        return Array.from(g.getImageData(c.width / 2, c.height / 2, 1, 1).data.slice(0, 3));
    }""")


def _near(found, wanted, slack=12):
    return all(abs(a - b) <= slack for a, b in zip(found, wanted))


@pytest.fixture
def card(page, view_urls):
    page.goto(view_urls[TITLE])
    return page


def test_card_shows_the_screenshot(card):
    img = card.locator("figure img[src*='/webpageviewplus/screenshot/']")
    assert _decoded(img) == [1350, 940]
    assert _near(_centre(img), COLOUR)
    assert img.get_attribute("alt") == "Screenshot of example.com"
    assert "Screenshot taken" in card.locator("figure figcaption").inner_text()
    # A card, not a frame of the page.
    assert card.locator("iframe[src*='example.com']").count() == 0


def test_card_axe(card):
    results = AXE.run(card, context={"include": [["figure"]]}, options={
        "resultTypes": ["violations"]})
    assert results.violations_count == 0, results.generate_report()


def test_list_thumbnail(page, dataset_url):
    page.goto(dataset_url)
    row = page.locator(".resource-item", has=page.locator(".heading", has_text="Screenshot example"))
    img = row.locator(".webpageviewplus-thumbnail img")
    assert "size=thumb" in img.get_attribute("src")
    width, height = _decoded(img)
    assert width <= 320 and height <= 180 and abs(width / height - 1350 / 940) < 0.02
    assert _near(_centre(img), COLOUR)
    assert row.locator(".format-label").count() == 0


PANEL = """<section data-webpageviewplus-screenshot>
  <p class="webpageviewplus-screenshot-status" role="status">Stored</p>
  <button type="button" data-webpageviewplus-screenshot-request
          data-api="/api/3/action/webpageviewplus_screenshot_request"
          data-view-id="view-1" data-pending="PENDING" data-failed="FAILED"
          data-cooldown="{cooldown}" {disabled}>Take a new screenshot</button>
  <span id="webpageviewplus-screenshot-wait">WAIT</span>
</section>"""


def _with_button(page, view_urls, cooldown=0):
    """The public view page, with the panel's button added before the
    extension's script runs, so the script binds it as on the form."""
    url = view_urls[TITLE]

    def add_panel(route):
        response = route.fetch()
        html = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + PANEL.format(
            cooldown=cooldown, disabled="disabled" if cooldown else ""), response.text(), count=1)
        route.fulfill(response=response, body=html)
    page.route(url, add_panel)
    page.goto(url)
    return page.locator("[data-webpageviewplus-screenshot-request]"), \
        page.locator(".webpageviewplus-screenshot-status")


def test_button_queues_and_stays_disabled(page, view_urls):
    sent = []

    def answer(route):
        sent.append(route.request)
        route.fulfill(json={"success": True, "result": {"state": "pending"}})
    page.route(ACTION, answer)
    button, status = _with_button(page, view_urls)
    button.click()
    status.filter(has_text="PENDING").wait_for()
    assert button.is_disabled()
    (request,) = sent
    assert request.method == "POST"
    # Stock Firefox over WebDriver BiDi doesn't hand Playwright the body.
    if request.post_data is not None:
        assert request.post_data_json == {"id": "view-1"}


def test_refusal_shows_its_message_then_reenables(page, view_urls):
    page.route(ACTION, lambda route: route.fulfill(status=409, json={
        "success": False,
        "error": {"id": ["You can take a new screenshot in 1 second."], "seconds_left": [1]}}))
    button, status = _with_button(page, view_urls)
    button.click()
    status.filter(has_text="in 1 second").wait_for()
    assert button.is_disabled()
    page.wait_for_function(
        "() => !document.querySelector('[data-webpageviewplus-screenshot-request]').disabled",
        timeout=5000)


def test_network_failure_reenables(page, view_urls):
    page.route(ACTION, lambda route: route.abort())
    button, status = _with_button(page, view_urls)
    button.click()
    status.filter(has_text="FAILED").wait_for()
    assert not button.is_disabled()


def test_cooldown_ends_by_itself(page, view_urls):
    button, _ = _with_button(page, view_urls, cooldown=1)
    assert button.is_disabled()
    page.wait_for_function(
        "() => !document.querySelector('[data-webpageviewplus-screenshot-request]').disabled",
        timeout=5000)
    assert page.locator("#webpageviewplus-screenshot-wait").inner_text() == ""
