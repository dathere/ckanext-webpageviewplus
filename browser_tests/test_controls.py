"""The control bar (Open in new tab, Reload, Fullscreen) and lazy loading."""
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from wvp_helpers import (
    FRAME, HEIGHT, click_in_frame, open_view, wait_content_height, wait_height,
)

VIEWS = ["Schematic auto", "Interactive auto", "Interactive fixed 300"]


def _origin(url):
    parts = urlsplit(url)
    return "{}://{}".format(parts.scheme, parts.netloc)


@pytest.mark.parametrize("title", VIEWS)
def test_open_in_new_tab(page, view_urls, title):
    open_view(page, view_urls, title)
    link = page.locator(".webpageviewplus-controls a")
    # CKAN's download URL, not the frame's (possibly signed, expiring) one.
    href = link.get_attribute("href")
    assert _origin(href) == _origin(page.url)
    assert "/download/" in href
    assert link.get_attribute("target") == "_blank"
    assert set(link.get_attribute("rel").split()) >= {"noopener", "noreferrer"}

    content_origin = _origin(page.locator(FRAME).get_attribute("src"))
    with page.expect_popup() as popup_info:
        link.click()
    popup = popup_info.value
    popup.wait_for_load_state()
    # The download redirects to the content origin, never served on CKAN's.
    assert _origin(popup.url) == content_origin
    assert popup.locator("#origin").inner_text() == content_origin


@pytest.mark.parametrize("title", ["Schematic auto", "Interactive auto"])
def test_reload_restores_page_and_height(page, view_urls, title):
    child = open_view(page, view_urls, title)
    start = page.evaluate(HEIGHT)
    click_in_frame(child, "#grow")
    wait_height(page, start + 295, start + 305)

    button = page.locator('[data-webpageviewplus-action="reload"]')
    reload_url = button.get_attribute("data-reload-url")
    button.click()
    page.wait_for_function(
        "url => document.querySelector('{}').src === url".format(FRAME), arg=reload_url)
    # The reloaded page starts small again (its filler back to 200px), and
    # the view follows it down.
    expect(child.locator("#filler")).to_have_css("height", "200px")
    child.locator("#viewport").filter(has_text="px").wait_for()
    assert abs(wait_content_height(page, child) - start) <= 1


def test_fullscreen(page, view_urls):
    open_view(page, view_urls, "Interactive fixed 300")
    button = page.locator('[data-webpageviewplus-action="fullscreen"]')
    if not page.evaluate("document.fullscreenEnabled"):
        assert button.is_hidden()
        pytest.skip("no element fullscreen in this browser")
    assert button.get_attribute("aria-pressed") == "false"

    button.click()
    page.wait_for_function(
        "() => document.fullscreenElement === document.querySelector('.webpageviewplus')")
    expect(button).to_have_attribute("aria-pressed", "true")

    button.click()
    page.wait_for_function("() => document.fullscreenElement === null")
    expect(button).to_have_attribute("aria-pressed", "false")


def test_lazy_loading(page, view_urls):
    # Push the view far below the fold before the first layout; the frame
    # must not load until it's scrolled
    # near. The spacer goes in as soon as the view's wrapper is parsed.
    page.add_init_script("""
        if (window.top === window) {
            new MutationObserver((records, observer) => {
                const view = document.querySelector('.webpageviewplus');
                if (!view) return;
                observer.disconnect();
                const spacer = document.createElement('div');
                spacer.style.height = '20000px';
                view.parentNode.insertBefore(spacer, view);
            }).observe(document, {childList: true, subtree: true});
        }
    """)
    page.goto(view_urls["Interactive fixed 300"])
    page.wait_for_load_state("load")
    page.wait_for_timeout(1000)
    frame = page.locator(FRAME).element_handle().content_frame()
    assert frame is None or frame.url in ("", "about:blank")

    page.locator(FRAME).scroll_into_view_if_needed()
    page.frame_locator(FRAME).locator("#origin").filter(has_text="null").wait_for()
