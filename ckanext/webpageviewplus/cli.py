from __future__ import annotations

import json
import time
from typing import Iterable, Optional

import click

import ckan.model as model

from ckanext.webpageviewplus import content, frame, frameability, health, pdf, settings, thumbnails

SCHEMATIC_MARKER = b"qsv-schematic-source"
# Stop scanning large uploads after this many bytes.
SCAN_LIMIT = 64 * 1024 * 1024


def contains(chunks: Iterable[bytes], marker: bytes, limit: int = SCAN_LIMIT) -> bool:
    """Scan a file's chunks for a marker, overlapping chunks so a marker that
    straddles a boundary is still found. Stops after ``limit`` bytes."""
    tail = b""
    read = 0
    for chunk in chunks:
        if read >= limit:
            return False
        chunk = chunk[:limit - read]
        if marker in tail + chunk:
            return True
        tail = (tail + chunk)[-(len(marker) - 1):]
        read += len(chunk)
    return False


def looks_like_schematic(resource: model.Resource) -> bool:
    """A qsv Data Schematic carries a qsv-schematic-source block. Only uploads
    are checked (linked pages aren't fetched), reading the head of the file
    from whatever storage content.stored_file can read, cloud ones included;
    an unreadable upload counts as not a schematic."""
    if resource.url_type != "upload":
        return False
    try:
        stored = content.stored_file(content.resource_dict(resource))
        return stored is not None and contains(stored.chunks(), SCHEMATIC_MARKER)
    except Exception:  # missing files: treat as not a schematic
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

    is_upload = (
        not page_url and resource is not None and resource.url_type == "upload"
    ) or frame.on_content_origin(page_url, settings.content_origin())
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
    isolating = content.frame_content_origin(
        content.resource_dict(resource) if resource is not None else None,
        not page_url and resource is not None and resource.url_type == "upload",
    )
    sandboxed = settings.harden_downloads()
    same_origin = frame.must_isolate(
        frame.frame_on_ckan_host(
            url, settings.ckan_hosts(), is_upload, settings.content_origin()
        ),
        sandboxed,
    )

    def safe(name: str) -> bool:
        if is_upload and frame.preset_for_upload(name) != name:
            return False
        effective = frame.effective_preset_name(name, isolating, sandboxed)
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
@click.option(
    "--dataset",
    help="Only this dataset's views (name or id), to migrate a large site in steps.",
)
def migrate(dry_run: bool, uploads_only: bool, preset: Optional[str],
            dataset: Optional[str] = None):
    """Convert webpage_view views to webpage_view_plus.

    Keeps each view's id, title, description, order and page_url.

    Third-party apps that need their own logins or storage stop working if
    migrated as interactive (the default): migrate them with --preset
    external (uploads still fall back to a sandboxed preset). --uploads-only
    converts just the views of uploaded HTML, which gain the most (stock
    webpage_view runs them unsandboxed on CKAN's origin), and leaves linked
    pages alone.
    """
    if preset and preset not in settings.allowed_presets():
        raise click.UsageError(
            "{} isn't in ckanext.webpageviewplus.presets".format(preset)
        )
    query = model.Session.query(model.ResourceView).filter(
        model.ResourceView.view_type == "webpage_view")
    if dataset:
        package = model.Package.get(dataset)
        if package is None:
            raise click.UsageError("No dataset {}".format(dataset))
        query = query.join(
            model.Resource, model.Resource.id == model.ResourceView.resource_id
        ).filter(model.Resource.package_id == package.id)
    views = query.order_by(
        model.ResourceView.resource_id, model.ResourceView.order).all()
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


def _plugin_loaded(name: str) -> bool:
    import ckan.plugins as plugins
    return plugins.plugin_loaded(name)


@webpageviewplus.command("migrate-pdf")
@click.option("--dry-run", is_flag=True, help="Show what would change, change nothing.")
def migrate_pdf(dry_run: bool):
    """Convert ckanext-pdfview's pdf_view views to pdf_view_plus.

    Keeps each view's id, title, description, order and pdf_url (both view
    types name the field alike). Needs the pdf_view_plus plugin and a
    content origin, without which the converted views couldn't show.
    """
    if not _plugin_loaded(pdf.VIEW_TYPE):
        raise click.UsageError("add pdf_view_plus to ckan.plugins first")
    if not settings.isolating_content_origin():
        raise click.UsageError(
            "pdf_view_plus needs ckanext.webpageviewplus.content_origin "
            "(with harden_downloads on)")
    views = (
        model.Session.query(model.ResourceView)
        .filter(model.ResourceView.view_type == "pdf_view")
        .order_by(model.ResourceView.resource_id, model.ResourceView.order)
        .all()
    )
    migrated = 0
    for view in views:
        config = dict(view.config or {})
        pdf_url = (config.get("pdf_url") or "").strip()
        resource = model.Resource.get(view.resource_id)
        found = None if resource is None else pdf.source(
            {"pdf_url": pdf_url} if pdf_url else {}, content.resource_dict(resource))
        problem = None
        if resource is None:
            problem = "its resource is gone"
        elif pdf_url and not frame.is_http_url(pdf_url):
            problem = "its pdf_url isn't an http(s) URL"
        elif found is None and resource.url_type == "upload":
            # Uploads kept by a cloud storage extension on CKAN 2.10/2.11: the
            # converted view couldn't read the file. pdfview can (the browser
            # fetches it from CKAN), so the view stays as it is.
            problem = ("its uploaded file is in a storage this extension can't read "
                       "(a cloud storage extension on CKAN 2.10/2.11; CKAN 2.12 can)")
        elif found is None:
            problem = "its resource's URL isn't an http(s) URL"
        elif found[0] == "link" and not pdf.host_allowed(found[1]):
            problem = "its PDF's host isn't in allowed_hosts"
        if problem:
            click.echo("{} {} skipped: {}; left on pdf_view".format(
                view.id, view.resource_id, problem), err=True)
            continue
        migrated += 1
        click.echo("{} {} -> {}{}".format(
            view.id, view.resource_id, pdf.VIEW_TYPE,
            " ({})".format(pdf_url) if pdf_url else ""))
        if not dry_run:
            # Only the fields pdf_view_plus knows: pdfview has no others.
            view.config = {"pdf_url": pdf_url} if pdf_url else {}
            view.view_type = pdf.VIEW_TYPE

    if dry_run:
        click.echo("{} view(s) would be migrated (dry run).".format(migrated))
    else:
        model.repo.commit()
        click.echo("{} view(s) migrated.".format(migrated))


@webpageviewplus.command("purge-stats")
def purge_stats():
    """Delete view counts, and hosts not reported, in more than
    view_counting_retention_days. Run it daily from cron."""
    from ckanext.webpageviewplus import connections, stats

    days = settings.view_counting_retention_days()
    deleted = stats.purge(days)
    click.echo("{} daily total(s) older than {} days deleted.".format(deleted, days))
    deleted = connections.purge(days)
    click.echo("{} host(s) not reported in {} days deleted.".format(deleted, days))


@webpageviewplus.command("stats-summary")
@click.option("--limit", default=20, show_default=True, help="Views to list.")
def stats_summary(limit: int):
    """Site-wide view counts per view, busiest first."""
    from ckanext.webpageviewplus import stats

    rows = stats.summary()[:limit]
    if not rows:
        click.echo("No view counts.")
        return
    names = ("impressions",) + stats.FLAGS
    click.echo("view_id\t" + "\t".join(names))
    for row in rows:
        click.echo("\t".join([row["view_id"]] + [str(row[n] or 0) for n in names]))


@webpageviewplus.command("connections")
@click.option("--limit", default=50, show_default=True, help="Hosts to list.")
def connections_summary(limit: int):
    """Hosts uploaded pages were seen contacting (CSP reports), site-wide."""
    from ckanext.webpageviewplus import connections

    rows = connections.summary()[:limit]
    if not rows:
        click.echo("No reported hosts.")
        return
    click.echo("host\tresources\treports")
    for row in rows:
        click.echo("{}\t{}\t{}".format(row["host"], row["resources"], row["reports"]))


VIEW_TYPES = ("webpage_view_plus", "webpage_view")
ACTIVE_CONTENT = ("html", "htm", "xhtml", "svg", "xml")


def _active_content(resource: model.Resource) -> bool:
    """Whether an upload looks like HTML, SVG or XML, which run scripts, or
    has a mimetype a browser might run scripts in (``is_passive_type``)."""
    from ckanext.webpageviewplus import downloads

    fmt = (resource.format or "").lower()
    ext = (resource.url or "").rpartition(".")[2].lower()
    return (fmt in ACTIVE_CONTENT or ext in ACTIVE_CONTENT
            or bool(resource.mimetype and not downloads.is_passive_type(resource.mimetype)))


def assess(
    view: model.ResourceView,
    resource: model.Resource,
    probes: dict[str, frameability.Probe],
    timeout: float,
    store: bool = True,
) -> dict[str, str]:
    """One view's problem ("" if none) and detail. Pages are fetched once
    per URL per run (``probes``), only when public, as in the form's check."""
    config = view.config or {}
    page_url = config.get("page_url") or ""
    url = page_url or resource.url or ""
    plus = view.view_type == "webpage_view_plus"

    def result(problem: str = "", detail: str = "") -> dict[str, str]:
        return {"problem": problem, "detail": detail, "url": url}

    if not page_url and resource.url_type == "upload":
        # Core frames uploads unsandboxed, but with harden_downloads (the
        # default) HTML downloads carry a CSP sandbox, so they can't run as
        # CKAN.
        # A content origin takes HTML uploads off CKAN's origin either way.
        if not plus and not settings.harden_downloads() and _active_content(resource) \
                and not content.serves(content.resource_dict(resource)):
            return result(
                "unsandboxed-upload",
                "uploaded HTML framed without a sandbox, on CKAN's own origin; "
                "`ckan webpageviewplus migrate` fixes it",
            )
        return result(detail="upload")
    if not frame.is_http_url(url):
        return result("not-http", "only http:// and https:// pages can be framed")
    if health.mixed_content(url):
        return result("mixed-content", "http:// page on an https:// site: browsers block it")
    if frame.is_ckan_host(url, settings.ckan_hosts()):
        return result(detail="a page on this site, not fetched")
    allowed = settings.allowed_hosts()
    # As in rendering, allowed_hosts applies to page_url only.
    if plus and page_url and allowed and frame.url_host(url) not in allowed:
        return result("host-not-allowed", "not in ckanext.webpageviewplus.allowed_hosts")
    if url not in probes:
        probes[url] = health.classify(frameability.probe(
            url, frameability.ckan_identity(settings.site_url()),
            time.monotonic() + timeout))
    probe = probes[url]
    if plus and store and probe.problem != "private":
        health.store(view.id, url, probe)
    if probe.problem == "private":
        return result(detail="not fetched: not a public address")
    if probe.problem == "unreachable":
        return result(
            "unreachable", "no answer (unknown host, network error, timeout or too many redirects)")
    if probe.problem == "mixed-content":
        return result("mixed-content", "redirects to an http:// page, which browsers block here")
    if probe.problem:
        return result(probe.problem, "HTTP {}".format(probe.status))
    if probe.verdict == frameability.BLOCKED:
        return result("refuses-embedding", probe.reason)
    return result()


@webpageviewplus.command("check-views")
@click.option("--dataset", help="Only this dataset (name or id).")
@click.option("--all", "show_all", is_flag=True, help="List every view, not only broken ones.")
@click.option("--json", "as_json", is_flag=True, help="JSON instead of tab-separated text.")
@click.option(
    "--timeout", default=10.0, show_default=True,
    help="Seconds per page, redirects included.")
@click.option(
    "--no-store", is_flag=True,
    help="Don't save the results (they decide which views show the "
    "can't-be-embedded card).")
def check_views(
    dataset: Optional[str], show_all: bool, as_json: bool, timeout: float, no_store: bool
):
    """Find web page views that can't show their page.

    Checks webpage_view_plus and core webpage_view views: pages that are
    gone, unreachable, refuse to be framed or are http:// on an https://
    site, and uploads core frames unsandboxed. Exits with status 1 if any
    view has a problem, so it can run from cron. Results for
    webpage_view_plus views are saved: with frameability_check on, a view
    whose page refuses to be framed shows a card instead of a blank frame.
    With the webpage_view_plus_thumbnails plugin, their thumbnails are
    remade too, and those of pdf_view_plus views (a copy stays if remaking
    it fails); that never changes the exit status.
    """
    query = (
        model.Session.query(model.ResourceView, model.Resource, model.Package)
        .join(model.Resource, model.Resource.id == model.ResourceView.resource_id)
        .join(model.Package, model.Package.id == model.Resource.package_id)
        .filter(model.ResourceView.view_type.in_(VIEW_TYPES))
        .filter(model.Resource.state == "active", model.Package.state == "active")
        .order_by(model.Package.name, model.Resource.position, model.ResourceView.order)
    )
    if dataset:
        query = query.filter(
            (model.Package.name == dataset) | (model.Package.id == dataset))
    probes: dict[str, frameability.Probe] = {}
    remake = thumbnails.enabled() and not no_store
    images: dict[tuple[str, str], Optional[bytes]] = {}
    rows = []
    for view, resource, package in query:
        row = assess(view, resource, probes, timeout, store=not no_store)
        if remake and view.view_type in thumbnails.VIEW_TYPES:
            thumbnails.after_save(
                dict(view.config or {}, id=view.id, view_type=view.view_type,
                     resource_id=view.resource_id),
                deadline=time.monotonic() + timeout, keep=True, memo=images)
        row.update(
            dataset=package.name, resource_id=resource.id, view_id=view.id,
            view_type=view.view_type)
        rows.append(row)
    if remake:
        # PDF (Plus) views have no page to check, only a thumbnail to remake.
        pdf_views = (
            model.Session.query(model.ResourceView)
            .join(model.Resource, model.Resource.id == model.ResourceView.resource_id)
            .join(model.Package, model.Package.id == model.Resource.package_id)
            .filter(model.ResourceView.view_type == thumbnails.PDF_VIEW_TYPE)
            .filter(model.Resource.state == "active", model.Package.state == "active")
        )
        if dataset:
            pdf_views = pdf_views.filter(
                (model.Package.name == dataset) | (model.Package.id == dataset))
        for view in pdf_views:
            thumbnails.after_save(
                dict(view.config or {}, id=view.id, view_type=view.view_type,
                     resource_id=view.resource_id),
                deadline=time.monotonic() + timeout, keep=True, memo=images)
    broken = [r for r in rows if r["problem"]]
    shown = rows if show_all else broken
    if as_json:
        click.echo(json.dumps(shown, indent=2))
    else:
        if shown:
            click.echo("problem\tview_type\tdataset\tview_id\turl\tdetail")
        for r in shown:
            click.echo("\t".join((
                r["problem"] or "ok", r["view_type"], r["dataset"], r["view_id"],
                r["url"], r["detail"])))
        click.echo("{} view(s) checked, {} with problems.".format(len(rows), len(broken)),
                   err=True)
    if broken:
        raise SystemExit(1)


BUNDLES = ("webpageviewplus/webpageviewplus-js", "webpageviewplus/webpageviewplus-css")


def diagnose() -> list[tuple[str, str]]:
    """What ``doctor`` reports without fetching anything: ``(level, text)``
    pairs, level ``ok``, ``note``, ``warn`` or ``fail``."""
    from ckan.plugins import toolkit

    found: list[tuple[str, str]] = []
    origin = settings.isolating_content_origin()
    if origin:
        found.append(("ok", "Uploads are isolated on " + origin
                      + ": every preset runs as itself."))
    elif settings.harden_downloads():
        found.append(("note", "No content origin (ckanext.webpageviewplus.content_origin): "
                      "third-party pages run as their presets, External included, but "
                      "uploads stay sandboxed on CKAN's origin, Data schematic runs as "
                      "Interactive, and PDF (Plus) is never offered. See the README's "
                      "'A separate domain for uploads'."))
    else:
        found.append(("warn", "harden_downloads is off, so uploads could run as CKAN: "
                      "External and Data schematic run as Interactive, so YouTube, "
                      "Vimeo, Tableau Public and other services needing External won't "
                      "play, and PDF (Plus) is never offered. Turn "
                      "ckanext.webpageviewplus.harden_downloads back on."))
    try:
        from ckan.cli.db import current_revision
        revision = current_revision("webpage_view_plus") or ""
    except Exception as error:  # an older CKAN, or no database to ask
        found.append(("note", "Couldn't check the plugin's tables: {}".format(error)))
    else:
        if revision.endswith("(head)"):
            found.append(("ok", "The plugin's tables are up to date."))
        else:
            found.append(("fail", "The plugin's tables are missing or out of date. Run: "
                          "ckan db upgrade -p webpage_view_plus"))
    default_views = toolkit.aslist(toolkit.config.get("ckan.views.default_views"))
    if "webpage_view_plus" not in default_views:
        found.append(("note", "webpage_view_plus isn't in ckan.views.default_views, so "
                      "new links to well-known services don't get a view by themselves."))
    if "webpage_view" in default_views:
        found.append(("warn", "ckan.views.default_views still has core webpage_view: "
                      "replace it with webpage_view_plus."))
    core_views = model.Session.query(model.ResourceView).filter(
        model.ResourceView.view_type == "webpage_view").count()
    if core_views:
        found.append(("note", "{} core Website views remain. Run: ckan webpageviewplus "
                      "migrate --dry-run".format(core_views)))
    if not settings.frameability_check():
        found.append(("note", "frameability_check is off: the view form only warns about "
                      "pages on well-known sites that refuse framing, not any page."))
    if _plugin_loaded("repo_view_plus"):
        if settings.github_token():
            found.append(("ok", "github_token is set: GitHub allows 5,000 requests an hour."))
        else:
            found.append(("note", "repo_view_plus is on without github_token: GitHub allows "
                          "this server 60 requests an hour, about 20 repositories, shared by "
                          "every visitor. Set ckanext.webpageviewplus.github_token (a "
                          "fine-grained token with no permissions) for 5,000."))
    return found


def asset_problems(site_url: str, timeout: float = 10) -> list[str]:
    """The extension's script and style bundles that CKAN's pages link to but
    the site doesn't serve. Without the script the form offers no
    suggestions and views don't size themselves. Raises
    ``requests.RequestException`` when the site can't be reached."""
    import requests
    from ckan.lib.webassets_tools import env

    problems = []
    for name in BUNDLES:
        for path in env[name].urls():
            url = site_url.rstrip("/") + path
            status = requests.get(url, timeout=timeout).status_code
            if status != 200:
                problems.append("{}: HTTP {}".format(url, status))
    return problems


def _probe_upload() -> Optional[model.Resource]:
    """A public upload a browser could run as a page, to check its download."""
    found = (
        model.Session.query(model.Resource)
        .join(model.Package, model.Package.id == model.Resource.package_id)
        .filter(model.Resource.url_type == "upload",
                model.Resource.state == "active",
                model.Package.state == "active",
                model.Package.private.is_(False))
        .order_by(model.Resource.created)
    )
    return next((r for r in found.yield_per(100) if _active_content(r)), None)


def _sandboxes(policies: list[str]) -> list[str]:
    """The ``sandbox`` directive browsers enforce in each of a response's CSP
    policies: within one policy, only the first directive of a name counts."""
    found = []
    for policy in policies:
        for directive in policy.split(";"):
            words = directive.split()
            if words and words[0].lower() == "sandbox":
                found.append(" ".join(words))
                break
    return found


def download_finding(site_url: str, timeout: float = 10) -> tuple[str, str]:
    """Whether an HTML upload's download reaches this machine still unable to
    run as CKAN: CSP-sandboxed (no allow-same-origin) with nosniff, or sent
    off CKAN's host (the content origin, or a storage's own link). A proxy or
    CDN rule stripping the header would undo the protection the external
    preset relies on without a content origin. Raises
    ``requests.RequestException`` when the site can't be reached."""
    import requests
    from ckanext.webpageviewplus import downloads

    resource = _probe_upload()
    if resource is None:
        return ("note", "No public HTML upload to check the download header with. After "
                "adding one, run doctor again, or check by hand with curl -sI on its "
                "download URL (see the README's 'Uploaded HTML downloads are sandboxed').")
    package = resource.package
    filename = (resource.url or "").rpartition("/")[2] or "file"
    url = "{}/{}/{}/resource/{}/download/{}".format(
        site_url.rstrip("/"), package.type or "dataset", package.id, resource.id, filename)
    response = requests.get(url, timeout=timeout, allow_redirects=False, stream=True)
    response.close()
    status = response.status_code
    if status in (301, 302, 303, 307, 308):
        location = response.headers.get("Location", "")
        host = frame.url_host(location)
        # The address probed counts as CKAN's too (--site-url may name one
        # that isn't in ckan.site_url or site_aliases).
        hosts = settings.ckan_hosts() + [frame.url_host(site_url)]
        if host and not frame.is_ckan_host(location, [h for h in hosts if h]):
            return ("ok", "An HTML upload's download is sent off CKAN's host, to {} ({})."
                    .format(host, url))
        return ("fail", "An HTML upload's download redirects to CKAN's own host ({} -> {}), "
                "where it isn't sandboxed.".format(url, location or "no Location"))
    if status != 200:
        return ("note", "Couldn't check the download header: {} answered HTTP {}."
                .format(url, status))
    if downloads.is_passive_type(response.headers.get("Content-Type", "")):
        return ("note", "Couldn't check the download header: {} was sent as {}, which "
                "browsers never run.".format(url, response.headers.get("Content-Type")))
    # requests joins repeated headers with commas, which also separate CSP
    # policies, so every policy the response carries is checked.
    sandboxes = _sandboxes(response.headers.get("Content-Security-Policy", "").split(","))
    safe = [s for s in sandboxes if "allow-same-origin" not in s.lower().split()]
    nosniff = response.headers.get("X-Content-Type-Options", "").strip().lower() == "nosniff"
    if safe and nosniff:
        return ("ok", "HTML upload downloads reach this machine sandboxed ({}).".format(url))
    missing = [name for name, ok in (
        ("Content-Security-Policy: sandbox without allow-same-origin", bool(safe)),
        ("X-Content-Type-Options: nosniff", nosniff),
    ) if not ok]
    return ("fail", "An HTML upload's download arrives without " + " or ".join(missing)
            + " ({}). Something between CKAN and this machine (a proxy or CDN rule) "
            "strips it, so uploaded pages could run as CKAN, and External frames "
            "aren't safe without a content origin.".format(url))


@webpageviewplus.command()
@click.option("--no-http", is_flag=True,
              help="Skip fetching from the site (the asset bundles, an HTML "
                   "upload's download headers).")
@click.option("--site-url", default="",
              help="Fetch from here instead of ckan.site_url "
                   "(e.g. http://localhost:5000 inside a container).")
def doctor(no_http: bool, site_url: str):
    """Check this site's setup for the extension and say what to fix.
    Exits 1 when something is broken."""
    found = diagnose()
    if not no_http:
        import requests
        site_url = site_url or settings.site_url()
        try:
            problems = asset_problems(site_url)
        except requests.RequestException as error:
            found.append(("note", "Couldn't reach {} to check the asset bundles ({}). "
                          "Pass --site-url with an address this machine can reach."
                          .format(site_url, type(error).__name__)))
        else:
            if problems:
                found.append(("fail", "The site doesn't serve the extension's bundles, so "
                              "its script never runs:\n    " + "\n    ".join(problems)
                              + "\n  Check ckan.webassets.path is writable and the same for "
                              "every CKAN process, then clear any CDN or proxy cache."))
            else:
                found.append(("ok", "The asset bundles load from " + site_url + "."))
        if settings.harden_downloads():
            try:
                found.append(download_finding(site_url))
            except requests.RequestException as error:
                found.append(("note", "Couldn't reach {} to check the download header ({})."
                              .format(site_url, type(error).__name__)))
    colours = {"ok": "green", "note": None, "warn": "yellow", "fail": "red"}
    for level, text in found:
        click.echo(click.style(level.upper().ljust(4), fg=colours[level], bold=True)
                   + " " + text)
    if any(level == "fail" for level, _ in found):
        raise SystemExit(1)


def get_commands():
    return [webpageviewplus]
