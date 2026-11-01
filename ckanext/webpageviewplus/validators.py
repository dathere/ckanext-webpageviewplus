from __future__ import annotations

from typing import Any, Optional

import ckan.authz as authz
import ckan.model as model
import ckan.plugins.toolkit as toolkit
from ckan.lib.navl.dictization_functions import missing
from ckan.types import Context, FlattenDataDict, FlattenErrorDict, FlattenKey

from ckanext.webpageviewplus import content, frame, settings, thumbnails

ADVANCED_FIELDS = ("sandbox", "allow", "referrer_policy")


def http_url_validator(value: Any, context: Context) -> Any:
    if value and not frame.is_http_url(value):
        raise toolkit.Invalid(
            toolkit._("Web Page url must be an http:// or https:// URL")
        )
    return value


def preset_validator(value: Any, context: Context) -> Any:
    if value is missing or not value:
        return settings.default_preset()
    if value not in settings.allowed_presets():
        raise toolkit.Invalid(toolkit._("Preset is not one of the allowed presets"))
    return value


def height_mode_validator(value: Any, context: Context) -> Any:
    if value and value not in frame.HEIGHT_MODES:
        raise toolkit.Invalid(toolkit._("Unknown height mode"))
    return value


def sandbox_validator(value: Any, context: Context) -> Any:
    if not value:
        return value
    ok, bad = frame.parse_sandbox(value)
    if bad:
        raise toolkit.Invalid(
            toolkit.ungettext(
                "Unknown sandbox token: {tokens}",
                "Unknown sandbox tokens: {tokens}",
                len(bad),
            ).format(tokens=" ".join(bad))
        )
    return " ".join(ok)


def allow_validator(value: Any, context: Context) -> Any:
    if not value:
        return value
    ok, bad = frame.parse_allow(value)
    if bad:
        raise toolkit.Invalid(
            toolkit.ungettext(
                "This feature can't be granted: {features}",
                "These features can't be granted: {features}",
                len(bad),
            ).format(features=" ".join(bad))
        )
    return "; ".join(ok)


def referrer_policy_validator(value: Any, context: Context) -> Any:
    if value and value not in frame.REFERRER_POLICIES:
        raise toolkit.Invalid(toolkit._("Unknown referrer policy"))
    return value


def _get(data: FlattenDataDict, name: str) -> Any:
    value = data.get((name,))
    return None if value is missing or value in ("", None) else value


def _add_error(errors: FlattenErrorDict, name: str, message: str) -> None:
    errors.setdefault((name,), []).append(message)


def can_set_advanced(context: Context) -> bool:
    return settings.editor_can_loosen() or authz.is_sysadmin(context.get("user"))


def _stored_view(data: FlattenDataDict) -> Optional[model.ResourceView]:
    view_id = _get(data, "id")
    return model.ResourceView.get(view_id) if view_id else None


def _resource(data: FlattenDataDict) -> Optional[model.Resource]:
    resource_id = _get(data, "resource_id")
    if not resource_id:
        stored = _stored_view(data)
        resource_id = stored.resource_id if stored else None
    return model.Resource.get(resource_id) if resource_id else None


def view_after_validator(
    key: FlattenKey,
    data: FlattenDataDict,
    errors: FlattenErrorDict,
    context: Context,
) -> None:
    """Checks across fields, run after the per-field validators."""
    # Advanced fields: users who may not set them keep whatever is stored,
    # so re-saving a view a sysadmin tuned doesn't silently strip it.
    if not can_set_advanced(context):
        stored = _stored_view(data)
        stored_config = (stored.config or {}) if stored else {}
        for name in ADVANCED_FIELDS:
            # Their submitted values are discarded, so their errors are too.
            errors[(name,)] = []
            if stored_config.get(name):
                data[(name,)] = stored_config[name]
            else:
                data.pop((name,), None)

    _check_thumbnail(data, errors)

    if any(errors.get((name,)) for name in ("page_url", "preset", "sandbox")):
        return

    # Height value must fit its mode.
    mode = _get(data, "height_mode")
    if mode and mode != "auto":
        value = _get(data, "height_value") or ""
        if not frame.height_value_valid(mode, value):
            _add_error(
                errors,
                "height_value",
                toolkit._("Height value doesn't fit the height mode"),
            )

    resource = _resource(data)
    page_url = _get(data, "page_url")
    if page_url:
        url, is_upload = page_url, False
    elif resource is not None:
        url, is_upload = resource.url or "", resource.url_type == "upload"
    else:
        return

    isolating = settings.isolating_content_origin()
    if is_upload and resource is not None and content.serves(
        content.resource_dict(resource)
    ):
        # Framed from the content origin (see plugin.setup_template_variables).
        url = settings.content_origin() + "/"
    on_ckan_host = frame.frame_on_ckan_host(
        url, settings.ckan_hosts(), is_upload, settings.content_origin()
    )

    chosen = _get(data, "preset") or settings.default_preset()
    if is_upload and frame.preset_for_upload(chosen) != chosen:
        _add_error(
            errors,
            "preset",
            toolkit._(
                "External is for third-party sites: uploaded HTML always gets "
                "a sandbox. Use Data Schematic or Interactive"
            ),
        )
        return

    allowed_hosts = settings.allowed_hosts()
    if page_url and allowed_hosts and not on_ckan_host:
        if frame.url_host(page_url) not in allowed_hosts:
            _add_error(
                errors,
                "page_url",
                toolkit._("This host isn't on the site's list of allowed hosts"),
            )

    preset_name = frame.effective_preset_name(chosen, isolating)
    sandbox = frame.PRESETS[preset_name].sandbox
    override = _get(data, "sandbox")
    if override:
        sandbox = tuple(frame.parse_sandbox(override)[0])
    same_origin = frame.must_isolate(on_ckan_host, isolating)
    if frame.violates_same_origin_rule(sandbox, same_origin):
        _add_error(
            errors,
            "sandbox" if override else "preset",
            toolkit._(
                "This frame can't let scripts keep a real origin: don't "
                "combine allow-scripts with allow-same-origin. Pages on this "
                "site never may; other pages may once uploaded HTML is served "
                "from a separate content origin"
            ),
        )


def _check_thumbnail(
    data: FlattenDataDict, errors: FlattenErrorDict, first_page: bool = False,
) -> None:
    # The thumbnail route answers to whoever may see the view's dataset, so
    # the picked image must be in that dataset too. PDF (Plus) views may
    # pick their PDF's first page instead.
    picked = _get(data, "thumbnail_resource")
    if picked == thumbnails.FIRST_PAGE and first_page:
        return
    if picked:
        resource = _resource(data)
        image = model.Resource.get(picked)
        if (
            resource is None or image is None or image.state != "active"
            or image.package_id != resource.package_id
            or not thumbnails.is_pickable(content.resource_dict(image))
        ):
            _add_error(
                errors,
                "thumbnail_resource",
                toolkit._("Choose an image from this dataset"),
            )


def pdf_height_mode_validator(value: Any, context: Context) -> Any:
    # A PDF viewer scrolls inside, so its height never follows the page.
    if value and value not in ("fixed", "viewport"):
        raise toolkit.Invalid(toolkit._("Unknown height mode"))
    return value


def pdf_after_validator(
    key: FlattenKey,
    data: FlattenDataDict,
    errors: FlattenErrorDict,
    context: Context,
) -> None:
    """pdf_view_plus's checks across fields."""
    _check_thumbnail(data, errors, first_page=True)
    mode = _get(data, "height_mode")
    value = _get(data, "height_value")
    # A blank value is the default (800 px, or 80% of the window), as the
    # form says: its height list has no blank choice, so it always posts one.
    if mode and value and not errors.get(("height_mode",)):
        if not frame.height_value_valid(mode, value):
            _add_error(
                errors,
                "height_value",
                toolkit._("Height value doesn't fit the height mode"),
            )
    pdf_url = _get(data, "pdf_url")
    allowed_hosts = settings.allowed_hosts()
    if (
        pdf_url and allowed_hosts and not errors.get(("pdf_url",))
        and not frame.is_ckan_host(pdf_url, settings.ckan_hosts())
        and frame.url_host(pdf_url) not in allowed_hosts
    ):
        _add_error(
            errors,
            "pdf_url",
            toolkit._("This host isn't on the site's list of allowed hosts"),
        )
