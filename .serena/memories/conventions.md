# Conventions

## Python
- Module docstrings explain the module's role/constraints; comments only for non-obvious WHY.
- Type hints throughout, `typing.Optional`/`Sequence` style (not PEP 604) — match surrounding code.
- `frame.py` and `services.py` stay pure (no CKAN imports) so security rules unit-test directly; pass settings in.
- Config only via `settings.py` accessors declared in `config_declaration.yaml`; new settings get a `check()` rule if misconfiguration is dangerous.
- Security-relevant failures surface uniformly: content-host failures are always 404; frameability failures always the same "unknown" verdict.
- No project ruff/flake8 config. A user-level ruff config flags ~190 items (UP032 f-string, UP045, I001, …); not a gate — do NOT mass-`--fix`: `.format()` with named placeholders is required for i18n strings.

## i18n / templates
- All UI strings through `_()` / `toolkit._`; counts via `toolkit.ungettext`; named placeholders + `.format()`.
- Literal `%` in a `_()` label breaks rendering (CKAN %-formats).
- `TRANSLATORS:` comment on the line before short labels; in `{% set %}` dicts put the label needing it first.
- Plugin overrides `i18n_domain()` to `ckanext-webpageviewplus`.
- Script has no translations: labels it swaps in come from template `data-*` attributes.

## JS (`assets/webpageviewplus.js`)
- No deps, nothing inline (sites may run CSP without `'unsafe-inline'`); included via `{% asset %}`.
- postMessage validation on `event.source` + expected origin (`"null"` for opaque frames).
- Never move an `<iframe>` in the DOM (reloads it).

## Tests
- Post-isolation logic simulated with `content_origin` fixture / `CO` constant / config markers.
- Counting tests use `counts_table` fixture (`non_clean_db` drops plugin tables); events via `events_log` fixture.
- Browser: click inside frames via `locator.evaluate("b => b.click()")`; touch tests use `touch_page`.
- Negative browser tests: verify non-vacuous by temporarily breaking the JS check, then restore (`git diff` empty).

## Git
- Commits only when asked; never put a session link/`Claude-Session:` trailer in commits, PRs or comments (user rule).
- Release: bump `pyproject.toml` version, date the `CHANGELOG.md` entry in the tagging commit.
