"""Thumbnails in the resource list (webpage_view_plus_thumbnails)."""
import io
import re
import sys

import pytest
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
        assert '<input type="hidden" name="no_auto_thumbnail" value="true">' in body


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
        import ckan.model as model

        dataset = factories.Dataset()
        page = factories.Resource(package_id=dataset["id"], url=PAGE, format="HTML")
        view = _view(page["id"])
        row = thumbnails.stored(view["id"])
        # Changed without going through the actions, as a direct DB edit or a
        # package_update would.
        resource = model.Resource.get(page["id"])
        resource.url = "https://pages.example.com/other.html"
        model.Session.commit()
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
