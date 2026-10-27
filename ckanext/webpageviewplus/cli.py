from __future__ import annotations

from typing import Optional

import click

import ckan.model as model
from ckan.lib.uploader import get_resource_uploader

from ckanext.webpageviewplus import frame, settings

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
) -> tuple[str, str]:
    """Return (preset, reason) for a migrated view, never one that the
    same-origin rule would refuse at save time."""
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

    if page_url:
        same_origin = frame.is_ckan_host(page_url, settings.ckan_hosts())
    elif resource is not None and resource.url_type == "upload":
        same_origin = True
    else:
        url = resource.url if resource is not None else ""
        same_origin = frame.is_ckan_host(url or "", settings.ckan_hosts())

    effective = frame.effective_preset_name(preset, settings.content_origin())
    if frame.violates_same_origin_rule(frame.PRESETS[effective].sandbox, same_origin):
        return "strict", "{} refused on CKAN's own host".format(preset)
    return preset, reason


@click.group(short_help="ckanext-webpageviewplus commands")
def webpageviewplus():
    pass


@webpageviewplus.command()
@click.option("--dry-run", is_flag=True, help="Show what would change, change nothing.")
@click.option(
    "--preset",
    type=click.Choice(sorted(frame.PRESETS)),
    help="Preset for every migrated view (default: detect, else the site default).",
)
def migrate(dry_run: bool, preset: Optional[str]):
    """Convert webpage_view views to webpage_view_plus.

    Keeps each view's id, title, description, order and page_url.
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
    for view in views:
        config = dict(view.config or {})
        resource = model.Resource.get(view.resource_id)
        chosen, reason = choose_preset(resource, config.get("page_url") or "", preset)
        click.echo("{} {} -> {} ({})".format(view.id, view.resource_id, chosen, reason))
        if not dry_run:
            config["preset"] = chosen
            view.config = config
            view.view_type = "webpage_view_plus"

    if dry_run:
        click.echo("{} view(s) would be migrated (dry run).".format(len(views)))
    else:
        model.repo.commit()
        click.echo("{} view(s) migrated.".format(len(views)))


def get_commands():
    return [webpageviewplus]
