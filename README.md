[![Tests](https://github.com/dathere/ckanext-webpageviewplus/workflows/Tests/badge.svg?branch=main)](https://github.com/dathere/ckanext-webpageviewplus/actions)

# ckanext-webpageviewplus

A safer, more capable replacement for CKAN's built-in `webpage_view`. It embeds web pages and
uploaded HTML in an `<iframe>` with a security preset per view, modern iframe attributes, and
optionally a separate content origin, so uploaded HTML (such as qsv Data Schematics) can run
fully without being able to act as CKAN. It uses its own view type (`webpage_view_plus`), so it
runs beside `webpage_view` while you migrate.

**What it protects you from.** Out of the box, CKAN serves an uploaded HTML file inline on its own
origin, so anyone who opens it runs its scripts as CKAN, with their login. Stock
`webpage_view` frames it the same way, unsandboxed. This plugin:

- sandboxes every frame it renders, so pages on CKAN's own host never run scripts with CKAN's
  origin;
- adds a CSP `sandbox` header to HTML, SVG and XML that CKAN itself serves (resource downloads
  and the resource proxy), see [Downloads](#uploaded-html-downloads-are-sandboxed);
- with a content origin, moves uploaded HTML off CKAN's origin entirely.


## Requirements

| CKAN version    | Compatible? |
| --------------- | ----------- |
| 2.9 and earlier | no          |
| 2.10            | yes         |
| 2.11            | yes         |

A content origin (optional) needs a second domain, which must not be a subdomain of CKAN's,
pointed at the same CKAN through your reverse proxy. See [Deploying a content
origin](#deploying-a-content-origin).


## Installation

1. Activate your CKAN virtual environment, for example:

       . /usr/lib/ckan/default/bin/activate

2. Install the extension:

       pip install git+https://github.com/dathere/ckanext-webpageviewplus.git

3. Add `webpage_view_plus` to `ckan.plugins` in your CKAN config file (by default
   `/etc/ckan/default/ckan.ini`). Keep `webpage_view` enabled until you've migrated.

4. Create the plugin's tables (used only by opt-in view counting and connection reports, but
   run it anyway so turning those on later just works). Run it again after every upgrade:

       ckan -c /etc/ckan/default/ckan.ini db upgrade -p webpage_view_plus

5. Restart CKAN. Settings are checked at startup, and an unsafe combination stops CKAN with a
   message naming the key.


## Usage

Add a "Website (Plus)" view to an HTML resource and pick a security preset:

| Preset           | Sandbox                                          | Use for |
| ---------------- | ------------------------------------------------ | ------- |
| `strict`         | everything restricted, no scripts                | static HTML: reports, notebooks without JS |
| `interactive`    | `allow-scripts allow-popups allow-downloads`     | scripted pages that need no storage (the default) |
| `external`       | none                                             | third-party sites you trust (dashboards, apps). Needs a content origin, else runs as `interactive`. Never for uploads or pages on CKAN's own host |
| `data-schematic` | `interactive` + `allow-same-origin` and popups that escape the sandbox | qsv Data Schematics (maps, Tour, Explore). Needs a content origin, else runs as `interactive` and maps stay blank |

**The rule behind the presets:** no frame may let scripts keep a real origin while uploaded
HTML could still run on CKAN's origin. Such a page could navigate itself to an upload and run
it as CKAN. Without a content origin, `external` and `data-schematic` therefore run as
`interactive`, and a sandbox combining `allow-scripts` with `allow-same-origin` is refused.

Every frame gets `loading="lazy"`, a `referrerpolicy`, the view's title as its accessible
name, and an `allow` list that always denies ad and tracking features. Editors pick the preset
and height (fixed, share of the window, aspect ratio, auto). Sysadmins can also set sandbox
tokens, allowed features and the referrer policy per view.

**Auto height.** With height "Auto", the frame takes the height the page reports, clamped to
`min_height`/`max_height`; until it reports one, it keeps a fixed fallback height. Any page can
report its height by posting `{type: "webpageviewplus:resize", height: <px>}` to its parent.
qsv Data Schematics from the release after 27.0.1 do it on their own, and the view also answers
their viewport and scroll requests (see the qsv wiki, Visualization#embedding). Only messages
from the view's own frame are accepted.

**Control bar.** Under each view: Open in new tab, Fullscreen, Reload and Share. A site chooses
the buttons with `ckanext.webpageviewplus.controls`, and editors can hide the bar per view.

- **Open in new tab** and **Reload** go through CKAN's download URL for uploads, so a private
  upload's signed link never goes stale.
- **Fullscreen** becomes **Expand** where the browser has no element fullscreen (iPhone
  Safari): the view fills the screen in place, with Close and Esc to leave. While fullscreen, a
  view that's wider than tall turns an Android phone sideways.
- **Share** opens the browser's share sheet with the CKAN page's URL (never the framed page's),
  where `navigator.share` exists, desktops included.

**Theme.** `ckanext.webpageviewplus.color_scheme = light` (or `dark`) makes framed pages see
that as the visitor's colour preference, so a qsv schematic matches the portal. Only Firefox
honours it for these frames so far; the default, `auto`, leaves it to the visitor's OS.


## Accessibility, phones and tablets

- A **skip link** jumps past each frame to its controls, a polite **live region** announces
  "*title* loaded", focus outlines and 24 px targets (44 px on touch screens) work in Windows
  High Contrast too, and axe-core runs on every view in the browser tests.
- **Reflow:** at 320 px wide (400% zoom), every frame is at least 600 px tall, and a view that
  hides its bar still offers Open in new tab.
- **Responsive height:** below `narrow_width` (576 px of view width by default), views sized by
  aspect ratio or share of the window take 80% of the screen's height. Fixed and auto heights
  don't change.
- **Tap to interact:** on a touch screen, a frame taller than half the screen gets a "Tap to
  interact" overlay, so a swipe scrolls the page instead of getting stuck in the frame. It comes
  back once the frame has scrolled out of view.
- **Load on tap:** when the browser asks to save data (`Save-Data: on`), a card with the
  resource's size and a Load button stands in for the page until the reader taps it.

### Uploaded HTML downloads are sandboxed

With this plugin enabled, HTML, SVG and XML from CKAN's resource downloads and the resource
proxy get a `Content-Security-Policy: sandbox` header without `allow-same-origin`. Pages still
render and run scripts, but in an isolated origin. **This changes core behaviour:** opened
from its download link, a page that needs storage or a real origin (for example a qsv Data
Schematic's maps) loses those features. With a content origin, HTML upload downloads redirect
there instead, and work fully. Turn the header off with `ckanext.webpageviewplus.harden_downloads
= false` only if you trust everyone who can upload. Doing so also keeps `external` and
`data-schematic` degraded.


## Deploying a content origin

A content origin serves uploaded HTML from a separate site, such as
`https://example-usercontent.org` for a CKAN at `https://data.example.org` (the way GitHub
uses `githubusercontent.com`). Pages there keep their own origin, so maps, storage and
workers work, and the browser keeps them apart from CKAN's pages, cookies and API.

1. **Register a separate domain.** It must be a different registrable domain, not a
   subdomain: `files.data.example.org` or `files.example.org` is the same site as
   `data.example.org`, and can set cookies CKAN reads. Startup checks this against the Public
   Suffix List and refuses a content origin on the same site as any CKAN host.
2. **Give it DNS and TLS**, and proxy it to the same CKAN. Keep `ckan.site_url` as it is.
   With nginx:

       server {
           listen 443 ssl;
           server_name example-usercontent.org;
           ssl_certificate     /etc/ssl/example-usercontent.org.pem;
           ssl_certificate_key /etc/ssl/example-usercontent.org.key;
           gzip on;
           gzip_types text/html;
           location /webpageviewplus/content/ {
               proxy_pass http://127.0.0.1:5000;   # the same CKAN
               proxy_set_header Host $host;        # required, see below
               proxy_set_header X-Forwarded-Proto $scheme;
           }
           # only needed with connection_reports = true
           location /webpageviewplus/report/ {
               proxy_pass http://127.0.0.1:5000;
               proxy_set_header Host $host;
               proxy_set_header X-Forwarded-Proto $scheme;
           }
           location / { return 404; }
       }

   **The proxy must pass the original `Host` header** (`proxy_set_header Host $host` in
   nginx, `ProxyPreserveHost On` in Apache, and the same in any CDN in front of it). CKAN
   recognises the content domain by that header. Only on it does CKAN answer the content
   route alone and 404 everything else. If the header is rewritten, the whole CKAN site
   answers on the content domain. Check it after deploying: `curl -I
   https://example-usercontent.org/dataset/` must return 404.
3. **Configure CKAN**, with a fresh 32+ character secret (for example from
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`):

       ckanext.webpageviewplus.content_origin = https://example-usercontent.org
       ckanext.webpageviewplus.signing_secret = <secret>
       # optional: how long a private resource's content URL lasts (one to two windows)
       ckanext.webpageviewplus.url_ttl = 300

4. **Restart CKAN.** Views of HTML uploads now load from the content origin, and
   `external`/`data-schematic` stop degrading.

How content is served:

- **Public datasets** get a stable, unsigned URL, sent with an ETag and `Cache-Control: public,
  no-cache`. A CDN in front of the content origin may store it, but checks with CKAN before
  each reuse (a cheap 304 while unchanged). So a dataset made private stops being served at
  once, even through the CDN.
- **Private datasets** get a URL signed with the secret when CKAN renders the view, after its
  normal access check. It stays valid for one to two `url_ttl` windows, and is sent with
  `Cache-Control: private, max-age=<url_ttl>`, so only the viewer's own browser keeps a copy,
  for at most that long. Anyone holding a signed URL can open it until it expires, so keep
  `url_ttl` short. Rotating `signing_secret` invalidates every signed URL for new requests;
  copies already in a viewer's browser last until their `max-age` runs out.
- Responses send `frame-ancestors` (only CKAN's hosts may frame them) and `nosniff`, and never
  set a cookie.
- **Only filesystem uploads are served** for now. With a cloud storage uploader (for example
  ckanext-cloudstorage), uploads stay framed from CKAN's origin under the sandbox.
- **Rollback:** unset `content_origin` and restart. No data changes, and views fall back to the
  sandboxed behaviour.


## Frameability check (optional)

With `ckanext.webpageviewplus.frameability_check = true`, the view form warns as an editor types
a page URL whose site refuses to be framed (`X-Frame-Options`, or a CSP `frame-ancestors` that
excludes your CKAN), and suggests linking to it instead. It never blocks saving.

**It makes your CKAN server request URLs that editors type**, so it's off by default. If you
enable it, the server:

- checks only for users who may add views to that resource;
- fetches only `http(s)` URLs whose host resolves entirely to public addresses, re-checking
  every redirect (at most three);
- reads headers only, times out after 5 seconds, and caches each answer for 5 minutes;
- gives the same "couldn't check" answer for every failure, so it can't be used to probe hosts.

Requests go out through any proxy your environment configures for Python `requests`. One known
limit: a host whose DNS answer changes between the check and the connection (DNS rebinding)
isn't caught, so keep internal services behind authentication as well.


## Privacy and telemetry

By default the extension sends no telemetry and adds no tracking. Every frame's `allow` list
denies ad and tracking features (Topics, Attribution Reporting, FLEDGE), and its
`referrerpolicy` keeps the dataset's URL from third-party pages.

- **Consent card.** A third-party page can wait behind a card ("This view loads content from
  *host*") until the reader asks, so nothing is requested from that host before. It's on with
  `consent_gate` (for the site, or per view), with `privacy_profile = eu` for frames running as
  `external`, and whenever the browser sends Global Privacy Control (`Sec-GPC: 1`). Uploads and
  CKAN's own pages never get it. View pages send `Vary: Save-Data, Sec-GPC`.
- **For a site's own analytics,** each view fires DOM events that bubble from its wrapper:
  `webpageviewplus:loaded`, `webpageviewplus:expanded` and `webpageviewplus:failed` (a load the
  view started, Load or Reload, that took over 15 s). The extension sends them nowhere.
- **Server logs** for admins, on the `ckanext.webpageviewplus.events` logger, with structured
  fields and nothing about the visitor: `view_rendered` (preset, notices),
  `same_origin_refused`, `signature_refused` and `frameability_blocked`.

### Opt-in view counting

Off unless a sysadmin sets `view_counting = true` (which requires an http(s)
`privacy_policy_url`), and then only for views whose editors tick "Count visits". A counted view
says "This view counts visits", linking to the privacy policy.

- **What's counted:** impressions when the view is rendered, then one beacon per page load
  saying whether the view was seen (half on screen for a second), loaded, used (Load, the bar's
  buttons, tapping through) or failed. Only per-view daily totals are stored: no IP, user, user
  agent or referrer. Browsers sending Global Privacy Control, and obvious bots, aren't counted.
- **Where to see them:** the view's edit form (last 30 days), the
  `webpageviewplus_view_stats_show` action (for anyone who can edit the resource), and
  `ckan webpageviewplus stats-summary`.
- **Retention:** run `ckan webpageviewplus purge-stats` daily from cron; it deletes totals
  older than `view_counting_retention_days` (365).
- Anyone can post to the beacon endpoint, so totals can be inflated: treat them as indicative.

### What uploaded pages contact

With `connection_reports = true` (off by default: each report request is a database write) and
a content origin, uploaded pages are served with a `Content-Security-Policy-Report-Only`
header, which never blocks anything. Browsers report each fetch from another host (map tiles, a
CDN) back to the content origin, and CKAN keeps just the hosts per resource. Read them with the
`webpageviewplus_connections_show` action or `ckan webpageviewplus connections`;
`show_connections = true` also lists them under the view as "This page connects to". Anyone can
post a report, so the list is a hint, not proof. Reports are posted to
`/webpageviewplus/report/` on the content origin, so its proxy must pass that path to CKAN
too (see the nginx example above).


## Migrating from webpage_view

`ckan webpageviewplus migrate` converts `webpage_view` views in place, keeping their id, title,
description, order and page URL. It picks `data-schematic` for uploads that contain a qsv
schematic, and the default preset otherwise. It never stores a preset the site disallows or
the rule above refuses: if none fits, it skips the view and says so. Back up the database
first, and start with a dry run:

    ckan -c /etc/ckan/default/ckan.ini webpageviewplus migrate --dry-run
    ckan -c /etc/ckan/default/ckan.ini webpageviewplus migrate [--uploads-only] [--preset NAME]

**Without a content origin, if your site embeds third-party apps or dashboards, use
`--uploads-only`.** Stock `webpage_view` runs embedded pages unsandboxed, and a migrated
third-party app (running as `interactive`) would lose its own logins and saved settings.
`--uploads-only` converts just the views of uploaded HTML, which gain the most. Linked pages
stay on `webpage_view` until you set up a content origin.


## Config settings

All optional. See `ckanext/webpageviewplus/config_declaration.yaml` for details.

| Key | Default | Meaning |
| --- | ------- | ------- |
| `ckanext.webpageviewplus.content_origin` | empty (off) | Origin serving uploaded HTML, a separate site from CKAN's; see above |
| `ckanext.webpageviewplus.signing_secret` | empty | HMAC key (32+ characters) for private content URLs; required with `content_origin` |
| `ckanext.webpageviewplus.url_ttl` | `300` | Signing window for private content URLs, in seconds (minimum 30) |
| `ckanext.webpageviewplus.harden_downloads` | `true` | Sandbox HTML/SVG/XML that CKAN serves from its own origin (downloads, resource proxy) |
| `ckanext.webpageviewplus.default_preset` | `interactive` | Preset for new views |
| `ckanext.webpageviewplus.presets` | all four | Presets editors may choose from |
| `ckanext.webpageviewplus.editor_can_loosen` | `false` | Let editors (not only sysadmins) set sandbox, allow and referrer policy |
| `ckanext.webpageviewplus.referrer_policy` | `strict-origin-when-cross-origin` | Default `referrerpolicy` |
| `ckanext.webpageviewplus.credentialless` | empty (off) | Presets whose frames of third-party pages load without the visitor's cookies or storage (Chromium only, extra hardening; never uploads or CKAN's own pages). Breaks logins and sign-in popups in those frames |
| `ckanext.webpageviewplus.min_height` / `max_height` | `400` / `4000` | Clamp for viewport and auto heights, in px |
| `ckanext.webpageviewplus.site_aliases` | empty | Other hosts this CKAN is reachable under; treated as CKAN's own |
| `ckanext.webpageviewplus.allowed_hosts` | empty (any) | Allow-list of hosts an external page URL may point to |
| `ckanext.webpageviewplus.formats` | `html htm` | Resource formats the view is offered for |
| `ckanext.webpageviewplus.controls` | `open fullscreen reload share` | Buttons in the control bar; empty hides it |
| `ckanext.webpageviewplus.frameability_check` | `false` | Warn editors as they type a page URL that refuses framing. Makes CKAN request editor-typed URLs, see above |
| `ckanext.webpageviewplus.color_scheme` | `auto` | `light` or `dark`: the colour scheme framed pages see (Firefox only so far) |
| `ckanext.webpageviewplus.narrow_width` | `576` | View width, in px, below which aspect and viewport heights become 80% of the screen; `0` turns it off |
| `ckanext.webpageviewplus.privacy_profile` | `default` | `eu` puts the consent card on frames running as `external` |
| `ckanext.webpageviewplus.consent_gate` | `false` | Consent card on every third-party view (editors can also turn it on per view) |
| `ckanext.webpageviewplus.view_counting` | `false` | Let editors turn on view counting per view |
| `ckanext.webpageviewplus.view_counting_retention_days` | `365` | Days of daily totals (and reported hosts) `purge-stats` keeps |
| `ckanext.webpageviewplus.privacy_policy_url` | empty | Linked from "This view counts visits"; required with `view_counting` |
| `ckanext.webpageviewplus.connection_reports` | `false` | Report which hosts uploaded pages contact (needs a content origin) |
| `ckanext.webpageviewplus.show_connections` | `false` | Show those hosts under the view |


## Commands and API

| Command | Does |
| --- | --- |
| `ckan webpageviewplus migrate [--dry-run] [--uploads-only] [--preset NAME]` | Convert `webpage_view` views, see above |
| `ckan webpageviewplus purge-stats` | Delete view counts and reported hosts older than the retention period (cron) |
| `ckan webpageviewplus stats-summary [--limit N]` | Site-wide view counts, busiest first |
| `ckan webpageviewplus connections [--limit N]` | Hosts uploaded pages were seen contacting, site-wide |

API actions: `webpageviewplus_frameability` (`url`, `resource_id`; for those who may add views),
`webpageviewplus_view_stats_show` (`id` of a view, optional `days`) and
`webpageviewplus_connections_show` (`id` of a resource), both for those who may update the
resource.


## Developer installation

To install ckanext-webpageviewplus for development, activate your CKAN virtualenv and
do:

    git clone https://github.com/dathere/ckanext-webpageviewplus.git
    cd ckanext-webpageviewplus
    pip install -e .
    pip install -r dev-requirements.txt


## Tests

To run the tests, do:

    pytest --ckan-ini=test.ini

`test.ini` inherits from `/srv/app/src/ckan/test-core.ini`, the path in the
`ckan/ckan-dev` Docker images used by CI. Elsewhere, edit its `use = config:` line
to point at your CKAN checkout's `test-core.ini`. The tests create the plugin's tables
themselves (CKAN's fixtures drop them when they reset the test database).

`browser_tests/` drives a running CKAN with Playwright in Chromium, Firefox and WebKit; see
`.github/workflows/test.yml` for how CI starts and seeds one.


## Releasing a new version of ckanext-webpageviewplus

If ckanext-webpageviewplus should be available on PyPI you can follow these steps to publish a new version:

1. Update the version number in the `pyproject.toml` file. See [PEP 440](http://legacy.python.org/dev/peps/pep-0440/#public-version-identifiers) for how to choose version numbers.

2. Make sure you have the latest version of necessary packages:

    pip install --upgrade setuptools wheel twine

3. Create a source and binary distributions of the new version:

       python -m build && twine check dist/*

   Fix any errors you get.

4. Upload the source distribution to PyPI:

       twine upload dist/*

5. Commit any outstanding changes:

       git commit -a
       git push

6. Tag the new release of the project on GitHub with the version number from
   `pyproject.toml`. For example if the version number is 0.1.0 then do:

       git tag 0.1.0
       git push --tags

Before releasing, refresh the translation template (`python setup.py extract_messages`,
which writes `ckanext/webpageviewplus/i18n/ckanext-webpageviewplus.pot`) and add new entries
to `CHANGELOG.md`.

## License

[AGPL](https://www.gnu.org/licenses/agpl-3.0.en.html)
