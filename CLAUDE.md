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
code departs from the earlier sections. Keep it current when you depart further. Rollout, in the
order now being built:
1. Done: attribute builder, presets (`strict`, `interactive`, `external`, `data-schematic`),
   per-view form, `ckan webpageviewplus migrate`.
2. Done (filesystem uploads): content-origin route, signed URLs. Not yet: cloud-storage
   redirects.
3. Next: self-sizing (generic `webpageviewplus:resize` message + qsv adapter), control bar,
   frameability check. **Target the qsv release after 27.0.1**: its schematics send
   `qsvVizHeight` themselves when framed (qsv PR #4949). Never inject a height reporter into
   uploaded HTML; older schematics just keep a fixed fallback height. The qsv protocol is in the
   spec's self-sizing table and the qsv wiki (Visualization#embedding). Its messages have no
   `type` field, are posted with targetOrigin `*`, and arrive with `event.origin` "null" from
   frames without `allow-same-origin`, so validate on `event.source`.
4. Chromium-only extras behind settings. Done: `credentialless` (a site list of presets, applied
   at the end of `build_frame` to the preset the frame runs as, third-party pages only: uploads
   and CKAN's own pages load and reload through CKAN, which needs the visitor's session). It's
   ignored outside Chromium, so it must never count toward the invariant. `csp` (CSP Embedded
   Enforcement) is deferred: in Chromium it blocks any page whose server doesn't opt in. If a site
   asks, build a cross-browser setting adding directives to the CSP on served HTML uploads instead.

## The security model (read before touching frame, validator, content or download code)

- **Invariant:** while uploaded HTML could still run on CKAN's origin, no frame may let scripts
  keep a real origin. A frame with no sandbox, or with `allow-scripts` + `allow-same-origin`, can
  navigate itself (or be redirected) to such an upload, which then runs as CKAN inside the CKAN
  page, and no URL check at save or render time can stop that. Uploads count as "isolated" only
  when `content_origin` is set AND `harden_downloads` is on
  (`settings.isolating_content_origin()`). Otherwise `frame.must_isolate` applies the rule to
  every frame, and `external`/`data-schematic` degrade to `interactive`. With isolation, it still
  applies to frames on CKAN's own host.
- **"CKAN's own host"** is a host match against `ckan.site_url` plus `site_aliases`, ignoring
  scheme and port, because cookies aren't isolated by either. This is stricter than the spec. Hosts
  are compared in canonical form (`frame._canonical_host`), and URLs whose host Python and a browser
  could parse differently are refused outright (`is_http_url`). An upload counts as CKAN's unless
  it's framed from the content origin (`frame.frame_on_ckan_host`).
- **`external` never applies to uploads** (`frame.preset_for_upload`). User content always gets a
  sandbox, and `data-schematic` is the upload preset.
- Checked on save (`view_after_validator`) AND on render (`build_frame`, which falls back to
  `strict`), and `migrate` follows the same rule. All three must agree, so change them together.
- Tests that exercise post-isolation logic simulate it with the `content_origin` fixture
  (`test_plugin.py`, `test_cli.py`), `CO` in `test_frame.py`, or real config markers in
  `test_content.py`.

## Architecture

- `frame.py`: pure code with no CKAN imports. Holds `PRESETS`, `build_frame()` (the attribute
  builder) and the URL/host helpers. `build_frame`'s order matters: degrade presets that can't
  run, then the upload policy, apply overrides, enforce the invariant, then work out the height.
  `Preset.sandbox=None` means no sandbox attribute, and `()` means `sandbox=""` (fully
  restricted). Templates must test `is not none`, never truthiness.
- `settings.py`: typed accessors for `config_declaration.yaml`, plus `check()`, which
  `IConfigurable.configure` runs so bad settings stop CKAN at startup. That includes a content
  origin that is a CKAN host, a subdomain or a parent, non-https off localhost, or a short
  `signing_secret`.
- `validators.py`: per-field validators, plus `view_after_validator` (`__after` in the view
  schema) for the checks across fields. It runs before `resource_view_create`/`_update` set
  `context['resource']`, so it loads the resource itself. At model level an upload's `url` is a
  bare filename. The advanced fields (`sandbox`, `allow`, `referrer_policy`) are sysadmin-only
  unless `editor_can_loosen`; for other users, stored values are copied back over whatever they
  posted.
- `plugin.py`: `setup_template_variables` re-checks everything at render time and hands the
  template a built `Frame` (or `frame_error`). It's also called for the form, where
  `resource_view` can be None (new view) or unsaved form data (preview). Never build the iframe
  `src` from `resource_view`/`resource` in templates.
- `content.py`: the content origin.
  - **The route** `/webpageviewplus/content/<id>/<file>` serves HTML uploads, but only on the
    content host and only for filesystem `ResourceUpload`s (`content.serves`). Public means an
    anonymous `package_show` succeeds. Private needs an HMAC over `resource_id:window`, accepting
    the current and previous `url_ttl` window.
  - **`ContentHostMiddleware`** (WSGI, via `IMiddleware`) confines the content host to that route
    and owns its `Set-Cookie`/`Cache-Control`, because CKAN's own response hooks run after the
    view and would overwrite them. Every failure is a 404, so private resources aren't revealed.
- `downloads.py`: an `after_app_request` hook. HTML uploads served by `*_resource.download` are
  302'd to their content URL when `content.serves` applies; otherwise HTML/SVG/XML from those
  responses and `resource_proxy.proxy_view` get a CSP `sandbox` header (no
  `allow-same-origin`). It matches on the response mimetype, because editors can set a
  resource's mimetype.
- `assets/webpageviewplus.js`: plain script, no dependencies and nothing inline, so a site CSP
  without `'unsafe-inline'` works. It's included via `{% asset %}`. `webassets.yml` is read only
  at CKAN startup, so restart ckan-dev after adding a bundle. It does three things:
  - **Self-sizing.** It reads each frame's `data-frame-origin`, `data-opaque`,
    `data-height-mode` and `data-min/max-height`, which `build_frame` computes. A message counts
    only if `event.source` is one of the view's frames and `event.origin` is that frame's
    origin, or `"null"` when it's opaque, i.e. sandboxed without `allow-same-origin`. Replies to
    opaque frames go to `"*"`.
  - **The qsv protocol.** It sends `qsvVizViewport` unasked on each frame's load, because a
    schematic asks only once and may ask before this script runs.
  - **The control bar and the form's frameability warning.**
- Control bar: Open in new tab and Reload use `open_url`. For a content-origin upload that's
  CKAN's download URL, because the frame's signed URL may have expired. The per-view field is
  `hide_controls`, so a view saved without it shows the bar.
- `frameability.py` + `actions.py`: `webpageviewplus_frameability`, a `side_effect_free` action
  the form calls by GET as the editor types (never in the view validator, which runs before
  CKAN's auth check). Its auth requires `resource_view_create` on the resource. It's off unless
  `frameability_check` is set. The SSRF guards are documented at the top of `frameability.py`;
  every failure must stay the same "unknown" verdict. The header rules follow browsers:
  `frame-ancestors` overrides `X-Frame-Options`, `ALLOW-FROM` is ignored, and `SAMEORIGIN` and
  `'self'` count as blocked.
- `tests/fixtures/qsv_protocol.html` speaks the qsv messages. It's uploaded to the `wvp-demo`
  dataset with three views (`data-schematic` auto, `interactive` auto, `interactive` fixed),
  for browser checks with Playwright (`uv run --with playwright`, system Chrome via
  `channel="chrome"`).
- `cli.py`: `ckan webpageviewplus migrate [--dry-run] [--uploads-only] [--preset NAME]` rewrites
  `ResourceView.view_type`/`config` directly, because `resource_view_update` can't change a
  view's type. It never stores a preset the site disallows or the invariant refuses, and skips
  the view instead. `--uploads-only` exists because, without isolation, migrating a third-party
  app's view sandboxes it and breaks its logins and storage.
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
- The dev site runs with a content origin: `CKANEXT__WEBPAGEVIEWPLUS__CONTENT_ORIGIN=http://127.0.0.1:5001`
  and a `..._SIGNING_SECRET` in ckan-docker's `.env` (read by the `envvars` plugin). Browsers
  treat `localhost` and `127.0.0.1` as different sites, so this exercises real isolation with no
  second domain. Remove both lines to test the degraded, no-content-origin behaviour.
- A public demo dataset `wvp-demo` (org `wvp-demo-org`) has one view per preset on a linked page
  and an uploaded HTML page. The upload prints its origin and whether it can reach the parent
  page. It's seeded by a script run inside the container that calls CKAN actions with
  `ignore_auth`; don't mint API tokens for this.
- Re-run the editable install only after changing entry points / packaging:
  `docker exec -u root ckan-docker-ckan-dev-1 pip install --no-deps -e /srv/app/src_extensions/ckanext-webpageviewplus`
- If ckan-dev crash-loops on boot, check `docker logs ckan-docker-ckan-dev-1`. A plugin that fails
  to load also aborts `prerun.py`, and the datapusher token error that follows clears after a
  `docker restart` once the plugin loads.

### Running tests — read before running pytest

CKAN reads `CKAN_*` env vars and they **override the ini file**. In the dev container:
- `CKAN_SQLALCHEMY_URL` points at the **dev** DB (`ckandb`), and `CKAN_SITE_ID=default`.
- `CKAN__PLUGINS` replaces `ckan.plugins` from `test.ini`, and `CKAN_SITE_URL` replaces its
  `ckan.site_url` (tests assume `test.ckan.net`, as in CI).
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
