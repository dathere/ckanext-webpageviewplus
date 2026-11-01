"""Thumbnails in the dataset page's resource list (the
webpage_view_plus_thumbnails plugin), on seed.py's cases: an uploaded image
a view picks, a service with a logo (Vimeo) and one without (Power BI).
Rows with no thumbnail keep CKAN's format label. Nothing here is fetched
from another site.
"""
import pytest
from axe_playwright_python.sync_playwright import Axe

from conftest import DATASET, _api

AXE = Axe()
THUMB = ".webpageviewplus-thumbnail img"


@pytest.fixture
def listing(page, dataset_url):
    page.goto(dataset_url)
    return page


def _row(page, name):
    return page.locator(".resource-item", has=page.locator(".heading", has_text=name))


def _decoded_size(img):
    # Lazy-loaded: in view first, then wait for the image itself.
    img.scroll_into_view_if_needed()
    return img.evaluate("i => i.decode().then(() => [i.naturalWidth, i.naturalHeight])")


def test_picked_image(listing):
    row = _row(listing, "Picked thumbnail page")
    img = row.locator(THUMB)
    assert "/webpageviewplus/thumbnail/" in img.get_attribute("src")
    assert img.get_attribute("alt") == ""
    width, height = _decoded_size(img)
    # Re-encoded within 320 x 180 from the 640 x 360 upload.
    assert (width, height) == (320, 180)
    assert row.locator(".format-label").count() == 0
    assert row.locator(".webpageviewplus-format").text_content() == "HTML"


def test_service_logo(listing):
    row = _row(listing, "Vimeo link")
    img = row.locator(THUMB)
    assert img.get_attribute("src").endswith("/webpageviewplus/logos/vimeo.svg")
    width, height = _decoded_size(img)
    assert width > 0 and height > 0
    assert row.locator(".format-label").count() == 0
    assert row.locator(".webpageviewplus-format").text_content() == "Vimeo"


def test_service_name_without_logo(listing):
    row = _row(listing, "Power BI link")
    assert row.locator(".webpageviewplus-thumbnail").count() == 0
    assert row.locator(".format-label").text_content() == "Power BI"


@pytest.mark.parametrize("name, label", [
    # A view, but nothing picked and no service.
    ("qsv protocol fixture", "HTML"),
    # No Website (Plus) view at all.
    ("Thumbnail image", "PNG"),
])
def test_other_rows_unchanged(listing, name, label):
    row = _row(listing, name)
    assert row.locator(".webpageviewplus-thumbnail").count() == 0
    assert row.locator(".format-label").text_content() == label


def test_names_clear_of_icons(listing):
    # With a thumbnail in the list, :has() widens every row's gutter;
    # without it a thumbnail would cover the start of the name.
    listing.locator(THUMB).first.wait_for()
    edges = listing.evaluate("""() => [...document.querySelectorAll('.resource-item')].map(item => {
        const icon = item.querySelector('.webpageviewplus-thumbnail, .format-label');
        const heading = item.querySelector('.heading');
        const text = [...heading.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
        const range = document.createRange();
        range.selectNodeContents(text);
        return [heading.textContent.trim().slice(0, 30),
                icon.getBoundingClientRect().right, range.getBoundingClientRect().left];
    })""")
    # Every seeded resource (seed.py adds a PDF where pdf_view_plus is on).
    assert len(edges) == len(_api("package_show", id=DATASET)["resources"])
    overlaps = [(name, right, left) for name, right, left in edges if right > left]
    assert overlaps == []


def test_axe(listing):
    for img in listing.locator(THUMB).all():
        _decoded_size(img)
    # The titles only: CKAN's Explore menus aren't the extension's.
    results = AXE.run(listing, context={"include": [[".resource-item .heading"]]}, options={
        "resultTypes": ["violations"],
    })
    assert results.violations_count == 0, results.generate_report()
