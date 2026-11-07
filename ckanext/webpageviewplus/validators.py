from __future__ import annotations

from typing import Any, Optional

import ckan.authz as authz
import ckan.model as model
import ckan.plugins.toolkit as toolkit
from ckan.lib.navl.dictization_functions import missing
from ckan.types import Context, FlattenDataDict, FlattenErrorDict, FlattenKey

from ckanext.webpageviewplus import content, frame, health, services, settings, thumbnails

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


def _page_url_kept(data: FlattenDataDict, page_url: str) -> bool:
    """The view is already saved with this page address. Problems the card
    already shows then don't block other edits, such as a title."""
    stored = _stored_view(data)
    return stored is not None and ((stored.config or {}).get("page_url") or "") == page_url


def _resource(data: FlattenDataDict) -> Optional[model.Resource]:
    resource_id = _get(data, "resource_id")
    if not resource_id:
        stored = _stored_view(data)
        resource_id = stored.resource_id if stored else None
    return model.Resource.get(resource_id) if resource_id else None


def _tidy_height(data: FlattenDataDict, infer_mode: bool) -> None:
    """Take a height as people type it: "600px", "80%", and, with the
    preset's default height chosen, a value picks its mode (16/9 an aspect
    ratio, 80% a share of the window, a number Fixed) rather than being
    silently ignored."""
    value = _get(data, "height_value")
    if not isinstance(value, str):
        return
    tidy = value.strip()
    share = tidy.endswith("%")
    if tidy.lower().endswith("px"):
        tidy = tidy[:-2].strip()
    elif share:
        tidy = tidy[:-1].strip()
    data[("height_value",)] = tidy
    if infer_mode and tidy and not _get(data, "height_mode"):
        if frame.ASPECT_RE.match(tidy):
            data[("height_mode",)] = "aspect"
        else:
            data[("height_mode",)] = "viewport" if share else "fixed"


def _height_error(mode: str) -> str:
    if mode == "viewport":
        return toolkit._("A share of the window takes a number from 1 to 100")
    if mode == "aspect":
        return toolkit._("An aspect ratio takes width/height, such as 16/9")
    return toolkit._("Fixed takes a number of pixels, such as 600")


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
    if not errors.get(("height_mode",)):
        _tidy_height(data, infer_mode=True)
    mode = _get(data, "height_mode")
    if mode and mode != "auto":
        value = _get(data, "height_value") or ""
        if not frame.height_value_valid(mode, value):
            _add_error(errors, "height_value", _height_error(mode))

    resource = _resource(data)
    page_url = _get(data, "page_url")
    kept = _page_url_kept(data, page_url or "")
    if page_url:
        if not kept and health.mixed_content(page_url):
            _add_error(
                errors,
                "page_url",
                toolkit._(
                    "This page uses http://, which browsers block inside this "
                    "https:// site. Use its https:// address, or link to it instead"
                ),
            )
            return
        # A pasted content-origin URL is an upload too: never unsandboxed.
        url, is_upload = page_url, frame.on_content_origin(page_url, settings.content_origin())
    elif resource is not None:
        url, is_upload = resource.url or "", resource.url_type == "upload"
    else:
        return

    package = resource.package if resource is not None else None
    if (not kept and not is_upload and package is not None and package.private
            and services.is_office_viewer(url)):
        _add_error(
            errors,
            "page_url",
            toolkit._(
                "Microsoft's viewer fetches the file from its own servers, "
                "so it can't show a file in a private dataset"
            ),
        )
        return

    resource_dict = content.resource_dict(resource) if resource is not None else None
    isolating = content.frame_content_origin(resource_dict, is_upload and not page_url)
    if is_upload and resource_dict is not None and content.serves(resource_dict):
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

    sandboxed = settings.harden_downloads()
    preset_name = frame.effective_preset_name(chosen, isolating, sandboxed)
    sandbox = frame.PRESETS[preset_name].sandbox
    override = _get(data, "sandbox")
    if override:
        sandbox = tuple(frame.parse_sandbox(override)[0])
    same_origin = frame.must_isolate(on_ckan_host, sandboxed)
    if frame.violates_same_origin_rule(sandbox, same_origin):
        _add_error(
            errors,
            "sandbox" if override else "preset",
            toolkit._(
                "These settings could let the page use visitors' logins on "
                "this site: don't combine allow-scripts with allow-same-origin. "
                "Pages on this site and uploads never may; other pages may "
                "once the site sandboxes downloads (harden_downloads)"
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
    _tidy_height(data, infer_mode=False)
    mode = _get(data, "height_mode")
    value = _get(data, "height_value")
    # A blank value is the default (800 px, or 80% of the window), as the
    # form says: its height list has no blank choice, so it always posts one.
    if mode and value and not errors.get(("height_mode",)):
        if not frame.height_value_valid(mode, value):
            _add_error(errors, "height_value", _height_error(mode))
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


def repo_after_validator(
    key: FlattenKey,
    data: FlattenDataDict,
    errors: FlattenErrorDict,
    context: Context,
) -> None:
    """repo_view_plus's checks across fields: the height, and that the view
    shows a repository (its repo_url, else the resource's own URL)."""
    from ckanext.webpageviewplus import repos

    _tidy_height(data, infer_mode=False)
    mode = _get(data, "height_mode")
    value = _get(data, "height_value")
    if mode and value and not errors.get(("height_mode",)):
        if not frame.height_value_valid(mode, value):
            _add_error(errors, "height_value", _height_error(mode))
    if errors.get(("repo_url",)):
        return
    url = _get(data, "repo_url")
    if not url:
        resource = model.Resource.get(_get(data, "resource_id") or "")
        url = resource.url if resource is not None and resource.url_type != "upload" else ""
    if not repos.is_repo_link(url or ""):
        hosts = repos.self_hosted_sites()
        if hosts:
            # TRANSLATORS: {hosts} is a list of this site's own Git servers.
            _add_error(errors, "repo_url", toolkit._(
                "Not the address of a repository on GitHub, GitLab, Codeberg, Bitbucket "
                "or {hosts}, such as https://github.com/ckan/ckan").format(hosts=hosts))
        else:
            _add_error(errors, "repo_url", toolkit._(
                "Not the address of a repository on GitHub, GitLab, Codeberg or Bitbucket, "
                "such as https://github.com/ckan/ckan"))
