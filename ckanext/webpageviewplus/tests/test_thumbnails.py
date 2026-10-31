"""Thumbnails in the resource list (webpage_view_plus_thumbnails)."""
import io
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
