# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A CKAN extension (scaffolded with `ckan generate extension`) meant as a drop-in replacement for core's
`webpage_view`. It adds safe, modern iframe embedding: sandbox presets, Permissions Policy `allow`,
self-sizing, a control bar, and a separate content origin for uploaded HTML. Targets CKAN 2.10 and
2.11 (Python 3.10+).

**The design spec is the source of truth for features:** the Claude Docs doc
"ckanext-webpageviewplus: design spec" (https://claude.ai/artifact/DFvE8C8NViiMK8Yvses1PV). Read it
with the Claude Docs connector, not WebFetch. Its "Decisions" table at the end records where the
code departs from the earlier sections. Keep it current when you depart further. Its rollout is phased
(phase 3 was moved ahead of phase 2):
1. Attribute builder, presets (`strict`, `interactive`, `external`, `data-schematic`), per-view form,
   `ckan webpageviewplus migrate` command (no content origin yet).
2. Self-sizing (generic `webpageviewplus:resize` message + qsv adapter), control bar,
   frameability check.
3. Content-origin route, signed URLs, cloud-storage redirects, optional height reporter.
4. Chromium-only extras behind settings (`credentialless`, `csp`).

The core security invariant: a frame whose URL is on CKAN's own host must never run scripts with
its real origin, so it gets no unsandboxed `external` and no `allow-scripts` + `allow-same-origin`.
It's checked on save (a validator) AND on render (the builder), and a failing render falls back to
`strict`. "CKAN's own host" means a host match against `ckan.site_url` plus `site_aliases`,
ignoring scheme and port, because cookies aren't isolated by either. This is deliberately stricter
than the spec's origin comparison. Uploads always count as CKAN's own.

**Until there's a content origin (phase 3), the rule applies to every frame, not just CKAN's
pages** (`frame.must_isolate`). Uploaded HTML is then served from CKAN's origin. A frame whose
scripts keep a real origin can navigate itself (or be redirected) to such an upload, which would
then run as CKAN inside the CKAN page, and no save- or render-time URL check can catch that. So
`external` degrades to `interactive` like `data-schematic` does, and `allow-scripts` +
`allow-same-origin` is refused everywhere. This deliberately departs from the spec, and was decided
with the user. Once phase 3 isolates uploads, the plan is to allow those frames only for
sysadmin-chosen hosts in `allowed_hosts`. Tests that exercise the CKAN-host logic simulate a
content origin (the `content_origin` fixture, or `CO` in `test_frame.py`).

## Architecture (phase 1 done)

- `frame.py`: pure code with no CKAN imports. Holds `PRESETS`, `build_frame()` (the attribute
  builder) and the URL/host helpers. Unit-test it in `tests/test_frame.py`. `build_frame`'s order
  matters: degrade presets that can't run (data-schematic without a content origin becomes
  interactive), apply overrides, enforce the invariant, then work out the height.
  `Preset.sandbox=None` means no sandbox attribute, and `()` means `sandbox=""` (fully
  restricted). Templates must test `is not none`, never truthiness.
- `settings.py`: typed accessors for `config_declaration.yaml`, plus `check()`, which
  `IConfigurable.configure` runs so bad settings stop CKAN at startup. `content_origin()` always
  returns None until phase 3.
- `validators.py`: per-field validators, plus `view_after_validator` (`__after` in the view
  schema) for the checks across fields. It runs before `resource_view_create`/`_update` set
  `context['resource']`, so it loads the resource itself. At model level an upload's `url` is a
  bare filename, which is why uploads are special-cased. The advanced fields (`sandbox`, `allow`,
  `referrer_policy`) are sysadmin-only unless `editor_can_loosen`; for other users, stored
  values are copied back over whatever they posted, so a re-save doesn't strip them.
- `plugin.py`: `setup_template_variables` re-checks everything at render time and hands the
  template a built `Frame` (or `frame_error`). It's also called for the form, where
  `resource_view` can be None (new view) or unsaved form data (preview). Never build the iframe
  `src` from `resource_view`/`resource` in templates.
- `downloads.py`: an `after_app_request` hook (registered through `IBlueprint`) that adds a CSP
  `sandbox` (no `allow-same-origin`) to HTML/SVG/XML responses from `*_resource.download` and
  `resource_proxy.proxy_view`. CKAN core serves uploaded HTML inline on its own origin, so
  without this, a content origin alone wouldn't stop a frame navigating to the CKAN-origin copy.
  It matches on the response's mimetype, because editors can set a resource's mimetype.
- `cli.py`: `ckan webpageviewplus migrate [--dry-run] [--uploads-only] [--preset NAME]` rewrites
  `ResourceView.view_type`/`config` directly, because `resource_view_update` can't change a
  view's type. It picks `data-schematic` for uploads containing `qsv-schematic-source`, never
  picks a preset the invariant refuses (that falls to `strict`), and keeps the view's id, title,
  description, order and `page_url`. `--uploads-only` exists because, until phase 3, migrating a
  third-party app's view sandboxes it and breaks its logins/storage.
- Plugin name and view type are both `webpage_view_plus` (per the spec). Stored views reference
  the view type, so don't rename it. Templates are namespaced under `templates/webpageviewplus/`.
- Template strings go through CKAN's `_()`, which %-formats, so a literal `%` in a label breaks
  rendering.
- Packaging follows the 2.11 cookiecutter: metadata and entry points in `pyproject.toml`,
  setuptools and Babel options in `setup.cfg`, and `setup.py` only for message extractors.
  `ckanext/` is a namespace package. Keep `*.yaml` in `MANIFEST.in` (for
  `config_declaration.yaml`).

## Local CKAN (Docker)

Dev stack: `~/GitHub/ckan-docker` (`docker-compose.dev.yml`), container `ckan-docker-ckan-dev-1`,
CKAN 2.11.5, http://localhost:5001.
- This repo is bind-mounted into the container at
  `/srv/app/src_extensions/ckanext-webpageviewplus` and installed there with `pip install -e`.
  Python and template edits are live (the dev server auto-reloads). The mount is one volume line
  in ckan-docker's `docker-compose.dev.yml`, committed on ckan-docker's local branch
  `local/dev-mounts`. If that line is lost while the plugin is still enabled, the editable install
  points at an empty directory and ckan-dev crash-loops with `Plugin 'webpage_view_plus' not found`.
  Fix: switch ckan-docker back to `local/dev-mounts` (or remove the plugin from `.env`), then run
  `docker compose -f docker-compose.dev.yml up -d ckan-dev`.
- `webpage_view_plus` is in `CKAN__PLUGINS` in ckan-docker's `.env` (before `envvars`, which must
  stay last).
- Re-run the editable install only after changing entry points / packaging:
  `docker exec -u root ckan-docker-ckan-dev-1 pip install --no-deps -e /srv/app/src_extensions/ckanext-webpageviewplus`
- If ckan-dev crash-loops on boot, check `docker logs ckan-docker-ckan-dev-1`. A plugin that fails
  to load also aborts `prerun.py`, and the datapusher token error that follows clears after a
  `docker restart` once the plugin loads.

### Running tests — read before running pytest

CKAN reads `CKAN_*` env vars and they **override the ini file**. In the dev container:
- `CKAN_SQLALCHEMY_URL` points at the **dev** DB (`ckandb`), and `CKAN_SITE_ID=default`.
- `CKAN__PLUGINS` replaces `ckan.plugins` from `test.ini`.
- The provided `TEST_CKAN_SQLALCHEMY_URL` is unusable: it has a `postgres://` scheme (SQLAlchemy 2
  rejects it) and the wrong user. The `ckan_test` DB exists and is owned by the dev DB user.
- Solr has only the `ckan` core. Test data is kept apart from dev data only by `ckan.site_id`.

So always point the DB at `ckan_test`, set the test site id, and use `non_clean_db` (never
`clean_db`/`clean_index`):

```sh
docker exec ckan-docker-ckan-dev-1 sh -c '
  TEST_DB_URL="$(echo "$CKAN_SQLALCHEMY_URL" | sed "s#/ckandb\$#/ckan_test#")"
  case "$TEST_DB_URL" in */ckan_test) ;; *) echo "refusing: not ckan_test"; exit 1;; esac
  cd /srv/app/src_extensions/ckanext-webpageviewplus &&
  env -u CKAN__PLUGINS -u CKAN_SITE_URL CKAN_SQLALCHEMY_URL="$TEST_DB_URL" CKAN_SITE_ID=test.ckan.net \
    pytest --ckan-ini=test.ini --disable-warnings -q -p no:cacheprovider ckanext/webpageviewplus'
```

For a single test, replace the final path with e.g.
`ckanext/webpageviewplus/tests/test_plugin.py::TestWebpageViewPlus::test_can_view`.

`test.ini` uses the absolute `use = config:/srv/app/src/ckan/test-core.ini`, the core test config's
path in every `ckan/ckan-dev` image. That's why CI runs it without rewriting.

### CI

`.github/workflows/test.yml` runs the suite on a CKAN 2.10 / 2.11 matrix in `ckan/ckan-dev` images,
with throwaway Postgres, Solr and Redis services. `pytest-ckan` comes from `dev-requirements.txt`.
The spec also plans a Playwright suite (Chromium, Firefox, WebKit), which isn't set up yet.
