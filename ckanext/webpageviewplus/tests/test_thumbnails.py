"""Thumbnails in the resource list (webpage_view_plus_thumbnails)."""
import io
import re
import sys

import pytest
from bs4 import BeautifulSoup
from PIL import Image
from werkzeug.datastructures import FileStorage

from ckan.exceptions import CkanConfigurationException
from ckan.plugins.toolkit import ValidationError, url_for
from ckan.tests import factories, helpers

from ckanext.webpageviewplus import plugin, thumbnails

YOUTUBE = "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
DATAWRAPPER = "https://datawrapper.dwcdn.net/AbC12/1/"
PAGE = "https://pages.example.com/report.html"


def _png(size=(640, 480), mode="RGBA", color=(200, 30, 30, 128)):
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, "PNG")
    return out.getvalue()


def _image(dataset, body=None, filename="chart.png", fmt="PNG"):
    return factories.Resource(
        package_id=dataset["id"],
        url=filename,
        url_type="upload",
        format=fmt,
        upload=FileStorage(io.BytesIO(body or _png()), filename=filename),
    )


def _view(resource_id, **fields):
    return helpers.call_action(
        "resource_view_create",
        context={"user": factories.Sysadmin()["name"], "ignore_auth": True},
        resource_id=resource_id,
        view_type="webpage_view_plus",
        title="Report",
        **fields,
    )


def _update(view, **fields):
    data = dict(view, **fields)
    return helpers.call_action(
        "resource_view_update",
        context={"user": factories.Sysadmin()["name"], "ignore_auth": True},
        **data,
    )


def _page(app, dataset, **kw):
    return app.get(url_for("dataset.read", id=dataset["name"]), **kw).body


class TestEncode:
    def test_small_jpeg_on_white(self):
        out = thumbnails.encode(_png())
        with Image.open(io.BytesIO(out)) as im:
            assert im.format == "JPEG"
            assert im.size == (240, 180)  # fits the box, aspect kept
            assert im.getpixel((10, 10))[1] > 100  # half-transparent red on white

    def test_metadata_dropped(self):
        source = io.BytesIO()
        exif = Image.Exif()
        exif[0x010E] = "secret description"
        Image.new("RGB", (100, 100), "blue").save(source, "JPEG", exif=exif)
        out = thumbnails.encode(source.getvalue())
        assert b"secret description" not in out
        with Image.open(io.BytesIO(out)) as im:
            assert not im.getexif()

    def test_first_frame_of_animation(self):
        source = io.BytesIO()
        frames = [Image.new("RGB", (50, 50), c) for c in ("red", "blue")]
        frames[0].save(source, "GIF", save_all=True, append_images=frames[1:])
        with Image.open(io.BytesIO(thumbnails.encode(source.getvalue()))) as im:
            r, g, b = im.getpixel((5, 5))
            assert r > 200 and b < 60

    @pytest.mark.parametrize("data", [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"not an image",
        b"",
        _png()[:40],  # truncated
    ])
    def test_refused(self, data):
        assert thumbnails.encode(data) is None

    def test_placeholder_pixel_refused(self):
        assert thumbnails.encode(_png(size=(1, 1))) is None
        assert thumbnails.encode(_png(size=(400, 10))) is None
        assert thumbnails.encode(_png(size=(16, 16))) is not None

    def test_too_many_pixels(self, monkeypatch):
        monkeypatch.setattr(thumbnails, "MAX_PIXELS", 100)
        assert thumbnails.encode(_png((20, 20))) is None

    def test_other_formats_refused(self):
        out = io.BytesIO()
        Image.new("RGB", (20, 20)).save(out, "BMP")
        assert thumbnails.encode(out.getvalue()) is None


def test_replace_format_label_keeps_the_rest():
    markup = ('<a class="heading" href="/r" title="T">Report<span class="format-label" '
              'property="dc:format" data-format="html">HTML</span>\n</a>')
    out = thumbnails.replace_format_label(markup, "<b>X</b>")
    assert out == '<a class="heading" href="/r" title="T">Report<b>X</b>\n</a>'
    # A theme without CKAN's label keeps its own markup.
    assert thumbnails.replace_format_label("<a>Report</a>", "<b>X</b>") == "<a>Report</a>"


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus webpage_view_plus_thumbnails")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "counts_table", "local_storage")
class TestThumbnails:
    def test_picked_image_stored_and_shown(self, app):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        image = _image(dataset)
        view = _view(page["id"], thumbnail_resource=image["id"])
        row = thumbnails.stored(view["id"])
        assert row["source"] == "resource:" + image["id"]

        body = _page(app, dataset)
        src = url_for("webpageviewplus_thumbnails.serve", view_id=view["id"],
                      v=row["digest"][:16])
        assert 'src="{}"'.format(src.replace("&", "&amp;")) in body
        assert 'alt="" width="80" height="45" loading="lazy"' in body
        assert "webpageviewplus/thumbnails-css" in body or "thumbnails.css" in body

        response = app.get(src)
        assert response.headers["Content-Type"] == "image/jpeg"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "public" in response.headers["Cache-Control"]
        assert response.data == row["image"]
        etag = response.headers["ETag"]
        again = app.get(src, headers={"If-None-Match": etag})
        assert again.status_code == 304

    def test_picked_image_streamed_from_storage(self, request):
        # A storage that isn't on disk (memory here; S3 and the like on real
        # sites, CKAN 2.12+) is read by streaming.
        request.getfixturevalue("memory_storage")
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        image = _image(dataset)
        view = _view(page["id"], thumbnail_resource=image["id"])
        assert thumbnails.stored(view["id"])["source"] == "resource:" + image["id"]

    def test_image_from_another_dataset_refused(self):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        elsewhere = _image(factories.Dataset())
        with pytest.raises(ValidationError) as e:
            _view(page["id"], thumbnail_resource=elsewhere["id"])
        assert "thumbnail_resource" in e.value.error_dict

    def test_non_image_refused(self):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        linked = factories.Resource(
            package_id=dataset["id"], url="https://img.example.com/a.png", format="PNG")
        for pick in (page["id"], linked["id"], "no-such-resource"):
            with pytest.raises(ValidationError):
                _view(page["id"], thumbnail_resource=pick)

    def test_undecodable_upload_stores_nothing(self):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        image = _image(dataset, body=b"<svg/>", filename="fake.png")
        view = _view(page["id"], thumbnail_resource=image["id"])
        assert thumbnails.stored(view["id"]) is None

    def test_clearing_the_pick_drops_the_copy(self, app):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        image = _image(dataset)
        view = _view(page["id"], thumbnail_resource=image["id"])
        _update(view, thumbnail_resource="")
        assert thumbnails.stored(view["id"]) is None
        app.get(url_for("webpageviewplus_thumbnails.serve", view_id=view["id"]),
                status=404)

    def test_deleting_the_image_drops_the_copy(self, app):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        image = _image(dataset)
        view = _view(page["id"], thumbnail_resource=image["id"])
        helpers.call_action("resource_delete", id=image["id"])
        assert thumbnails.stored(view["id"]) is None
        assert "webpageviewplus-thumbnail" not in _page(app, dataset)
        app.get(url_for("webpageviewplus_thumbnails.serve", view_id=view["id"]),
                status=404)

    def test_image_dropped_by_package_update_stops_showing(self, app):
        # package_update doesn't go through resource_delete: rendering and
        # the route check the image is still there.
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        image = _image(dataset)
        view = _view(page["id"], thumbnail_resource=image["id"])
        pkg = helpers.call_action("package_show", id=dataset["id"])
        pkg["resources"] = [r for r in pkg["resources"] if r["id"] != image["id"]]
        helpers.call_action("package_update", **pkg)
        assert "webpageviewplus-thumbnail" not in _page(app, dataset)
        app.get(url_for("webpageviewplus_thumbnails.serve", view_id=view["id"]),
                status=404)

    def test_replacing_the_image_remakes_the_copy(self):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        image = _image(dataset)
        view = _view(page["id"], thumbnail_resource=image["id"])
        before = thumbnails.stored(view["id"])["digest"]
        helpers.call_action(
            "resource_update", id=image["id"], package_id=dataset["id"], url="chart.png",
            url_type="upload", format="PNG",
            upload=FileStorage(io.BytesIO(_png(color=(0, 0, 255, 255))), filename="chart.png"))
        assert thumbnails.stored(view["id"])["digest"] != before

    def test_private_dataset(self, app):
        org = factories.Organization()
        dataset = factories.Dataset(owner_org=org["id"], private=True)
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        view = _view(page["id"], thumbnail_resource=_image(dataset)["id"])
        src = url_for("webpageviewplus_thumbnails.serve", view_id=view["id"])
        app.get(src, status=404)
        sysadmin = factories.SysadminWithToken()
        response = app.get(src, headers={"Authorization": sysadmin["token"]})
        assert response.status_code == 200
        assert "private" in response.headers["Cache-Control"]
        assert "public" not in response.headers["Cache-Control"]

    def test_unknown_view_404(self, app):
        app.get(url_for("webpageviewplus_thumbnails.serve", view_id="nope"), status=404)

    def test_service_logo_and_name(self, app):
        dataset = factories.Dataset()
        video = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        _view(video["id"], page_url=YOUTUBE, preset="external",
              height_mode="aspect", height_value="16/9")
        body = _page(app, dataset)
        assert "/webpageviewplus/logos/youtube.svg" in body
        assert '<span class="webpageviewplus-format">YouTube</span>' in body
        assert '<span class="webpageviewplus-thumbnail"><img' in body  # no copy frame
        # The service's name isn't the dataset's format.
        assert 'property="dc:format">YouTube' not in body

    def test_service_without_logo_keeps_icon(self, app):
        dataset = factories.Dataset()
        chart = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        _view(chart["id"], page_url=DATAWRAPPER, preset="interactive")
        body = _page(app, dataset)
        assert '<span class="format-label" data-format="html">Datawrapper</span>' in body
        assert "webpageviewplus-thumbnail" not in body

    def test_second_view_drops_logo_and_name(self, app):
        dataset = factories.Dataset()
        video = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        _view(video["id"], page_url=YOUTUBE, preset="external")
        _view(video["id"], preset="strict")
        body = _page(app, dataset)
        assert "logos/youtube.svg" not in body
        assert ">YouTube<" not in body

    def test_picked_image_beats_logo(self, app):
        dataset = factories.Dataset()
        video = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        view = _view(video["id"], page_url=YOUTUBE, preset="external",
                     thumbnail_resource=_image(dataset)["id"])
        body = _page(app, dataset)
        assert "/webpageviewplus/thumbnail/" + view["id"] in body
        assert "logos/youtube.svg" not in body
        assert '<span class="webpageviewplus-format">YouTube</span>' in body

    def test_plain_page_unchanged(self, app):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        _view(page["id"])
        body = _page(app, dataset)
        assert "webpageviewplus-" not in body.split('class="resource-list"')[1]
        assert 'property="dc:format" data-format="html">HTML</span>' in body

    def test_one_query_per_dataset_page(self, app, monkeypatch):
        dataset = factories.Dataset()
        for _ in range(3):
            page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
            _view(page["id"], page_url=YOUTUBE, preset="external")
        calls = []
        real = thumbnails._views
        monkeypatch.setattr(thumbnails, "_views", lambda pid: calls.append(pid) or real(pid))
        assert _page(app, dataset).count("logos/youtube.svg") == 3
        assert calls == [dataset["id"]]

    def test_form_offers_the_dataset_images(self, app):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        image = _image(dataset)
        sysadmin = factories.SysadminWithToken()
        body = app.get(
            url_for("dataset_resource.edit_view", id=dataset["name"],
                    resource_id=page["id"], view_type="webpage_view_plus"),
            headers={"Authorization": sysadmin["token"]},
        ).body
        assert 'name="thumbnail_resource"' in body
        assert 'value="{}"'.format(image["id"]) in body

    def test_form_controls_are_out_of_themes_reach(self, app):
        # Themes size every input in .control-medium (checkboxes too), so
        # checkboxes stay out of it, and style.css's scoped rules need
        # every control inside the form's wrapper.
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        sysadmin = factories.SysadminWithToken()
        html = BeautifulSoup(app.get(
            url_for("dataset_resource.edit_view", id=dataset["name"],
                    resource_id=page["id"], view_type="webpage_view_plus"),
            headers={"Authorization": sysadmin["token"]},
        ).body, "html.parser")
        wrapper = html.find(class_="webpageviewplus-form")
        checkboxes = wrapper.find_all("input", type="checkbox")
        assert {c["name"] for c in checkboxes} >= {
            "no_auto_thumbnail", "hide_controls", "consent_gate"}
        assert not [c["name"] for c in checkboxes if c.find_parent(class_="control-medium")]
        assert {"preset", "height_mode", "thumbnail_resource"} <= {
            s["name"] for s in wrapper.find_all("select")}


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
def test_off_without_the_plugin(app):
    dataset = factories.Dataset()
    page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
    _view(page["id"], page_url=YOUTUBE, preset="external")
    assert "logos/youtube.svg" not in _page(app, dataset)
    sysadmin = factories.SysadminWithToken()
    body = app.get(
        url_for("dataset_resource.edit_view", id=dataset["name"],
                resource_id=page["id"], view_type="webpage_view_plus"),
        headers={"Authorization": sysadmin["token"]},
    ).body
    assert 'name="thumbnail_resource"' not in body


class TestConfigure:
    def test_needs_pillow(self, monkeypatch):
        monkeypatch.setattr(plugin.plugins, "plugin_loaded", lambda name: True)
        monkeypatch.setitem(sys.modules, "PIL", None)
        with pytest.raises(CkanConfigurationException, match=r"\[thumbnails\]"):
            plugin.WebpageviewplusThumbnailsPlugin().configure({})

    def test_needs_the_main_plugin(self, monkeypatch):
        monkeypatch.setattr(plugin.plugins, "plugin_loaded", lambda name: False)
        with pytest.raises(CkanConfigurationException, match="webpage_view_plus plugin"):
            plugin.WebpageviewplusThumbnailsPlugin().configure({})


# --- stage 2: fetching (thumbnail_fetch) ------------------------------------

LINKED = "https://img.example.com/chart.png"
YOUTUBE_IMAGE = "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg"
OG = "https://cdn.example.com/og.png"
LOOKER = ("https://lookerstudio.google.com/embed/reporting/"
          "e1efb265-1581-4bfe-961c-e7c3b1795208/page/kIV1C")


def _html(head):
    return "<html><head><title>T</title>{}</head><body></body></html>".format(head)


@pytest.mark.parametrize("html,expected", [
    (_html('<meta property="og:image" content="https://a.example/1.png">'
           '<meta name="twitter:image" content="https://a.example/2.png">'),
     "https://a.example/1.png"),
    (_html('<meta name="twitter:image" content="/2.png">'), "https://pages.example.com/2.png"),
    (_html('<meta name="twitter:image:src" content="3.png">'), "https://pages.example.com/a/3.png"),
    (_html('<meta property="og:image" content="data:image/png;base64,AAAA">'), None),
    (_html('<meta property="og:image" content="javascript:alert(1)">'), None),
    (_html('<meta property="og:image" content="//[bad">'), None),
    # Only the <head> counts: a page's body can carry anyone's markup.
    ('<html><head></head><body><meta property="og:image" content="/x.png"></body></html>',
     None),
    ("<html><head><title>T</title>", None),
])
def test_preview_image_url(html, expected):
    assert thumbnails.preview_image_url(html, "https://pages.example.com/a/b.html") == expected


@pytest.fixture
def dns(monkeypatch):
    from ckanext.webpageviewplus import frameability as fa

    table = {"pages.example.com": ["93.184.216.34"], "cdn.example.com": ["93.184.216.35"],
             "img.example.com": ["93.184.216.36"], "internal.example.com": ["10.0.0.5"],
             "i.ytimg.com": ["93.184.216.37"]}
    monkeypatch.setattr(fa, "_resolve", lambda host, port: table.get(host, []))
    return table


@pytest.fixture
def web(dns):
    """A page naming OG as its preview image, OG and LINKED as PNGs."""
    import responses

    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        # CKAN's own requests (Solr) go through.
        mock.add_passthru(re.compile(r"^(?!https?://[^/]*example\.com[:/])"))
        mock.get(PAGE, body=_html('<meta property="og:image" content="{}">'.format(OG)),
                 content_type="text/html; charset=utf-8")
        mock.get(OG, body=_png(), content_type="image/png")
        mock.get(LINKED, body=_png(color=(0, 0, 200, 255)), content_type="image/png")
        mock.get(YOUTUBE_IMAGE, body=_png(size=(320, 180), color=(0, 200, 0, 255)),
                 content_type="image/jpeg")
        yield mock


def _no_fetching(monkeypatch):
    from ckanext.webpageviewplus import frameability as fa

    monkeypatch.setattr(fa, "_http_get", lambda *a, **kw: pytest.fail("fetched"))


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus webpage_view_plus_thumbnails")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "counts_table", "local_storage")
class TestFetchOff:
    def test_nothing_fetched(self, monkeypatch):
        _no_fetching(monkeypatch)
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        view = _view(page["id"])
        assert thumbnails.stored(view["id"]) is None
        linked = factories.Resource(package_id=dataset["id"], url=LINKED, format="PNG")
        with pytest.raises(ValidationError):
            _view(page["id"], thumbnail_resource=linked["id"])

    def test_opt_out_kept_while_fetching_is_off(self, app):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        view = _view(page["id"], no_auto_thumbnail=True)
        sysadmin = factories.SysadminWithToken()
        body = app.get(
            url_for("dataset_resource.edit_view", id=dataset["name"],
                    resource_id=page["id"], view_id=view["id"]),
            headers={"Authorization": sysadmin["token"]},
        ).body
        # The checkbox shows (and keeps it) whether fetching is on or off.
        assert re.search(r'id="field-no_auto_thumbnail"[^>]*checked', body)


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus webpage_view_plus_thumbnails")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.ckan_config("ckanext.webpageviewplus.thumbnail_fetch", "true")
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "counts_table", "local_storage")
class TestFetch:
    def test_page_image_stored_and_shown(self, app, web):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        view = _view(page["id"])
        row = thumbnails.stored(view["id"])
        assert row["source"] == "page:" + PAGE
        with Image.open(io.BytesIO(row["image"])) as im:
            assert im.format == "JPEG" and im.size == (240, 180)
        src = url_for("webpageviewplus_thumbnails.serve", view_id=view["id"],
                      v=row["digest"][:16])
        assert 'src="{}"'.format(src.replace("&", "&amp;")) in _page(app, dataset)
        assert app.get(src).data == row["image"]

    def test_service_fixed_address(self, app, web):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        view = _view(page["id"], page_url=YOUTUBE, preset="external")
        row = thumbnails.stored(view["id"])
        assert row["source"] == "service:" + YOUTUBE
        with Image.open(io.BytesIO(row["image"])) as im:
            assert im.getpixel((10, 10))[1] > 150  # the green test image
        assert [c.request.url for c in web.calls] == [YOUTUBE_IMAGE]  # no page fetched
        src = url_for("webpageviewplus_thumbnails.serve", view_id=view["id"],
                      v=row["digest"][:16])
        body = _page(app, dataset)
        assert 'src="{}"'.format(src.replace("&", "&amp;")) in body
        assert "logos/youtube.svg" not in body  # beats the logo
        assert '<span class="webpageviewplus-thumbnail webpageviewplus-copy"><img' in body
        assert app.get(src).data == row["image"]

    def test_opting_out_drops_the_copy(self, app, web):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        view = _view(page["id"])
        assert thumbnails.stored(view["id"])
        _update(view, no_auto_thumbnail=True)
        assert thumbnails.stored(view["id"]) is None

    def test_uploads_and_ckan_pages_not_fetched(self, monkeypatch):
        _no_fetching(monkeypatch)
        dataset = factories.Dataset()
        upload = _image(dataset, filename="page.html", fmt="HTML")
        own = factories.Resource(package_id=dataset["id"], url="http://test.ckan.net/about",
                                 format="HTML")
        for resource in (upload, own):
            assert thumbnails.stored(_view(resource["id"])["id"]) is None

    def test_logo_only_previews_skipped(self, monkeypatch):
        _no_fetching(monkeypatch)
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        view = _view(page["id"], page_url=LOOKER, preset="external")
        assert thumbnails.stored(view["id"]) is None
        assert thumbnails.source_for({"page_url": LOOKER}, PAGE, "") is None

    def test_image_at_a_private_address_refused(self, web):
        web.replace("GET", PAGE, body=_html(
            '<meta property="og:image" content="https://internal.example.com/x.png">'),
            content_type="text/html")
        web.get("https://internal.example.com/x.png", body=_png(), content_type="image/png")
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        assert thumbnails.stored(_view(page["id"])["id"]) is None
        assert [c.request.url for c in web.calls] == [PAGE]

    def test_redirect_to_a_private_address_refused(self, web):
        web.replace("GET", OG, status=302,
                    headers={"Location": "https://internal.example.com/x.png"})
        web.get("https://internal.example.com/x.png", body=_png(), content_type="image/png")
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        assert thumbnails.stored(_view(page["id"])["id"]) is None
        assert "https://internal.example.com/x.png" not in [c.request.url for c in web.calls]

    def test_large_image_refused_while_reading(self, web, monkeypatch):
        monkeypatch.setattr(thumbnails, "MAX_IMAGE_BYTES", 1000)
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        assert len(_png()) > 1000
        assert thumbnails.stored(_view(page["id"])["id"]) is None

    def test_svg_refused(self, web):
        web.replace("GET", OG, body=b'<svg xmlns="http://www.w3.org/2000/svg"/>',
                    content_type="image/svg+xml")
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        assert thumbnails.stored(_view(page["id"])["id"]) is None

    def test_changed_page_url_hides_the_copy(self, app, web):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        view = _view(page["id"])
        row = thumbnails.stored(view["id"])
        helpers.call_action("resource_patch", id=page["id"],
                            url="https://pages.example.com/other.html")
        src = url_for("webpageviewplus_thumbnails.serve", view_id=view["id"],
                      v=row["digest"][:16])
        assert app.get(src, status=404)
        assert 'src="{}"'.format(src.replace("&", "&amp;")) not in _page(app, dataset)

    def test_linked_image_picked(self, app, web, monkeypatch):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        linked = factories.Resource(package_id=dataset["id"], url=LINKED, format="PNG")
        view = _view(page["id"], thumbnail_resource=linked["id"])
        row = thumbnails.stored(view["id"])
        assert row["source"] == "resource:" + linked["id"]
        with Image.open(io.BytesIO(row["image"])) as im:
            assert im.getpixel((10, 10))[2] > 150  # the blue image, not the page's
        src = url_for("webpageviewplus_thumbnails.serve", view_id=view["id"],
                      v=row["digest"][:16])
        assert app.get(src).status_code == 200
        # Fetching turned off: copies fetched from links stop showing.
        from ckanext.webpageviewplus import settings
        monkeypatch.setattr(settings, "thumbnail_fetch", lambda: False)
        assert app.get(src, status=404)

    def test_form_offers_links_and_the_opt_out(self, app):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        linked = factories.Resource(package_id=dataset["id"], url=LINKED, format="PNG")
        sysadmin = factories.SysadminWithToken()
        body = app.get(
            url_for("dataset_resource.edit_view", id=dataset["name"],
                    resource_id=page["id"], view_type="webpage_view_plus"),
            headers={"Authorization": sysadmin["token"]},
        ).body
        assert 'value="{}"'.format(linked["id"]) in body
        assert 'name="no_auto_thumbnail"' in body

    def test_check_views_remakes_and_keeps(self, web):
        from click.testing import CliRunner

        from ckanext.webpageviewplus import cli as wvp_cli

        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        image = _image(dataset)
        picked = _view(page["id"], thumbnail_resource=image["id"])
        fetched = _view(factories.Resource(
            package_id=dataset["id"], url=PAGE, format="HTML")["id"])
        old = thumbnails.stored(picked["id"])["digest"]
        kept = thumbnails.stored(fetched["id"])["digest"]
        # The image is replaced without the resource actions (package_update).
        thumbnails.store(picked["id"], "resource:" + image["id"], b"stale")
        web.replace("GET", OG, status=503)
        # The command itself, in this test's config (the ckan CLI reloads
        # test.ini, without local_storage).
        result = CliRunner().invoke(wvp_cli.check_views, ["--dataset", dataset["name"]])
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert thumbnails.stored(picked["id"])["digest"] == old
        assert thumbnails.stored(fetched["id"])["digest"] == kept  # the 503 keeps it


# --- stage 4: service_format -------------------------------------------------


def _format(resource):
    return helpers.call_action("resource_show", id=resource["id"])["format"]


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus webpage_view_plus_thumbnails")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.ckan_config("ckanext.webpageviewplus.service_format", "true")
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "counts_table", "local_storage")
class TestServiceFormat:
    @pytest.mark.parametrize("fmt", ["", "HTML", "html"])
    def test_names_the_service(self, fmt):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format=fmt)
        view = _view(page["id"], page_url=YOUTUBE, preset="external")
        assert _format(page) == "YouTube"
        # The view survives the resource update.
        assert helpers.call_action("resource_view_show", id=view["id"])["page_url"] == YOUTUBE

    def test_from_the_resource_url(self):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=DATAWRAPPER, format="")
        _view(page["id"])
        assert _format(page) == "Datawrapper"

    def test_other_formats_kept(self):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="CSV")
        _view(page["id"], page_url=YOUTUBE, preset="external")
        assert _format(page) == "CSV"

    def test_only_with_one_view(self):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        _view(page["id"])
        _view(page["id"], page_url=YOUTUBE, preset="external")
        assert _format(page) == "HTML"

    def test_not_a_service(self):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        _view(page["id"])
        assert _format(page) == "HTML"


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus webpage_view_plus_thumbnails")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "counts_table", "local_storage")
def test_service_format_off_by_default():
    dataset = factories.Dataset()
    page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
    _view(page["id"], page_url=YOUTUBE, preset="external")
    assert _format(page) == "HTML"


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus webpage_view_plus_thumbnails")
@pytest.mark.ckan_config("ckan.views.default_views", "webpage_view_plus")
@pytest.mark.ckan_config("ckanext.webpageviewplus.service_format", "true")
@pytest.mark.ckan_config("ckanext.webpageviewplus.content_origin", "http://127.0.0.1")
@pytest.mark.ckan_config("ckanext.webpageviewplus.signing_secret", "k" * 40)
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "counts_table", "local_storage")
def test_default_view_names_the_service_once():
    # The default view's save patches the format, and resource_update then
    # asks for default views again, inside resource_create's own request.
    dataset = factories.Dataset()
    page = factories.Resource(package_id=dataset["id"],
                              url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", format="")
    [view] = helpers.call_action("resource_view_list", id=page["id"])
    assert (view["page_url"], view["preset"]) == (YOUTUBE, "external")
    assert _format(page) == "YouTube"


# --- PDF (Plus) views and Office files' own previews ------------------------

def _office_file(preview_name="Thumbnails/thumbnail.png", preview=None, mimetype=True):
    """A minimal Office zip: an OpenDocument-style mimetype entry and,
    unless preview_name is None, a preview picture under that name."""
    out = io.BytesIO()
    import zipfile
    with zipfile.ZipFile(out, "w") as z:
        if mimetype:
            z.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        z.writestr("content.xml", "<office:document-content/>")
        if preview_name:
            z.writestr(preview_name, preview if preview is not None else _png(color=(0, 120, 0, 255)))
    return out.getvalue()


SITE = "http://test.ckan.net"


def _office_upload(dataset, body, filename):
    return factories.Resource(
        package_id=dataset["id"], url=filename, url_type="upload",
        format=filename.rsplit(".", 1)[1].upper(),
        upload=FileStorage(io.BytesIO(body), filename=filename))


def _embed(file_url):
    from urllib.parse import quote_plus
    return "https://view.officeapps.live.com/op/embed.aspx?src=" + quote_plus(file_url)


class TestOfficePreview:
    def test_opendocument_thumbnail(self):
        out = thumbnails.office_preview(_office_file())
        with Image.open(io.BytesIO(out)) as im:
            assert im.format == "JPEG"

    def test_ooxml_thumbnail(self):
        source = io.BytesIO()
        Image.new("RGB", (400, 300), "navy").save(source, "JPEG")
        assert thumbnails.office_preview(
            _office_file("docProps/thumbnail.jpeg", source.getvalue(), mimetype=False))

    def test_none_without_a_preview(self):
        assert thumbnails.office_preview(_office_file(preview_name=None)) is None

    def test_oversized_preview_refused(self):
        big = _office_file(preview=b"\0" * (thumbnails.MAX_IMAGE_BYTES + 1))
        assert thumbnails.office_preview(big) is None

    def test_not_a_zip(self):
        assert thumbnails.office_preview(b"\xd0\xcf\x11\xe0 an old binary .doc") is None

    def test_office_file(self):
        assert thumbnails.office_file(_embed("https://x.org/a.odt")) == "https://x.org/a.odt"
        assert thumbnails.office_file(_embed("https://x.org/a.docx")) == "https://x.org/a.docx"
        # The older binary formats keep their preview elsewhere.
        assert thumbnails.office_file(_embed("https://x.org/a.doc")) is None
        assert thumbnails.office_file(PAGE) is None


@pytest.mark.ckan_config("ckan.plugins",
                         "webpage_view_plus webpage_view_plus_thumbnails pdf_view_plus")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "counts_table", "local_storage")
# Microsoft's viewer can't fetch from a non-default port, so an upload on
# ckan-dev's CKAN_SITE_URL (localhost:5000, as in CI, which overrides any
# ckan.site_url) is never offered: these uploads use a site alias instead.
@pytest.mark.ckan_config("ckanext.webpageviewplus.site_aliases", "test.ckan.net")
class TestOfficeAndPdfViews:
    def test_uploaded_office_file_without_fetching(self, app, monkeypatch, ckan_config):
        # CKAN's own upload is read from storage: no fetch, so no setting needed.
        _no_fetching(monkeypatch)
        dataset = factories.Dataset()
        doc = _office_upload(dataset, _office_file(), "minutes.odt")
        download = "{}/dataset/{}/resource/{}/download/minutes.odt".format(
            SITE, dataset["id"], doc["id"])
        view = _view(doc["id"], page_url=_embed(download), preset="interactive")
        row = thumbnails.stored(view["id"])
        assert row["source"] == "office:" + download
        body = _page(app, dataset)
        assert url_for("webpageviewplus_thumbnails.serve", view_id=view["id"],
                       v=row["digest"][:16]).replace("&", "&amp;") in body
        assert "ODT" in body  # the file keeps its own format label

    def test_office_file_elsewhere_needs_fetching(self, monkeypatch):
        _no_fetching(monkeypatch)
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url="https://files.example.com/a.odt",
                                  format="ODT")
        view = _view(page["id"], page_url=_embed("https://files.example.com/a.odt"),
                     preset="interactive")
        assert thumbnails.stored(view["id"]) is None

    @pytest.mark.ckan_config("ckanext.webpageviewplus.thumbnail_fetch", "true")
    def test_office_file_elsewhere_fetched(self, monkeypatch):
        from ckanext.webpageviewplus import frameability as fa
        calls = []

        def fetch_body(url, deadline, max_bytes, user_agent, truncate=False):
            calls.append(url)
            return fa.Body(url, "application/vnd.oasis.opendocument.text", None, _office_file())

        monkeypatch.setattr(fa, "fetch_body", fetch_body)
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url="https://files.example.com/a.odt",
                                  format="ODT")
        view = _view(page["id"], page_url=_embed("https://files.example.com/a.odt"),
                     preset="interactive")
        assert thumbnails.stored(view["id"])["source"] == "office:https://files.example.com/a.odt"
        assert calls == ["https://files.example.com/a.odt"]

    def test_opt_out_respected(self, monkeypatch, ckan_config):
        dataset = factories.Dataset()
        doc = _office_upload(dataset, _office_file(), "minutes.odt")
        download = "{}/dataset/{}/resource/{}/download/minutes.odt".format(
            SITE, dataset["id"], doc["id"])
        view = _view(doc["id"], page_url=_embed(download), preset="interactive",
                     no_auto_thumbnail=True)
        assert thumbnails.stored(view["id"]) is None

    def test_pdf_view_picked_image(self, app):
        dataset = factories.Dataset()
        report = factories.Resource(package_id=dataset["id"], url="https://files.example.com/r.pdf",
                                    format="PDF")
        image = _image(dataset)
        view = helpers.call_action(
            "resource_view_create",
            context={"user": factories.Sysadmin()["name"], "ignore_auth": True},
            resource_id=report["id"], view_type="pdf_view_plus", title="Report",
            thumbnail_resource=image["id"])
        row = thumbnails.stored(view["id"])
        assert row["source"] == "resource:" + image["id"]
        assert url_for("webpageviewplus_thumbnails.serve", view_id=view["id"],
                       v=row["digest"][:16]).replace("&", "&amp;") in _page(app, dataset)
        assert app.get(url_for("webpageviewplus_thumbnails.serve", view_id=view["id"])).status_code == 200

    def test_one_run_never_shares_an_upload_across_datasets(self, ckan_config):
        # check-views fetches each file once per run, but an upload is read
        # only for a view in its own dataset: a private dataset's preview
        # mustn't reach a public view pointing at the same file.
        private = factories.Dataset(private=True, owner_org=factories.Organization()["id"])
        doc = _office_upload(private, _office_file(), "minutes.odt")
        download = "{}/dataset/{}/resource/{}/download/minutes.odt".format(
            SITE, private["id"], doc["id"])
        own = _view(doc["id"], page_url=_embed(download), preset="interactive")
        public = factories.Dataset()
        page = factories.Resource(package_id=public["id"], url=PAGE, format="HTML")
        other = _view(page["id"], page_url=_embed(download), preset="interactive")
        assert thumbnails.stored(other["id"]) is None
        memo = {}
        for view in (own, other):
            thumbnails.after_save(view, memo=memo)
        assert thumbnails.stored(own["id"])
        assert thumbnails.stored(other["id"]) is None

    def test_deleted_office_upload_drops_its_preview(self, app, ckan_config):
        dataset = factories.Dataset()
        doc = _office_upload(dataset, _office_file(), "minutes.odt")
        download = "{}/dataset/{}/resource/{}/download/minutes.odt".format(
            SITE, dataset["id"], doc["id"])
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        view = _view(page["id"], page_url=_embed(download), preset="interactive")
        assert thumbnails.stored(view["id"])
        serve = url_for("webpageviewplus_thumbnails.serve", view_id=view["id"])
        # Deleted without the resource actions: the copy stops showing at once.
        from ckan import model
        model.Resource.get(doc["id"]).state = "deleted"
        model.Session.commit()
        # (The dataset page lists resources from the search index on CKAN
        # 2.12, which this skips, so only the route is checked here.)
        assert app.get(serve).status_code == 404
        # Deleted through them: the copy goes.
        model.Resource.get(doc["id"]).state = "active"
        model.Session.commit()
        helpers.call_action("resource_delete", id=doc["id"])
        assert thumbnails.stored(view["id"]) is None
        assert "webpageviewplus/thumbnail/" not in _page(app, dataset)

    def test_check_views_remakes_pdf_thumbnails(self):
        from click.testing import CliRunner

        from ckanext.webpageviewplus import cli as wvp_cli

        dataset = factories.Dataset()
        report = factories.Resource(package_id=dataset["id"], url="https://files.example.com/r.pdf",
                                    format="PDF")
        image = _image(dataset)
        view = helpers.call_action(
            "resource_view_create",
            context={"user": factories.Sysadmin()["name"], "ignore_auth": True},
            resource_id=report["id"], view_type="pdf_view_plus", title="Report",
            thumbnail_resource=image["id"])
        good = thumbnails.stored(view["id"])["digest"]
        # The image is replaced without the resource actions (package_update).
        thumbnails.store(view["id"], "resource:" + image["id"], b"stale")
        result = CliRunner().invoke(wvp_cli.check_views, ["--dataset", dataset["name"]])
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert thumbnails.stored(view["id"])["digest"] == good

    def test_pdf_view_image_from_another_dataset_refused(self):
        dataset = factories.Dataset()
        report = factories.Resource(package_id=dataset["id"], url="https://files.example.com/r.pdf",
                                    format="PDF")
        other = _image(factories.Dataset())
        with pytest.raises(ValidationError):
            helpers.call_action(
                "resource_view_create",
                context={"user": factories.Sysadmin()["name"], "ignore_auth": True},
                resource_id=report["id"], view_type="pdf_view_plus", title="Report",
                thumbnail_resource=other["id"])


# --- a PDF's first page, drawn by PDF.js in the editor's browser -------------

def _png_b64(**kw):
    import base64
    return base64.b64encode(_png(**kw)).decode()


def _pdf_view(resource_id, **fields):
    return helpers.call_action(
        "resource_view_create",
        context={"user": factories.Sysadmin()["name"], "ignore_auth": True},
        resource_id=resource_id, view_type="pdf_view_plus", title="Report", **fields)


@pytest.mark.ckan_config("ckan.plugins",
                         "webpage_view_plus webpage_view_plus_thumbnails pdf_view_plus")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "counts_table", "local_storage")
class TestPdfFirstPage:
    def _report(self, dataset):
        return factories.Resource(package_id=dataset["id"], url="https://files.example.com/r.pdf",
                                  format="PDF")

    def test_stored_then_counts_once_chosen(self, app):
        dataset = factories.Dataset()
        report = self._report(dataset)
        view = _pdf_view(report["id"])
        sysadmin = factories.Sysadmin()
        result = helpers.call_action(
            "webpageviewplus_pdf_thumbnail", context={"user": sysadmin["name"]},
            id=view["id"], image=_png_b64())
        assert result["source"].startswith("pdf-page:https://files.example.com/r.pdf#")
        # Not chosen yet: the copy is kept but not shown.
        assert "webpageviewplus/thumbnail/" not in _page(app, dataset)
        view = _update(view, thumbnail_resource=thumbnails.FIRST_PAGE)
        row = thumbnails.stored(view["id"])
        assert row["source"] == result["source"]  # the save kept it
        assert url_for("webpageviewplus_thumbnails.serve", view_id=view["id"],
                       v=row["digest"][:16]).replace("&", "&amp;") in _page(app, dataset)

    def test_cancelled_edit_keeps_the_picked_image(self, app):
        # Choosing the first page draws it at once; until the view is saved,
        # the image it uses stays its thumbnail.
        dataset = factories.Dataset()
        image = _image(dataset)
        view = _pdf_view(self._report(dataset)["id"], thumbnail_resource=image["id"])
        before = thumbnails.stored(view["id"])
        helpers.call_action("webpageviewplus_pdf_thumbnail",
                            context={"user": factories.Sysadmin()["name"]},
                            id=view["id"], image=_png_b64(color=(0, 0, 255, 255)))
        assert thumbnails.stored(view["id"]) == before
        assert app.get(url_for("webpageviewplus_thumbnails.serve",
                               view_id=view["id"])).status_code == 200
        # Saved with the first page: the drawing takes over, the draft goes.
        _update(view, thumbnail_resource=thumbnails.FIRST_PAGE)
        assert thumbnails.stored(view["id"])["source"].startswith("pdf-page:")
        assert thumbnails._draft(view["id"]) is None

    def test_redrawn_once_chosen(self):
        # A view already using its first page takes a new drawing at once: a
        # linked PDF changed under the same URL, or a save that landed
        # before the drawing did.
        view = _pdf_view(self._report(factories.Dataset())["id"],
                         thumbnail_resource=thumbnails.FIRST_PAGE)
        assert thumbnails.stored(view["id"]) is None
        for color in ((255, 0, 0, 255), (0, 255, 0, 255)):
            helpers.call_action("webpageviewplus_pdf_thumbnail",
                                context={"user": factories.Sysadmin()["name"]},
                                id=view["id"], image=_png_b64(color=color))
            digest = thumbnails.stored(view["id"])["digest"]
            _update(view, title="Report")  # the save keeps the newest drawing
            assert thumbnails.stored(view["id"])["digest"] == digest
        assert thumbnails._draft(view["id"]) is None

    def test_changed_file_stops_counting(self, app):
        dataset = factories.Dataset()
        report = self._report(dataset)
        view = _pdf_view(report["id"])
        helpers.call_action("webpageviewplus_pdf_thumbnail",
                            context={"user": factories.Sysadmin()["name"]},
                            id=view["id"], image=_png_b64())
        _update(view, thumbnail_resource=thumbnails.FIRST_PAGE)
        assert "webpageviewplus/thumbnail/" in _page(app, dataset)
        import datetime
        helpers.call_action("resource_patch", id=report["id"],
                            last_modified=datetime.datetime(2030, 1, 1).isoformat())
        assert "webpageviewplus/thumbnail/" not in _page(app, dataset)

    def test_upload_source_same_from_model_and_dict(self):
        # The database keeps an upload's url bare; the resource list sees
        # its download URL. Both must name the same copy.
        bare = thumbnails.first_page_source({}, "report.pdf", "upload", "2026-11-01T10:00:00")
        full = thumbnails.first_page_source(
            {}, "http://test.ckan.net/dataset/x/resource/y/download/report.pdf", "upload",
            "2026-11-01T10:00:00")
        assert bare == full

    def test_not_an_image_refused(self):
        view = _pdf_view(self._report(factories.Dataset())["id"])
        import base64
        with pytest.raises(ValidationError):
            helpers.call_action("webpageviewplus_pdf_thumbnail",
                                context={"user": factories.Sysadmin()["name"]},
                                id=view["id"], image=base64.b64encode(b"<svg/>").decode())
        with pytest.raises(ValidationError):
            helpers.call_action("webpageviewplus_pdf_thumbnail",
                                context={"user": factories.Sysadmin()["name"]},
                                id=view["id"], image="not base64!")

    def test_only_editors(self):
        from ckan.plugins.toolkit import NotAuthorized
        org = factories.Organization()
        view = _pdf_view(self._report(factories.Dataset(owner_org=org["id"]))["id"])
        stranger = factories.User()
        for user in ("", stranger["name"]):
            with pytest.raises(NotAuthorized):
                helpers.call_action("webpageviewplus_pdf_thumbnail",
                                    context={"user": user, "ignore_auth": False},
                                    id=view["id"], image=_png_b64())

    def test_only_pdf_views(self):
        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        view = _view(page["id"])
        from ckan.plugins.toolkit import ObjectNotFound
        with pytest.raises(ObjectNotFound):
            helpers.call_action("webpageviewplus_pdf_thumbnail",
                                context={"user": factories.Sysadmin()["name"]},
                                id=view["id"], image=_png_b64())
        # And a Website (Plus) view can't pick "the first page".
        with pytest.raises(ValidationError):
            _view(page["id"], thumbnail_resource=thumbnails.FIRST_PAGE)


@pytest.mark.ckan_config("ckan.plugins",
                         "webpage_view_plus webpage_view_plus_thumbnails pdf_view_plus")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.ckan_config("ckanext.webpageviewplus.content_origin", "http://127.0.0.1")
@pytest.mark.ckan_config("ckanext.webpageviewplus.signing_secret", "k" * 40)
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "counts_table", "local_storage")
def test_first_page_check_only_in_the_form(app, monkeypatch):
    # Rendering a PDF view for a visitor mustn't query for the form's copy.
    monkeypatch.setattr(thumbnails, "first_page_ready",
                        lambda *a: pytest.fail("checked outside the form"))
    dataset = factories.Dataset()
    report = factories.Resource(package_id=dataset["id"], url="https://files.example.com/r.pdf",
                                format="PDF")
    view = _pdf_view(report["id"])
    body = app.get(url_for("dataset_resource.read", id=dataset["name"],
                           resource_id=report["id"], view_id=view["id"])).body
    assert "webpageviewplus/pdfjs/" in body


def test_linked_pdf_never_ready():
    view = {"id": "v", "pdf_url": "https://files.example.com/r.pdf"}
    assert thumbnails.first_page_ready(view, {"url_type": "upload"}) is False
    assert thumbnails.first_page_ready({"id": "v"}, {"url": "https://x.org/a.pdf"}) is False
