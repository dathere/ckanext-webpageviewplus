# Changelog

## Unreleased

### Added

- CKAN 2.12 support. Its new file storage (`ckan.files`) is recognised: uploads in a
  filesystem storage (`ckan:fs`) are served from the content origin, like uploads in CKAN's
  upload folder before; other storages stay sandboxed on CKAN's origin. CI tests 2.10, 2.11
  and 2.12.

## 0.4.0 (2026-10-30)

### Added

- Fifteen more well-known services, each checked in Chrome with and without the
  `interactive` sandbox, and by clicking into it:
  - `interactive`: Google Sheets, Docs and Slides published to the web, Our World in Data
    charts and explorers (the query that picks countries and years is kept), and Felt maps.
  - `external`: ArcGIS Online (Dashboards, Experience Builder, Instant Apps and web maps),
    Looker Studio, Streamlit, Hugging Face Spaces and shinyapps.io apps, Infogram, uMap,
    OpenStreetMap's embed address (sandboxed, its tiles carry no Referer and OpenStreetMap
    blocks them), the Internet Archive and Sketchfab.

### Fixed

- A notebook URL with a query isn't turned into nbviewer's `urls/` form, which would drop it.
- A remembered consent card opening on its own no longer starts the failure timer, so a lazy
  frame below the fold doesn't count as failed; "Always load" shows as remembered only when
  the browser saved it.

### Changed

- The README leads with a plain-language overview; the technical details are under Advanced.
- The demo GIF ends with a clip of each of the 29 well-known services.

## 0.3.0 (2026-10-29)

### Added

- Well-known services: Power BI (Publish to web) and CARTO (public Builder maps) are suggested
  with `interactive`, and ArcGIS StoryMaps and Plotly Cloud apps (`*.plotly.app`, including
  Plotly Studio's) with `external`, each checked in Chrome under every preset.
- Well-known services: Google Forms and Typeform, both `external` (each needs its own origin; a
  Google Form renders without one but its inputs stay disabled). A response was submitted
  through a Google Forms view on the dev site.
- Well-known services: Jupyter notebooks, any public `.ipynb` (GitHub, raw, Gist, nbviewer or
  any https URL), and Google Colab notebooks opened from GitHub, all shown read-only through
  nbviewer with `interactive` (Colab itself refuses every frame).
- Observable links with a version (`@d3/bar-chart/2`, where D3's maintained examples live) and
  links from `old.observablehq.com` are recognised. The new Observable (September 2026) has no
  embeds; `observablehq.com/embed/` redirects to `old.observablehq.com` for every notebook.
- The consent card explains what loading tells the host (with the site's privacy policy link,
  if set) and offers **Always load from *host***. The choice is kept in that browser only;
  later views from that host load without asking and show **Ask me again**, which forgets it.
  `Save-Data: on` still shows the card.

### Changed

- `privacy_profile = eu` now puts the consent card on every third-party view, not only those
  running as `external`. A sandbox doesn't stop the browser sending the visitor's cookies for the
  host when the frame loads: a Google Form under `interactive` shows the visitor's signed-in
  account. EU sites will see the card on `strict` and `interactive` views that loaded directly
  before.
- `interactive` and `data-schematic` now include `allow-forms`. Without it, a plain HTML form,
  in an uploaded page or a third-party one, renders and fills in but silently drops the
  response. Scripts in these frames could already send the
  same requests, and CKAN's own forms need a CSRF token. `strict` is unchanged. Existing views
  pick this up without being edited.
- The view form has help text under the page URL, preset, height, control bar, consent and
  advanced fields, linked to each field for screen readers. The page URL's help names every
  well-known service (from `services.NAMES`, so it stays current), and where `allowed_hosts` is
  set, it lists the accepted hosts. Translated into de, es, fr, it, ja and pt_BR.

### Docs

- README: why YouTube links become `youtube-nocookie.com` embeds, and that `allowed_hosts` must
  list that host.
- README: the well-known services table links each service, and the demo GIF shows the new
  consent card (Always load, then Ask me again), the form's list of services, and ends with a
  montage of all 14 services.
- `demo/seed_features.py` adds one view per well-known service, its settings taken from what
  the view form would suggest.
- README: embedding self-hosted Superset dashboards and Dash apps, and what isn't recognised
  (private Power BI, CARTO and Plotly Cloud embeds, the closed Plotly Chart Studio).

## 0.2.1 (2026-10-29)

### Fixed

- The view form labelled the External preset "runs as Interactive for now" on every site. It now
  says so only where uploads aren't yet isolated (no content origin, or `harden_downloads` off),
  which is when it's true.

### Docs

- The README opens with a demo GIF of the four presets, and explains how the plugin answers
  CKAN's warning about `webpage_view`, including that `allowed_hosts` covers a view's own page
  URL only, not a resource URL it falls back to.
- `demo/` has scripts that seed a feature tour and a qsv Data Schematics gallery on a dev site,
  each view described as a short story of what to look for.

## 0.2.0 (2026-10-28)

Upgrading: run `ckan db upgrade -p webpage_view_plus` (a new table for stored frameability
checks).

### New

- A card instead of a blank frame for pages browsers won't show: `http://` pages on an
  `https://` site, and, with `frameability_check` on, pages that refuse to be framed. Saving a
  view checks its page and stores the answer; showing it only reads that, so visitors never make
  CKAN fetch anything. Needs `ckan db upgrade -p webpage_view_plus` (a new table).
- `ckan webpageviewplus check-views` lists `webpage_view_plus` and core `webpage_view` views
  whose page is gone, unreachable, refuses framing or is blocked as mixed content, and exits
  with status 1 if any are, for cron.
- The view form recognises YouTube, Vimeo, Datawrapper, Flourish, Observable and Tableau Public
  links and offers each service's embed address, preset and height, or says why it won't work
  on the site (YouTube, Vimeo, Observable and Tableau Public need a content origin).
- German, Spanish, French, Italian, Japanese and Brazilian Portuguese translations (first
  versions; corrections welcome).

### Fixed

- Translations never loaded: CKAN looked for `ckanext-webpage_view_plus` catalogues (from the
  plugin name), not `ckanext-webpageviewplus`.
- Strings follow CKAN's i18n guidelines: named placeholders and plural forms in validation
  messages, a translated API error, and translator comments on short labels.

### Viewing

- Browsers without CSS `aspect-ratio` (before Chrome 88, Firefox 89, Safari 15) or `clamp()`
  showed aspect-ratio and share-of-window views at the default iframe height (150 px). They now
  get 600 px.
- Safari before 16.4 (including iOS) ignored the narrow-screen height and the 320 px reflow
  floor, because those rules named `:fullscreen`. They now apply from Safari 14.1.
- In Safari before 16.4 (including iOS 15 and 16.0–16.3), the "Tap to interact" overlay never
  hid, so it sat over every view and blocked using the framed page on touch screens.

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
