"""Against a real Gotenberg: skipped unless WVP_GOTENBERG_URL names one
running with CHROMIUM_DENY_PRIVATE_IPS=true (and, for the refusal test,
WVP_GOTENBERG_UNSAFE_URL one running without it). CI's gotenberg job starts
both; locally, `docker run` them on CKAN's network."""
import os

import pytest

from ckanext.webpageviewplus import screenshots, settings

SAFE = os.environ.get("WVP_GOTENBERG_URL", "")
UNSAFE = os.environ.get("WVP_GOTENBERG_UNSAFE_URL", "")

pytestmark = pytest.mark.skipif(not SAFE, reason="no WVP_GOTENBERG_URL")


def _use(monkeypatch, url):
    monkeypatch.setattr(settings, "gotenberg_url", lambda: url.rstrip("/"))
    monkeypatch.setattr(settings, "gotenberg_auth", lambda: None)


def test_protected_gotenberg_passes(monkeypatch):
    _use(monkeypatch, SAFE)
    assert screenshots.check_gotenberg()


@pytest.mark.skipif(not UNSAFE, reason="no WVP_GOTENBERG_UNSAFE_URL")
def test_unprotected_gotenberg_is_refused(monkeypatch):
    _use(monkeypatch, UNSAFE)
    assert not screenshots.check_gotenberg()


def test_private_iframe_left_out(monkeypatch):
    """The outbound policy covers what a page loads, not only its own URL:
    an iframe of a private address renders as Chrome's blank error frame.
    Gotenberg's HTML route, so no page needs serving; the canary is
    Gotenberg's own health page, which the unprotected one does show."""
    import requests

    page = ('<html><body style="margin:0;background:#fff">'
            '<iframe src="http://127.0.0.1:3000/health" width="1300" height="900"></iframe>'
            '</body></html>')

    def render(base):
        response = requests.post(
            base.rstrip("/") + "/forms/chromium/screenshot/html",
            files={"files": ("index.html", page.encode(), "text/html"),
                   "format": (None, "png"), "width": (None, "1350"), "height": (None, "940"),
                   "clip": (None, "true"), "waitDelay": (None, "1s")},
            timeout=60)
        assert response.status_code == 200
        return response.content

    from PIL import Image
    import io

    def text_pixels(data):
        im = Image.open(io.BytesIO(data)).convert("L")
        return sum(1 for p in im.getdata() if p < 100)

    protected = text_pixels(render(SAFE))
    if UNSAFE:
        # The unprotected one shows the health page's JSON as dark text.
        assert text_pixels(render(UNSAFE)) > protected + 50
    assert protected < 2000
