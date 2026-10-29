# Tech stack

- Python 3.10+ (classifiers 3.10–3.13); `from __future__ import annotations` used.
- CKAN 2.10 and 2.11 (CI matrix in `ckan/ckan-dev` images). Local dev: CKAN 2.11.5 in Docker.
- Runtime dep: `publicsuffixlist` only (bundled PSL, no network).
- Packaging per CKAN 2.11 cookiecutter: metadata + entry point in `pyproject.toml`; setuptools (`<82`) + Babel options in `setup.cfg`; `setup.py` only for message extractors. `ckanext/` is a namespace package.
- `MANIFEST.in` must keep `*.yaml`, `*.pot`/`*.po`, and migration files (`alembic.ini`, `env.py`, `script.py.mako`, `versions/`) — editable installs hide omissions; check the built wheel.
- DB: SQLAlchemy 2 + alembic migrations per plugin (`ckan db upgrade -p webpage_view_plus`).
- Tests: pytest + `pytest-ckan` (`dev-requirements.txt`); browser: Playwright + axe-playwright-python, pinned in `browser_tests/requirements.txt`, run via `uv run --no-project`.
- Frontend: vanilla JS, CKAN webassets (`webassets.yml` read only at startup → restart ckan-dev after adding a bundle).
- Release: bare version tag (e.g. `0.2.0`) → `.github/workflows/publish.yml` → PyPI trusted publishing.
