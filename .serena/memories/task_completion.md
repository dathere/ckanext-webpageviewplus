# Task completion checklist

1. Unit/integration suite in the dev container with the `ckan_test` override command (`mem:suggested_commands`). Must pass; CI runs it on CKAN 2.10 and 2.11.
2. If `webpageviewplus.js`, templates or CSS changed: reseed and run `browser_tests/` (chromium + firefox locally). `git diff ckanext/webpageviewplus/assets/webpageviewplus.js` must contain only intended changes (no leftover deliberate breakage).
3. If UI strings changed: extract/update/compile catalogs in the container; `tests/test_i18n.py` checks completeness, placeholders, `%`, `.mo` freshness.
4. If a migration, packaging or data file was added: confirm `MANIFEST.in` covers it (build the wheel and inspect).
5. If security rules changed: save validator, `build_frame` and `cli migrate` updated together, with tests on both isolated and non-isolated configs.
6. If behaviour departs from the design spec: update its Decisions table and CLAUDE.md.
7. Check LSP/Serena diagnostics on edited Python files. No lint gate (ruff not configured for the project).
