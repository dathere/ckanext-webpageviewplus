"""The management pages (manage.py): the dataset's Thumbnails tab for its
editors and the admin tab for sysadmins, logged in as seed.py's test users."""
from axe_playwright_python.sync_playwright import Axe

from conftest import BASE_URL, DATASET

AXE = Axe()
DATASET_PAGE = "{}/dataset/{}/thumbnails".format(BASE_URL, DATASET)
ADMIN_PAGE = BASE_URL + "/ckan-admin/thumbnails"


def _no_violations(page):
    results = AXE.run(page, context={"include": [[".webpageviewplus-manage"]]},
                      options={"resultTypes": ["violations"]})
    assert results.violations_count == 0, results.generate_report()


def _row(page, title):
    return page.locator(".webpageviewplus-manage-table tbody tr").filter(
        has=page.get_by_role("link", name=title, exact=True)).first


def test_dataset_page(login):
    page = login("editor")
    page.goto(DATASET_PAGE)
    assert page.get_by_role("link", name="Thumbnails").count() >= 1
    row = _row(page, "Picked thumbnail")
    assert "A picked image" in row.inner_text()
    _no_violations(page)


def test_change_and_back(login):
    page = login("editor")
    page.goto(DATASET_PAGE)
    row = _row(page, "Picked thumbnail")
    with page.expect_navigation():
        row.get_by_role("button", name="Turn automatic thumbnails off").click()
    assert page.get_by_text("Automatic thumbnails are off for this view.").is_visible()
    row = _row(page, "Picked thumbnail")
    with page.expect_navigation():
        row.get_by_role("button", name="Turn automatic thumbnails on").click()
    assert page.get_by_text("Automatic thumbnails are on for this view.").is_visible()


def test_narrow_screen(login):
    page = login("editor")
    page.set_viewport_size({"width": 320, "height": 800})
    page.goto(DATASET_PAGE)
    # The extension's content only: CKAN's own page chrome isn't ours.
    wider = page.evaluate("""() => {
        const width = document.documentElement.clientWidth;
        return [...document.querySelectorAll(".webpageviewplus-manage *")]
            .filter(e => e.getBoundingClientRect().right > width + 0.5)
            .map(e => e.tagName.toLowerCase() + "." + [...e.classList].join(".")
                 + " " + Math.round(e.getBoundingClientRect().right));
    }""")
    assert wider == []
    _no_violations(page)


def test_admin_page(login):
    page = login("sysadmin")
    page.goto("{}?q={}".format(ADMIN_PAGE, DATASET))
    assert page.get_by_role("heading", name="Screenshot service").is_visible()
    # With rows, so the table and the bulk forms are checked too.
    assert page.locator(".webpageviewplus-manage-table tbody tr").count() > 0
    _no_violations(page)
    page.select_option("#field-filter", "opted_out")
    page.fill("#field-q", DATASET)
    with page.expect_navigation():
        page.locator(".webpageviewplus-manage-filter button[type=submit]").click()
    assert "filter=opted_out" in page.url
    _no_violations(page)


def test_admin_page_is_for_sysadmins(login):
    page = login("editor")
    response = page.goto(ADMIN_PAGE)
    assert response.status == 403
