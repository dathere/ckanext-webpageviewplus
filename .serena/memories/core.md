# ckanext-webpageviewplus — core

CKAN extension; drop-in replacement for core `webpage_view` (safe iframe embedding). Plugin name AND view type `webpage_view_plus` — never rename (stored views reference it).

Root `CLAUDE.md` is the authoritative, detailed guide (security model, per-module architecture, rollout status, dev stack, test recipe). Feature source of truth: Claude Docs design spec (link in CLAUDE.md; read via Claude Docs connector, not WebFetch); its "Decisions" table records departures — keep it current.

## Source map (`ckanext/webpageviewplus/`)
- `frame.py` — pure (no CKAN imports): `PRESETS`, `build_frame()`, URL/host helpers, isolation invariant (`must_isolate`, `violates_same_origin_rule`).
- `services.py` — pure: well-known-service embed suggestions.
- `settings.py` + `config_declaration.yaml` — typed config accessors; `check()` fails startup on bad config.
- `validators.py` — field validators + `view_after_validator` (cross-field, loads resource itself).
- `plugin.py` — CKAN interfaces; `setup_template_variables` re-checks at render, hands template a built `Frame`.
- `content.py` — content-origin route + `ContentHostMiddleware`; `downloads.py` — download hardening hook.
- `frameability.py` (SSRF-guarded probe), `health.py` (stored per-view verdicts), `actions.py` (actions + chained create/update).
- `stats.py`, `connections.py`, `events.py` — opt-in counting, CSP report collection, structured logging.
- `cli.py` — `ckan webpageviewplus migrate|check-views|purge-stats|stats-summary|connections`.
- `assets/webpageviewplus.js` (plain, no deps, nothing inline), `style.css`, `webassets.yml`; templates in `templates/webpageviewplus/{view,form}.html`.
- `migration/webpage_view_plus/versions/` — alembic (3 plugin tables); `i18n/` — de es fr it ja pt_BR.
- `tests/` — pytest-ckan unit/integration; `browser_tests/` (repo root) — Playwright, separate `pytest.ini`.

## Invariants (cross-cutting)
- Security rule is enforced in 3 places that must agree: save (`view_after_validator`), render (`build_frame`), `cli migrate`. Change together. Read the CLAUDE.md security section before touching frame/validator/content/download code.
- Never build iframe `src` from `resource_view`/`resource` in templates.
- `Preset.sandbox`: `None` = no attribute, `()` = `sandbox=""`; templates test `is not none`.

## Further memories
- Language/framework versions, packaging layout: `mem:tech_stack`
- Dev-stack, test, browser-test, i18n and release commands: `mem:suggested_commands`
- Code style and i18n/template string rules, lint caveats: `mem:conventions`
- What to run before declaring a change done: `mem:task_completion`
