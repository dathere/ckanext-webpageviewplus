# Commands

Host: macOS (Darwin, zsh, BSD sed/grep — `sed -i ''`). CKAN itself runs only in Docker; no local CKAN install.

## Dev stack (`~/GitHub/ckan-docker`, `docker-compose.dev.yml`)
- Container `ckan-docker-ckan-dev-1`, http://localhost:5001; repo bind-mounted at `/srv/app/src_extensions/ckanext-webpageviewplus` (edits live).
- Start: `docker compose -f docker-compose.dev.yml up -d ckan-dev` (ckan-docker must be on branch `local/dev-mounts`).
- Logs: `docker logs ckan-docker-ckan-dev-1`.
- Reinstall after entry-point/packaging changes: `docker exec -u root ckan-docker-ckan-dev-1 pip install --no-deps -e /srv/app/src_extensions/ckanext-webpageviewplus`
- Plugin tables on dev DB: `docker exec ckan-docker-ckan-dev-1 ckan db upgrade -p webpage_view_plus`

## Unit/integration tests
Must override env so tests hit `ckan_test`, not dev DB (see CLAUDE.md "Running tests"). Exact command:
```sh
docker exec ckan-docker-ckan-dev-1 sh -c '
  TEST_DB_URL="$(echo "$CKAN_SQLALCHEMY_URL" | sed "s#/ckandb\$#/ckan_test#")"
  case "$TEST_DB_URL" in */ckan_test) ;; *) echo "refusing: not ckan_test"; exit 1;; esac
  cd /srv/app/src_extensions/ckanext-webpageviewplus &&
  env -u CKAN__PLUGINS -u CKAN_SITE_URL CKAN_SQLALCHEMY_URL="$TEST_DB_URL" CKAN_SITE_ID=test.ckan.net \
    pytest --ckan-ini=test.ini --disable-warnings -q -p no:cacheprovider ckanext/webpageviewplus'
```
Single test: replace last path with `ckanext/webpageviewplus/tests/test_x.py::Class::test_name`. Never use `clean_db`/`clean_index`.

## Browser tests (host, repo root)
```sh
docker exec ckan-docker-ckan-dev-1 python /srv/app/src_extensions/ckanext-webpageviewplus/browser_tests/seed.py
WVP_BROWSERS="chromium firefox" WVP_CHROMIUM_CHANNEL=chrome WVP_FIREFOX_CHANNEL=moz-firefox \
  uv run --with playwright --with-requirements browser_tests/requirements.txt --no-project \
    pytest -p no:cacheprovider browser_tests
```
Always `--no-project` (else uv creates `.venv`/`uv.lock`). WebKit only in CI.

## i18n (run inside container)
`python setup.py extract_messages` → `update_catalog` → `compile_catalog`; commit `.po` and `.mo`.

## CLI (inside container)
`ckan webpageviewplus migrate [--dry-run] [--uploads-only] [--preset NAME]`, `check-views`, `purge-stats`, `stats-summary`, `connections`.
