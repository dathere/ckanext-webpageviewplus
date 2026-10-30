"""Demo dataset `wvp-features`: one resource per feature of webpage_view_plus,
each view described as a short story of what to look for. Run inside the
CKAN container, where the repo is mounted:

    python /srv/app/src_extensions/ckanext-webpageviewplus/demo/seed_features.py

Idempotent: the dataset is purged and recreated. Actions run as the site user
with ignore_auth, so no API token is needed. Some features depend on site
settings (a content origin, frameability_check); their descriptions say so.
"""
import io
import os
import sys

from werkzeug.datastructures import FileStorage

from ckan.cli import load_config
from ckan.config.middleware import make_app
import ckan.plugins.toolkit as tk

from ckanext.webpageviewplus import services

DATASET = "wvp-features"
ORG = "wvp-demo-org"
FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "ckanext", "webpageviewplus", "tests", "fixtures", "qsv_protocol.html",
)

NOTES = """\
A tour of webpage_view_plus, one resource per feature. Open each resource and
read the view's description: it says what to look for.

Also try:

- The page in another language: put `/de`, `/es`, `/fr`, `/it`, `/ja` or
  `/pt_BR` before `/dataset` in the address.
- A phone, or your browser's device mode: tall views get "Tap to interact" so
  the page still scrolls, and Fullscreen becomes Expand on iPhone.
- `ckan webpageviewplus check-views --dataset wvp-features` in the container:
  it reports the page that refuses framing and the one that's gone.
"""

# (resource name, resource url or None for the upload, views)
# Each view: (title, fields, description). Descriptions are Markdown.
RESOURCES = [
    ("Uploaded HTML on the content origin", None, [
        ("Data Schematic preset, self-sizing",
         {"preset": "data-schematic", "height_mode": "auto"},
         "An uploaded HTML page, the kind that core CKAN would serve from its own "
         "address, where its scripts could act with your login. Here it's served from "
         "the content origin instead. The page prints the origin it runs on: the "
         "content origin, not CKAN's, so its scripts can't act as you. It also reports its height, so the frame fits it exactly. Without "
         "`content_origin` set, it still runs, sandboxed with no origin at all."),
        ("Strict preset: no scripts",
         {"preset": "strict"},
         "The same upload with scripts switched off entirely. What's left is the "
         "page's static HTML: no origin printed, no resizing, nothing that runs. "
         "`strict` suits documents that don't need JavaScript."),
    ]),
    ("Wikipedia: Open data", "https://en.wikipedia.org/wiki/Open_data", [
        ("Strict preset, 70% of the window",
         {"preset": "strict", "height_mode": "viewport", "height_value": "70"},
         "A third-party page that reads fine without JavaScript, so it gets the "
         "tightest sandbox: no scripts, no forms, no popups. The view takes 70% of the "
         "window. The bar below it opens the article in a new tab, or reloads it."),
        ("Same, without the control bar",
         {"preset": "strict", "height_mode": "viewport", "height_value": "70",
          "hide_controls": True},
         "The bar is hidden for this view. On a phone, or at 400% zoom, Open in new "
         "tab still appears, so nobody is left stuck inside a small frame."),
    ]),
    ("Datawrapper chart", "https://www.datawrapper.de/_/XSj9X/", [
        ("Interactive preset, fixed 500 px",
         {"page_url": "https://datawrapper.dwcdn.net/XSj9X/", "preset": "interactive",
          "height_mode": "fixed", "height_value": "500"},
         "A chart from Datawrapper. Its scripts run so the tooltips work, but the "
         "frame has no origin of its own: it can't read cookies, storage, or anything "
         "on this page. The resource links to the chart's page; the view frames its "
         "embed address, which is what the view form offers when you paste the link."),
    ]),
    ("YouTube video", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", [
        ("External preset, 16:9",
         {"page_url": "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
          "preset": "external", "height_mode": "aspect", "height_value": "16/9"},
         "You know the song. It is never going to give you up, and it is never going "
         "to run as CKAN either. "
         "YouTube needs its own origin to play, so this view uses `external`: no "
         "sandbox. That's safe only because uploads live on a separate content origin; "
         "without one, the view falls back to `interactive` and the player shows an "
         "error. It frames the privacy-enhanced youtube-nocookie.com player and keeps "
         "a 16:9 shape at any width."),
        ("Ask before loading",
         {"page_url": "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
          "preset": "external", "height_mode": "aspect", "height_value": "16/9",
          "consent_gate": True},
         "The same video behind a consent card: nothing loads from YouTube, and it "
         "learns nothing about you, until you choose Load. Sites can require this for "
         "every third-party page, and it appears on its own for visitors whose browser "
         "sends Global Privacy Control."),
    ]),
    ("Try the view form", "https://vimeo.com/76979871", [
        ("Paste a link to see a suggestion",
         {"preset": "interactive"},
         "This view frames a Vimeo page as pasted, which doesn't play: Vimeo's player "
         "lives at another address. Log in, open Manage, then Views, edit this view "
         "and paste https://vimeo.com/76979871 into the page URL. The form recognises "
         "Vimeo and offers its player address, the preset it needs and a 16:9 height: "
         "choose Use them and save."),
    ]),
    ("A page that refuses to be framed", "https://github.com/ckan/ckan", [
        ("Interactive preset",
         {"preset": "interactive"},
         "GitHub tells browsers never to show it inside a frame. Core CKAN would show "
         "a blank box here. With `frameability_check` on, saving the view checks the "
         "page once, and visitors see a card explaining why it can't be shown, with "
         "Open in new tab, instead of a blank frame. (With the setting off, as on a "
         "fresh dev site, you see the blank frame; turn it on and save the view.)"),
    ]),
    ("A page that's gone", "https://example.com/this-page-is-gone", [
        ("Interactive preset",
         {"preset": "interactive"},
         "Links rot. This one returns 404. A frame can't tell you that, but "
         "`ckan webpageviewplus check-views` can: run it from cron and it lists every "
         "view whose page is gone, unreachable or refuses framing, core "
         "`webpage_view` views included, and exits with status 1 when it finds any."),
    ]),
]


# Google Forms and Typeform: the extension's own feedback form and community
# survey, so anyone viewing the dataset can answer them. Point them at your
# own with these variables (an empty value skips that resource).
GOOGLE_FORM = os.environ.get(
    "WVP_DEMO_GOOGLE_FORM",
    "https://docs.google.com/forms/d/e/1FAIpQLSfSAqH6bNERmm7oc0LrGB6oaSpiOLxUgJN_u1l7luBXT7dyFA/viewform")
TYPEFORM = os.environ.get("WVP_DEMO_TYPEFORM", "https://form.typeform.com/to/zdKXe3nl")

# Well-known services, one resource each (YouTube and Datawrapper are above).
# The resource URL is the page link an editor would paste; the view takes the
# embed address, preset and height the view form would suggest for it.
SERVICE_PAGES = [
    ("Vimeo", "https://vimeo.com/76979871",
     "Vimeo's player needs its own origin to play, so the form suggests `external` and "
     "a 16:9 shape. The resource links to the video's page; the view frames the player "
     "address the form worked out from it."),
    ("Flourish", "https://public.flourish.studio/visualisation/11597006/",
     "A Flourish visualisation runs in `interactive`: its scripts animate and answer the "
     "mouse, but the frame has no origin, so it can't read anything of yours."),
    ("Observable", "https://observablehq.com/@observablehq/plot-vertical-bar-chart",
     "A notebook from the Observable Plot gallery. Observable's runtime needs its own "
     "origin, so this one is `external`, which a site gets only with a content origin."),
    ("Tableau Public", "https://public.tableau.com/views/RegionalSampleWorkbook/Storms",
     "Tableau's sample storms workbook. Tableau Public needs its own origin to run, so "
     "`external` at 800 px; hover the map for each storm's track."),
    ("Power BI", "https://app.powerbi.com/view?r=eyJrIjoiYjRlZjg5YzItOWM4Ni00MGNkLWI1OGYtZDA4MDAxNGYyNDQyIiwidCI6ImQ2NDk2NzM4LWY5MTItNGExZS04NDE1LTQwY2E2ZjRhOTRlZCJ9",
     "A report Ecuador's public procurement agency (SERCOP) published to the web. It runs "
     "sandboxed in `interactive`: the filters work, no Microsoft login is involved, and "
     "the report can't touch this page."),
    ("ArcGIS StoryMaps", "https://storymaps.arcgis.com/stories/cb37f51514e04b389cdbb443f000992b",
     "Five Esri cartographers on how they mapped the 2020 US Census. Without its own "
     "origin a story shows its text but none of its maps, so StoryMaps is `external`. "
     "Scroll inside the frame to move through it."),
    ("CARTO", "https://clausa.app.carto.com/map/ef3f50e6-b954-45ac-94f1-9325a6a9cb20",
     "Global photovoltaic power, a public Builder map from CARTO's gallery. It runs in "
     "`interactive`, widgets and all; hold Cmd or Ctrl and scroll to zoom."),
    ("Plotly Cloud", "https://oecd-pension-explorer.plotly.app/",
     "A Dash app on Plotly Cloud, where Plotly Studio publishes too. Some Dash apps run "
     "without an origin and some don't, so they all get `external`."),
    ("Google Forms", GOOGLE_FORM,
     "The extension's feedback form, answered right here. Without its own origin a "
     "Google Form shows but can't be filled in, so it's `external`. Google sees who you "
     "are if you're signed in."),
    ("Google Colab", "https://colab.research.google.com/github/CamDavidsonPilon/"
     "Probabilistic-Programming-and-Bayesian-Methods-for-Hackers/blob/master/"
     "Chapter1_Introduction/Ch1_Introduction_PyMC3.ipynb",
     "A Colab link to a notebook on GitHub. Colab refuses to be framed at all, so the "
     "form suggests the same notebook through nbviewer instead: read-only, with its "
     "plots and equations, in `interactive` (MathJax needs scripts, nothing needs an "
     "origin). Notebooks kept in Google Drive can't be shown this way."),
    ("Jupyter", "https://jakevdp.github.io/downloads/notebooks/XKCD_plots.ipynb",
     "Any public notebook file, here one on a personal site, rendered by nbviewer: "
     "matplotlib in the style of xkcd. GitHub, Gist and raw links work the same way."),
    ("Typeform", TYPEFORM,
     "The extension's community survey. Typeform never loads without its own origin, so "
     "it's `external`, unlike Google Forms."),
    ("ArcGIS Online", "https://www.arcgis.com/apps/dashboards/94054353efa943b781cc5d6176ca8283",
     "Esri's dashboard of severe weather reports across the USA, updated through the day. "
     "Dashboards can run sandboxed, but web maps and Instant Apps can't, so everything "
     "from ArcGIS Online gets `external`."),
    ("Looker Studio", "https://lookerstudio.google.com/reporting/"
     "e1efb265-1581-4bfe-961c-e7c3b1795208/page/kIV1C",
     "iDigBio's public web analytics report. Looker Studio shows nothing without its own "
     "origin, so `external`, and only reports whose owner allowed embedding work at all."),
    ("Google Sheets", "https://docs.google.com/spreadsheets/d/e/2PACX-1vS4CmyhVsSYtVtK8MwyZ6Wqkc"
     "Ef1oxCx15zq-dZDitvCZt7alqYBy-ydnxJiApR-gD7oLsDY4s4V_sa/pubhtml",
     "A sheet published to the web, with its tabs along the bottom. Published Google files "
     "run in `interactive`; links to a file's editor need a Google login and aren't offered."),
    ("Google Docs", "https://docs.google.com/document/d/e/2PACX-1vSG44o2kP9YalCpqd4Kx3Q-WpgS6mzg"
     "rF6ZMAh71KFIk2t_PNJCtx7yePvuVnfC0r7H1WyjaXDWL_jo/pub",
     "A user manual written in Google Docs and published to the web, in `interactive`."),
    ("Google Slides", "https://docs.google.com/presentation/d/e/2PACX-1vSvEbWoZ7v3W0nEsckVmNsSj2"
     "65C68s5YuwwD5K9EKISBGnQW8DD0J4MhA2TAbgai4-B00ePB9TLRdP/pub",
     "Malaysia's Ministry of Health published this COVID-19 summary deck. The form swaps "
     "the published link for the deck's player; click the arrows to step through it."),
    ("Our World in Data", "https://ourworldindata.org/grapher/life-expectancy"
     "?tab=chart&country=USA~GBR~JPN",
     "Life expectancy in three countries. The link's query picks the countries and tab, and "
     "the view keeps it. Charts run in `interactive`: hover for each year's values."),
    ("Streamlit", "https://gdp-dashboard-template.streamlit.app/",
     "Streamlit's GDP dashboard template on Community Cloud. Streamlit apps need their own "
     "origin, so `external`; the form adds `?embed=true`, which drops Streamlit's frame."),
    ("Hugging Face Spaces", "https://huggingface.co/spaces/HuggingFaceFW/blogpost-fineweb-v1",
     "The story of FineWeb, a public dataset of web text, told in a Hugging Face Space with "
     "interactive charts. Spaces are often apps that call their own server, so they're all "
     "`external`. The view loads the Space's own address through Hugging Face."),
    ("shinyapps.io", "https://gallery.shinyapps.io/050-kmeans-example/",
     "Posit's k-means example. Shiny apps draw nothing without their own origin, so "
     "`external`; change the variables and the clusters redraw."),
    ("Infogram", "https://infogram.com/10-years-since-lehman-1hkv2nvzgnop6x3",
     "An Infogram slide story. Infogram's embed never loads without its own origin, so "
     "it's `external`."),
    ("Felt", "https://felt.com/map/Global-Human-Settlement-5FzbIDg4RA9B5XnhuPnGgzC?loc=20.1,5.3,2.5z",
     "Built-up land worldwide, on a public Felt map. It runs in `interactive`: zoom and pan "
     "work, though Felt can't keep its own settings without an origin."),
    ("uMap", "https://umap.openstreetmap.fr/en/map/carte-des-reseaux-express-velo-de-france_894719",
     "France's planned express cycle networks on uMap, OpenStreetMap's map maker. uMap's "
     "scripts don't load without an origin, so `external`, with uMap's own embed options."),
    ("OpenStreetMap", "https://www.openstreetmap.org/export/embed.html"
     "?bbox=2.29%2C48.85%2C2.30%2C48.86&layer=mapnik&marker=48.8584%2C2.2945",
     "The Eiffel Tower, from OpenStreetMap's Share dialog. Without its own origin the "
     "map's tiles carry no Referer and OpenStreetMap blocks them, so it's `external`. "
     "Paste the dialog's embed address: a map page's position isn't in its URL."),
    ("Internet Archive", "https://archive.org/details/adventuresoftoms00twaiiala",
     "The Adventures of Tom Sawyer in the Archive's book reader: turn the pages, or open "
     "it full screen. The Archive's readers and players need their own origin, so "
     "`external`; films and audio work the same way."),
    ("Sketchfab", "https://sketchfab.com/3d-models/batmobile-hJ91ZS1d5kqX9N8HLqwfcHVKC9h",
     "A 3D model on Sketchfab. Its viewer refuses to run without its own origin, so "
     "`external`; press play, then drag to turn the model."),
]


def service_resources():
    for name, page, story in SERVICE_PAGES:
        if not page:
            print("skipped (no link): {}".format(name))
            continue
        found = services.suggest(page)
        assert found and found.service == name, (name, page)
        fields = {"page_url": found.embed_url, "preset": found.preset}
        if found.height_mode:
            fields.update(height_mode=found.height_mode, height_value=found.height_value)
        yield (name, page, [("{} ({})".format(name, found.preset), fields, story)])


def main():
    app = make_app(load_config(os.environ["CKAN_INI"]))
    with app._wsgi_app.test_request_context():
        act = tk.get_action
        site_user = act("get_site_user")({"ignore_auth": True}, {})["name"]

        def ctx():
            return {"ignore_auth": True, "user": site_user}

        try:
            act("dataset_purge")(ctx(), {"id": DATASET})
        except tk.ObjectNotFound:
            pass
        try:
            org = act("organization_show")(ctx(), {"id": ORG})
        except tk.ObjectNotFound:
            org = act("organization_create")(ctx(), {"name": ORG})
        pkg = act("package_create")(ctx(), {
            "name": DATASET, "title": "webpageviewplus feature tour",
            "notes": NOTES, "owner_org": org["id"], "private": False,
        })
        site = tk.config.get("ckan.site_url").rstrip("/")
        for name, url, views in RESOURCES + list(service_resources()):
            data = {"package_id": pkg["id"], "name": name, "format": "HTML"}
            if url:
                data["url"] = url
            else:
                with open(FIXTURE, "rb") as f:
                    body = f.read()
                data["url"] = "qsv_protocol.html"
                data["upload"] = FileStorage(
                    io.BytesIO(body), filename="qsv_protocol.html",
                    content_type="text/html")
            res = act("resource_create")(ctx(), data)
            for v in act("resource_view_list")(ctx(), {"id": res["id"]}):
                act("resource_view_delete")(ctx(), {"id": v["id"]})
            print(name)
            for title, fields, description in views:
                view = act("resource_view_create")(ctx(), dict(
                    resource_id=res["id"], view_type="webpage_view_plus",
                    title=title, description=description, **fields))
                print("  {}: {}/dataset/{}/resource/{}?view_id={}".format(
                    title, site, DATASET, res["id"], view["id"]))


if __name__ == "__main__":
    sys.exit(main())
