# Changelog

## Unreleased

### Added

- The Repository (Plus) view shows public repositories on Codeberg (Forgejo), GitLab
  (nested groups too) and Bitbucket, as well as GitHub, each from its own link and branch
  addresses. Their READMEs come as raw Markdown, rendered by CKAN with cmarkgfm (GitHub's
  cmark-gfm) and cleaned with nh3 as GitHub's are, with heading anchors so in-page links
  work. Stars and forks are left out where a site doesn't count them (Bitbucket). The
  `[repos]` extra now installs cmarkgfm too.
- `ckanext.webpageviewplus.repo_hosts`: self-hosted GitHub Enterprise, Gitea/Forgejo and
  GitLab sites, as `host kind` pairs, checked at startup. They're fetched over https at
  public addresses only, with no token, and redirects are followed only within the site's
  API.
- Each site's rate limit is read from its own headers (GitHub's and GitLab's reset times,
  Codeberg's `RateLimit`, `Retry-After`), so a view waits for the right moment.

### Changed

- Texts naming GitHub alone (the form's help, the save error, the missing-repository and
  rate-limit cards, the consent card's host) now name the repository's own site. New and
  changed strings are translated at release.

### Fixed

- On CKAN 2.12 with a storage that answers downloads with a redirect to its own link
  (`ckan:s3` from ckanext-file-keeper-cloud), an HTML upload's download URL went to that
  link rather than the content origin, so the control bar's Open in new tab and Reload
  left the content origin. They're now sent to it, as with every other storage.

## 0.11.0 (2026-11-04)

### Added

- A third plugin, `repo_view_plus`: the Repository (Plus) view shows a public GitHub
  repository's name, description, facts, root files and README, which GitHub's own pages
  can't be framed to show. CKAN fetches GitHub's API and keeps a copy per repository
  (`repo_cache_seconds`, optional `github_token`); the page has no scripts, so no separate
  domain for uploads is needed. Install with the new `[repos]` extra (nh3). `doctor` notes
  when it's on without `github_token` (GitHub then allows 60 requests an hour, about 20
  repositories). Its strings are translated into all six languages.

- `ckan webpageviewplus doctor` checks a site's setup and says what to fix: no isolating
  content origin (and what that costs), the plugin's tables not upgraded, the extension's
  asset bundles not served by the site, `ckan.views.default_views`, and core `webpage_view`
  views left to migrate. It exits 1 when something is broken.
- The view form warns at once when a page is on a well-known site that doesn't let its pages
  be framed (GitHub, GitLab, Bitbucket, Codeberg, Hugging Face, LinkedIn, X, Facebook,
  Instagram, Reddit, Medium, Stack Overflow, Notion, Dropbox, Data.gov, Google, Power BI's and
  Tableau Public's own sites), so the view would stay blank. No setting or fetch needed.
  Their embed addresses (Facebook's `/plugins/`, LinkedIn's `/embed/`, Instagram's
  `/p/<id>/embed`, Power BI's `reportEmbed`...) aren't flagged, and the embeddable pages some
  of them have (GitHub notebooks, Tableau Public views, Power BI Publish to web, Hugging Face
  Spaces) still get their suggestion.

- PDF (Plus) shows a PDF linked by its GitHub file page (`github.com/.../blob/...`): CKAN
  fetches the file from `raw.githubusercontent.com` (the page itself is HTML), and Open in new
  tab still opens the GitHub page.

### Changed

- The README starts with a Quickstart: install, tables, `doctor`, then the optional steps.
- Every problem the view form names for a well-known service's page (preset not allowed,
  host not allowed, private dataset, no content origin, refuses framing) is now rendered by
  the server too, so it shows even when the form's script doesn't load.

### Fixed

- In a private dataset, a view of a link to an Office file itself (`.docx`, `.xlsx`, ...) was
  refused on save and shown as "can't open files in a private dataset", as if it went through
  Microsoft's viewer. Only views of the viewer's own address are refused now.

## 0.10.3 (2026-11-03)

### Changed

- The view form now explains why a well-known service such as YouTube won't play until the
  site has a content origin even when the form's script doesn't load (for example when the
  site's asset bundles fail to build). Before, nothing was shown.

## 0.10.2 (2026-11-03)

### Security

- `harden_downloads` could still be bypassed with a mimetype like
  `text/plain; charset=UTF-8, text/html`: the type was judged by what came before the first
  `;`, but browsers split the header on commas and take the last type. Any comma in the
  Content-Type now gets the sandbox. Upgrading is enough.
- Thumbnails of public datasets are no longer cached for a day: they're sent `public, no-cache`
  with an ETag, so a thumbnail of a dataset made private, or of a removed image, isn't served
  from browsers or a CDN. A repeat visit costs a 304 per thumbnail.
- The Office viewer check for private datasets also covers a view with no page URL whose
  resource's own address is the viewer's.

### Fixed

- External is offered again in the form for uploaded resources. 0.10.0 hid it, which broke
  Microsoft's Office viewer for uploaded documents (its page is on Microsoft's site) and could
  silently change an existing External view's preset on save. External for the upload itself
  is still refused at save.
- A database error while re-saving a view no longer deletes its thumbnail.
- A page that answers 404 or 410 and also refuses framing shows "This page is gone".

## 0.10.1 (2026-11-03)

### Changed

- 0.10.0's two new save refusals (an `http://` page address on an `https://` site, and
  Microsoft's Office viewer in a private dataset) now apply only when a view is created or its
  page address changes. A view already saved that way keeps showing its card, and editors can
  still change its title or other settings without first fixing the address.

## 0.10.0 (2026-11-02)

### Security

- `harden_downloads` could be bypassed by an editor. CKAN sends a resource's mimetype, which
  editors set, as its download's Content-Type, and the sandbox was only added for exactly
  HTML, SVG or XML types. Browsers run a download typed `text/plain, text/html` as HTML, and
  guess HTML for `unknown/unknown`, `html` or a blank type, so such an upload ran on CKAN's own
  address with the visitor's login. Every site with `harden_downloads` on (the default) was
  exposed, through resource downloads and the resource proxy. Now every such response gets
  `nosniff`, and the sandbox goes on everything except one well-formed type a browser never
  runs scripts in (images but not SVG, audio, video, fonts, plain text, CSV, TSV, JSON, PDF,
  zip, gzip, `application/octet-stream`, `application/vnd.*`). `check-views` judges uploads'
  mimetypes the same way. Upgrading is enough; no setting changes.
- The thumbnails extra needs Pillow 10.3 or newer: thumbnails decode images from other sites,
  and older Pillow has known decoder holes (libwebp's CVE-2023-4863 before 10.0.1).
- A page URL on the content origin (an upload's content address, pasted) counts as an upload,
  so the `external` preset can't frame it without a sandbox.
- The fetch guard judges NAT64 addresses (`64:ff9b::/96`) by the IPv4 address inside, so a
  host resolving to one can't reach a private or link-local address on networks with NAT64.
- View counts are only taken from people who may see the view: a private dataset's views
  can't be counted by strangers posting to the beacon.

### Added

- A view whose page answered 404 or 410 when last checked shows "This page is gone" instead of
  the other site's own error page (needs `frameability_check`, as for pages that refuse
  framing). The form's preview shows the same card.
- A view of Microsoft's Office viewer in a private dataset shows a card saying the viewer can't
  open private files, and saving one there is refused.
- The Website (Plus) form points PDF resources to PDF (Plus), where that view is offered. The
  PDF (Plus) form says how large a linked PDF may be.
- `ckan webpageviewplus migrate --dataset NAME` migrates one dataset's views at a time.
- A content-origin request that reaches CKAN's own host is logged
  (`content_request_on_ckan_host`): the sign of a proxy rewriting `Host`, which leaves upload
  views empty.

### Changed

- Saving a view whose page address is `http://` on an `https://` site is refused, with a hint to
  use its `https://` address: browsers would block it. Default views skip such resources.
- Heights as people type them: "600px" and "80%" are accepted, and a value given with the
  preset's default height picks its mode (a number Fixed, 16/9 an aspect ratio, 80% a share of
  the window) instead of being ignored. The error says what the chosen mode takes.
- Editor-facing messages say "a separate domain for uploads" and what a setting would allow
  ("use visitors' logins"), not "content origin" or "real origin", and suggest asking the
  site administrator. The form's placeholder shows an `https://` address. The thumbnail opt-out
  is labelled "Don't make a thumbnail automatically", which is what it does. The embeddability
  warning no longer quotes the refusing header (it's in a tooltip).
- External isn't offered for uploads in the form (it was refused at save).
- The form's embeddability check also checks the resource's own link when the page URL is
  blank, like the service suggestion.
- Thumbnails of public datasets are cached by browsers for a day (`immutable`): their address
  names the copy, so a dataset page no longer costs a request per thumbnail on every visit.
- `view_rendered` is logged at DEBUG: there's one per page view.
- `service_format` leaves harvested datasets alone: the next harvest would set the format back.
- New translated strings in all six languages.

### Fixed

- Linked PDFs (PDF (Plus)): a failed fetch is remembered for a minute, and only one worker
  fetches a link at a time; the others serve the copy being refreshed instead of waiting. A
  broken or slow link could otherwise keep workers busy with one fetch per request.
- The view form's embeddability check (`frameability_check`) now has an overall deadline, so
  a page that sends its headers a byte at a time can't hold a worker.
- Connection reports (`connection_reports`) are only accepted for uploads the content origin
  serves, and a resource keeps at most 200 hosts, so posting reports can't grow the table
  without limit.
- Re-saving a view no longer drops its thumbnail when the image's site is down for a moment;
  the copy stays until its source changes.
- Database errors no longer break pages: a deleted view, a read-only replica or a busy database
  while counting or reading stored checks, thumbnails or reported hosts is logged, and the page
  renders. View stats before `ckan db upgrade -p webpage_view_plus` answer with no counts.
- The embeddability action refuses (403) a request without `resource_id`, instead of a 404.
- The PDF (Plus) first-page message says to edit the view again after saving, which is when
  the page is drawn.
- Docs: removing the extension (`ckan views clean` deletes its views; `ckan db downgrade -p`),
  linked PDFs' copies on each server's disk, CDNs that ignore `Vary`, and backfilling views on
  large or harvested sites.

## 0.9.0 (2026-11-02)

### Added

- Views made automatically for recognised services' links. With `webpage_view_plus` in CKAN's
  `ckan.views.default_views`, a resource whose link is a recognised service's page (a YouTube
  watch link, say) gets a Website (Plus) view with the settings the form would suggest, and the
  service's name as its title. Only where those settings work on the site: services needing
  `external` wait for a content origin, `allowed_hosts` applies, and uploads never get one.
  `ckanext.webpageviewplus.default_view_services` picks the services by key (`youtube`,
  `tableau-public`...); by default every service except Microsoft Office and Jupyter, which
  match any linked Office or `.ipynb` file and whose views send its address to Microsoft or
  nbviewer (Office, if listed, still needs a public dataset).
  `ckan views create webpage_view_plus` adds them to existing resources.

### Changed

- `ckanext.webpageviewplus.formats` now defaults to empty, so default views go only to
  recognised services' links. Its formats only ever decided which resources CKAN's default
  views reach (the view is offered on every resource). Sites that list `webpage_view_plus` in
  `ckan.views.default_views` and want plain web pages to get one too: set it to `html htm`.
- Thumbnails in the resource list (not service logos) get a thin border on a light grey
  backing, so a white page, such as a PDF's first page, shows where it ends.

### Fixed

- The view forms hold up under site themes that restyle CKAN's form classes: checkboxes no
  longer stretch into wide empty bars (a theme sizing every input in `.control-medium`), and
  selects keep their arrow when a theme resets `.form-control`'s background.

## 0.8.0 (2026-11-01)

### Added

- A second view type, **PDF (Plus)** (plugin `pdf_view_plus`, after `webpage_view_plus`), on
  sites with a content origin. It shows PDFs with PDF.js 6.5.438 (its legacy build, for
  Chrome and Edge 125+, Firefox ESR and Safari 18+), which ships with the extension and is served from the content origin only, in the `data-schematic` sandbox. The
  same viewer runs in every browser, phones included, where ckanext-pdfview's browser viewer
  shows nothing; Chrome refuses its own viewer in any sandbox. Uploaded PDFs come from CKAN's
  storage, with signed URLs for private datasets. PDFs on other sites (the view's PDF url, or
  the resource's own link) are fetched by CKAN with the frameability check's guards, checked
  to be PDFs, capped at `pdf_max_size` MB (default 25) and kept under `ckan.storage_path` for
  `pdf_cache_seconds` (default an hour), so visitors never contact the other site and a busy
  view costs one fetch an hour. A PDF's own JavaScript never runs, the viewer is read-only, and
  its Download and Print buttons give way to the control bar, and a PDF's links open in a new
  tab. Reload and Load go through a CKAN
  route that re-checks access and signs a fresh viewer URL. The viewer follows the page's
  language: English plus the ten languages CKAN portals use most (Spanish, Brazilian
  Portuguese, Thai, German, Italian, French, Japanese, Indonesian, Ukrainian, Turkish). Proxies
  must forward `/webpageviewplus/pdfjs/` and `/webpageviewplus/pdf/` on the content domain.
  `scripts/vendor_pdfjs.py` updates the vendored copy.
- Thumbnails (`webpage_view_plus_thumbnails`): PDF (Plus) views take an image the editor picks,
  or the PDF's first page, drawn by PDF.js in the editor's browser when it's chosen in the view
  form and sent to the new `webpageviewplus_pdf_thumbnail` action (for those who may edit the
  view), which re-encodes it and keeps it in Redis until the view is saved (a cancelled edit
  keeps the old thumbnail); CKAN never renders PDFs. `check-views` remakes PDF (Plus) views'
  thumbnails too. For an upload it's tied to the file's
  last change, and the form redraws it once the file has changed; a linked PDF's is redrawn
  whenever the form is opened. A Website (Plus) view of an Office file shows the preview picture inside the file
  (always in OpenDocument; in `.docx`, `.xlsx`, `.pptx` and `.ppsx` when saved with one). An
  upload to the dataset is read from storage with no setting needed; a file elsewhere is
  fetched only with `thumbnail_fetch`, at most 20 MB.
- `ckan webpageviewplus migrate-pdf [--dry-run]` converts ckanext-pdfview's `pdf_view` views to
  PDF (Plus) in place, keeping id, title, description, order and PDF url. It needs
  `pdf_view_plus` and a content origin, and skips views whose PDF url isn't http(s), whose
  host isn't in `allowed_hosts`, or whose upload it can't read: on CKAN 2.10/2.11, files kept by
  a cloud storage extension, for which PDF (Plus) isn't offered either. Such sites keep
  ckanext-pdfview for uploads until CKAN 2.12.
- OpenDocument files (`.odt`, `.ods`, `.odp`) join the Microsoft Office service: the form
  suggests Microsoft's viewer for them, with `external`, and presentations get a 16/9 height.
  0.7.0 refused them because the sample files it was tested with fail to open. Others render
  (checked in Chrome under `external`, clicking and scrolling in), though the viewer still
  refuses some files.

## 0.7.0 (2026-11-01)

### Added

- On CKAN 2.12, the content origin serves uploaded HTML kept in cloud storage too: any
  `ckan.files` storage that can stream its files (S3, Google Cloud Storage, Azure and others,
  through file-keeper). CKAN streams the file, so the content origin's rules hold whatever the
  storage. Views of these uploads, including qsv Data Schematics, now run with their own origin
  (maps, saved data) instead of sandboxed on CKAN's address. The storage's hash is the ETag, so
  an unchanged page revalidates with a 304 without being read again. Picked thumbnails and
  `migrate`'s schematic check read them too.
- A 30th well-known service: Word, Excel and PowerPoint files (`.doc`, `.docx`, `.xls`, `.xlsx`,
  `.ppt`, `.pptx`, `.ppsx`) shown through Microsoft's online viewer, with `external` (checked in
  Chrome: under `interactive` its pages fail as soon as you scroll). Paste a public file's
  address, or Microsoft's `view.aspx` address, and the form suggests the viewer; with no page
  URL it suggests from the resource's own address, for any service. The form says a file in a
  private dataset can't be shown, as Microsoft fetches it. Sites offer the view on Office
  resources by adding their formats to `ckanext.webpageviewplus.formats`. OpenDocument files
  aren't supported: neither Microsoft's viewer nor Google's opens them.

### Changed

- The README says plainly that on CKAN 2.10 and 2.11, uploads kept by a cloud storage
  extension (ckanext-s3filestore, ckanext-cloudstorage) stay sandboxed on CKAN's address, and
  that upgrading to 2.12 lifts it.
- The demo GIF's montage has 30 services, ending with a PowerPoint file in Microsoft's viewer.

### Docs

- An FAQ in the README, for site administrators and for editors: plain-language answers, each
  linking to the section with the detail.
- A screenshot of thumbnails in the resource list, and a Codacy coverage badge.
- Browser tests for self-sizing (the generic resize message, clamping, malformed heights,
  window resizes) and for thumbnails in the resource list, in Chromium, Firefox and WebKit.

## 0.6.0 (2026-10-31)

### Added

- Thumbnails in the resource list, a second plugin (`webpage_view_plus_thumbnails`) needing
  Pillow (`pip install ckanext-webpageviewplus[thumbnails]`). A resource with a Website (Plus)
  view shows an image an editor picked from the dataset's uploaded images (stored small, in a
  new table: run `ckan db upgrade -p webpage_view_plus`), or, when its only view is a
  well-known service's, that service's logo (from Simple Icons, for 20 of the 29 services) and name.
- With `ckanext.webpageviewplus.thumbnail_fetch = true` (off by default), thumbnails can also
  come from the page's own preview image (`og:image`, then `twitter:image`) and from linked
  images an editor picks, fetched once when the view is saved, with the frameability check's
  guards. Editors can opt a view out ("Don't use the page's own preview image").
  `ckan webpageviewplus check-views` remakes thumbnails too, keeping a copy when remaking it fails.
- With `thumbnail_fetch` on, YouTube, Datawrapper, Flourish, Tableau Public and Internet Archive
  views take the thumbnail each service publishes at a fixed address.
- `ckanext.webpageviewplus.service_format` (off by default): saving a view on a resource whose
  only view is a well-known service's sets an empty or `HTML` format to the service's name.

### Changed

- The frameability check (and the new thumbnail fetch) checks each connection's address once
  it's open, before sending anything, so a host whose DNS answer changes to an internal
  address after the check (DNS rebinding) is refused. Through an egress proxy, the proxy does
  the lookup.
- The demo GIF shows thumbnails in the resource lists of the feature tour and the qsv Data
  Schematics gallery.

## 0.5.0 (2026-10-30)

### Added

- CKAN 2.12 support. Its new file storage (`ckan.files`) is recognised: uploads in a
  filesystem storage (`ckan:fs`) are served from the content origin, like uploads in CKAN's
  upload folder before, and `ckan webpageviewplus migrate` finds qsv schematics in them; other
  storages stay sandboxed on CKAN's origin. CI runs the tests and the browser tests on 2.12
  too.
- Pasted static Hugging Face Space addresses (`<name>.static.hf.space`) are recognised as
  Hugging Face Spaces (`external`).

### Changed

- The demo GIF's ArcGIS Online, Hugging Face and Internet Archive clips use clearer examples.

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
