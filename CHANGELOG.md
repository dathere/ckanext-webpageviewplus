# Changelog

## Unreleased

### Viewing

- Browsers without CSS `aspect-ratio` (before Chrome 88, Firefox 89, Safari 15) or `clamp()`
  showed aspect-ratio and share-of-window views at the default iframe height (150 px). They now
  get 600 px.

## 0.1.0 (2026-10-28)

The first release: a drop-in replacement for CKAN's `webpage_view` (CKAN 2.10 and 2.11). The
design spec's Decisions table records where it departs from the spec.

### Upgrading and installing

- Add `webpage_view_plus` to `ckan.plugins`, then run
  `ckan db upgrade -p webpage_view_plus` (two tables, used only by opt-in view counting and
  connection reports). Run it again after each upgrade.
- With the plugin enabled, HTML, SVG and XML that CKAN serves from its own origin (downloads,
  the resource proxy) get a CSP `sandbox` header (`harden_downloads`, on by default).
- `ckan webpageviewplus migrate` converts existing `webpage_view` views; see the README.

### Security

- Security presets per view (`strict`, `interactive`, `external`, `data-schematic`), enforced
  on save, on render and in `migrate`: no frame lets scripts keep a real origin while uploaded
  HTML could still run on CKAN's origin.
- An optional content origin serves HTML uploads from a separate site, with signed URLs for
  private resources, `frame-ancestors`, no cookies, and the content host confined to its route.
- An optional frameability check warns editors about pages that refuse framing.
- `credentialless` frames for third-party pages (Chromium, opt-in).

### Viewing

- Self-sizing (`webpageviewplus:resize`, and the qsv Data Schematic protocol from the qsv
  release after 27.0.1), fixed, viewport and aspect heights.
- Control bar: Open in new tab, Fullscreen (Expand where there's no element fullscreen), Reload,
  and Share (`navigator.share`). Wide views lock to landscape in fullscreen on Android.
- Accessibility: skip link, live region, focus and contrast (forced colours), 24/44 px targets,
  reflow at 320 px, and axe-core in the browser tests.
- Phones and tablets: tap to interact, load on tap for `Save-Data`, responsive height below
  `narrow_width`.
- `color_scheme` (opt-in; Firefox only so far) and `<link rel="preconnect">` to the content
  origin.

### Privacy and telemetry

- No telemetry by default. Ad and tracking features are always denied in `allow`.
- Consent card for third-party pages (`consent_gate`, per view, `privacy_profile = eu`, and
  Global Privacy Control).
- DOM events (`webpageviewplus:loaded`, `:expanded`, `:failed`) and structured server logs.
- Opt-in view counting: per-view daily totals only, with a notice, a stats action, and
  `purge-stats` / `stats-summary` commands.
- Opt-in connection reports: `Content-Security-Policy-Report-Only` on uploads, kept as hosts per
  resource, with an optional "This page connects to" disclosure.

### Known limitations

- Only filesystem uploads are served from the content origin; cloud storage uploads stay framed
  from CKAN's origin under the sandbox.
- Esc doesn't close Expand while focus is inside the framed page (Close always does).
- Firefox and WebKit give no fullscreen inside `interactive` frames (opaque origins).
- View-counting beacons and connection reports can be posted by anyone.
- A frame that finishes loading before the view's script runs isn't counted as loaded.
