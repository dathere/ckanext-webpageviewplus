# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A CKAN extension (scaffolded with `ckan generate extension`) meant as a drop-in replacement for core's
`webpage_view`. It adds safe, modern iframe embedding: sandbox presets, Permissions Policy `allow`,
self-sizing, a control bar, and a separate content origin for uploaded HTML. Targets CKAN 2.10 and
2.11 (Python 3.10+).

**The design spec is the source of truth for features:** the Claude Docs doc
"ckanext-webpageviewplus: design spec" (https://claude.ai/artifact/DFvE8C8NViiMK8Yvses1PV). Read it
with the Claude Docs connector, not WebFetch. Its rollout is phased:
1. Attribute builder, presets (`strict`, `interactive`, `external`, `data-schematic`), per-view form,
   `ckan webpageviewplus migrate` command (no content origin yet).
2. Self-sizing (generic `webpageviewplus:resize` message + qsv adapter), control bar,
   frameability check.
3. Content-origin route, signed URLs, cloud-storage redirects, optional height reporter.
4. Chromium-only extras behind settings (`credentialless`, `csp`).

The core security invariant: a frame whose resolved URL is on CKAN's own origin must never get
`allow-scripts` + `allow-same-origin` (or no sandbox at all). It's checked on save AND on render;
a failing render falls back to `strict`.

## Current state

So far, only the scaffold: `plugin.py` is a port of core's `ckanext/webpageview` (one `page_url`
field, same `can_view`: format or URL extension `html`/`htm`, `always_available`), plus an
http(s)-only check on the frame URL. Sandbox presets are phase 1.
- Plugin name (entry point in `pyproject.toml`) and view type (`info()['name']`) are both
  `webpage_view_plus`, as the spec requires. Views stored in the DB reference the view type, so
  don't rename it.
- The frame URL must be an absolute `http(s)` URL (`is_http_url`), because a `javascript:` iframe
  `src` runs in CKAN's origin. `page_url` is validated on save (`http_url_validator` in the view
  schema), and `setup_template_variables` checks the resolved URL (`page_url` or the resource URL)
  again on render. The template only gets the checked `frame_url`; never build the `src` from
  `resource_view`/`resource` directly.
- Templates live under `templates/webpageviewplus/` so they don't collide with core's
  `webpage_view.html` / `webpage_form.html`.
- Packaging follows the 2.11 cookiecutter: metadata and entry points in `pyproject.toml`,
  setuptools and Babel options in `setup.cfg`, and `setup.py` only for message extractors.
  `ckanext/` is a namespace package.
- Site settings go in `config_declaration.yaml` via `IConfigDeclaration`, keys
  `ckanext.webpageviewplus.*` (the full list is in the spec).

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
  env -u CKAN__PLUGINS CKAN_SQLALCHEMY_URL="$TEST_DB_URL" CKAN_SITE_ID=test.ckan.net \
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
