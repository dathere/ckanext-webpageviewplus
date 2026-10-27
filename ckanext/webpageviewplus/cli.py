from __future__ import annotations

from typing import Optional

import click

import ckan.model as model
from ckan.lib.uploader import get_resource_uploader

from ckanext.webpageviewplus import content, frame, settings

SCHEMATIC_MARKER = b"qsv-schematic-source"
# Stop scanning large uploads after this many bytes.
SCAN_LIMIT = 64 * 1024 * 1024
CHUNK = 1024 * 1024


def file_contains(path: str, marker: bytes, limit: int = SCAN_LIMIT) -> bool:
    """Scan a file in chunks for a marker, overlapping chunks so a marker that
    straddles a boundary is still found."""
    tail = b""
    read = 0
    with open(path, "rb") as f:
        while read < limit:
            chunk = f.read(CHUNK)
            if not chunk:
                return False
            if marker in tail + chunk:
                return True
            tail = chunk[-(len(marker) - 1):]
            read += len(chunk)
    return False


def looks_like_schematic(resource: model.Resource) -> bool:
    """A qsv Data Schematic carries a qsv-schematic-source block. Only uploads
    are checked: linked pages aren't fetched."""
    if resource.url_type != "upload":
        return False
    try:
        uploader = get_resource_uploader(
            {"id": resource.id, "url": resource.url, "url_type": resource.url_type}
        )
        path = uploader.get_path(resource.id)
        return file_contains(path, SCHEMATIC_MARKER)
    except Exception:  # cloud uploaders, missing files: treat as not a schematic
        return False


def choose_preset(
    resource: Optional[model.Resource],
    page_url: str,
    requested: Optional[str],
) -> tuple[Optional[str], str]:
    """Return (preset, reason) for a migrated view: never one the site
    disallows or the same-origin rule would refuse at save time. None means
    no preset fits, and the view is left as it is."""
    if requested:
        preset, reason = requested, "--preset"
    elif (
        "data-schematic" in settings.allowed_presets()
        and resource is not None
        and not page_url
        and looks_like_schematic(resource)
    ):
        preset, reason = "data-schematic", "qsv schematic"
    else:
        preset, reason = settings.default_preset(), "default"

    is_upload = not page_url and resource is not None and resource.url_type == "upload"
    if page_url:
        url = page_url
    elif (
        is_upload
        and resource is not None
        and content.serves(content.resource_dict(resource))
    ):
        url = settings.content_origin() + "/"
    else:
        url = (resource.url if resource is not None else "") or ""
    isolating = settings.isolating_content_origin()
    same_origin = frame.must_isolate(
        frame.frame_on_ckan_host(
            url, settings.ckan_hosts(), is_upload, settings.content_origin()
        ),
        isolating,
    )

    def safe(name: str) -> bool:
        if is_upload and frame.preset_for_upload(name) != name:
            return False
        effective = frame.effective_preset_name(name, isolating)
        return not frame.violates_same_origin_rule(
            frame.PRESETS[effective].sandbox, same_origin
        )

    if safe(preset):
        return preset, reason
    # Fall back to an allowed preset the rule accepts: the default if it
    # qualifies, else the first in the site's list. Only if none does, strict
    # (which is also what such a view renders as).
    allowed = settings.allowed_presets()
    for name in [settings.default_preset()] + allowed:
        if name in allowed and safe(name):
            return name, "{} isn't allowed for this page".format(preset)
    return None, "no allowed preset is safe for this page"


@click.group(short_help="ckanext-webpageviewplus commands")
def webpageviewplus():
    pass


@webpageviewplus.command()
@click.option("--dry-run", is_flag=True, help="Show what would change, change nothing.")
@click.option(
    "--uploads-only",
    is_flag=True,
    help="Only migrate views of uploaded resources that use their own URL; "
    "leave views of linked pages on webpage_view.",
)
@click.option(
    "--preset",
    type=click.Choice(sorted(frame.PRESETS)),
    help="Preset for every migrated view (default: detect, else the site default).",
)
def migrate(dry_run: bool, uploads_only: bool, preset: Optional[str]):
    """Convert webpage_view views to webpage_view_plus.

    Keeps each view's id, title, description, order and page_url.

    Until uploads are served from a separate content origin, the external
    preset runs sandboxed, so third-party apps that need their own logins or
    storage stop working once migrated. --uploads-only converts just the
    views of uploaded HTML, which gain the most (stock webpage_view runs them
    unsandboxed on CKAN's origin), and leaves linked pages alone.
    """
    if preset and preset not in settings.allowed_presets():
        raise click.UsageError(
            "{} isn't in ckanext.webpageviewplus.presets".format(preset)
        )
    views = (
        model.Session.query(model.ResourceView)
        .filter(model.ResourceView.view_type == "webpage_view")
        .order_by(model.ResourceView.resource_id, model.ResourceView.order)
        .all()
    )
    migrated = 0
    for view in views:
        config = dict(view.config or {})
        resource = model.Resource.get(view.resource_id)
        if uploads_only and (
            config.get("page_url")
            or resource is None
            or resource.url_type != "upload"
        ):
            continue
        migrated += 1
        chosen, reason = choose_preset(resource, config.get("page_url") or "", preset)
        if chosen is None:
            click.echo(
                "{} {} skipped: {}; left on webpage_view".format(
                    view.id, view.resource_id, reason
                ),
                err=True,
            )
            migrated -= 1
            continue
        click.echo("{} {} -> {} ({})".format(view.id, view.resource_id, chosen, reason))
        if not dry_run:
            config["preset"] = chosen
            view.config = config
            view.view_type = "webpage_view_plus"

    if dry_run:
        click.echo("{} view(s) would be migrated (dry run).".format(migrated))
    else:
        model.repo.commit()
        click.echo("{} view(s) migrated.".format(migrated))


def get_commands():
    return [webpageviewplus]
