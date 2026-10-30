import pytest

from ckan.plugins import toolkit
from ckan.plugins.toolkit import url_for
from ckan.tests import factories, helpers

from ckanext.webpageviewplus import frame, services

YT = "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
PBI = "eyJrIjoiYjRlZjg5YzItOWM4Ni00MGNkLWI1OGYtZDA4MDAxNGYyNDQyIiwidCI6ImQ2NDk2NzM4LWY5MTItNGExZS04NDE1LTQwY2E2ZjRhOTRlZCJ9"
STORY = "https://storymaps.arcgis.com/stories/18b624c3fd3b461796341ce3bd2c14a7"
CARTO = "https://clausa.app.carto.com/map/5d942679-411f-4ab7-afb7-0f6061c9af63"
GF_ID = "1FAIpQLSejGC-PLjdLLfHp9M6Ge2bJNbSr1JoWTC0n3RrcvUn1m2VqPw"
GF = "https://docs.google.com/forms/d/e/{}/viewform?embedded=true".format(GF_ID)
NB_PATH = "CamDavidsonPilon/Probabilistic-Programming-and-Bayesian-Methods-for-Hackers/blob/master/Chapter1_Introduction/Ch1_Introduction_PyMC3.ipynb"
NBV = "https://nbviewer.org/github/" + NB_PATH
AG = "02b63130e0ad4462a904215858213ee7"
PUB = "2PACX-1vS4CmyhVsSYtVtK8MwyZ6WqkcEf1oxCx15zq-dZDitvCZt7alqYBy-ydnxJiApR-gD7oLsDY4s4V_sa"
LOOKER = "e1efb265-1581-4bfe-961c-e7c3b1795208"
FELT = "Global-Human-Settlement-5FzbIDg4RA9B5XnhuPnGgzC"
UMAP = "carte-des-reseaux-express-velo-de-france_894719"
UMAP_OPTS = ("scaleControl=false&miniMapControl=false&scrollWheelZoom=false&zoomControl=true"
             "&editMode=disabled&moreControl=true&searchControl=null&tilelayersControl=null"
             "&embedControl=null&datalayersControl=true&onLoadPanel=none&captionBar=false"
             "&captionMenus=true")
OSM = "https://www.openstreetmap.org/export/embed.html?bbox=2.29,48.85,2.30,48.86&layer=mapnik"
SKETCH = "hJ91ZS1d5kqX9N8HLqwfcHVKC9h"


RECOGNISED = [
    "https://youtu.be/dQw4w9WgXcQ", "https://vimeo.com/76979871",
    "https://datawrapper.dwcdn.net/XSj9X/", "https://flo.uri.sh/visualisation/11597006/embed",
    "https://observablehq.com/@d3/bar-chart", "https://public.tableau.com/views/A/B",
    "https://app.powerbi.com/view?r=" + PBI, STORY, CARTO, "https://x.plotly.app/", GF,
    "https://form.typeform.com/to/NPUAWX7u",
    "https://www.arcgis.com/apps/dashboards/" + AG,
    "https://lookerstudio.google.com/reporting/" + LOOKER,
    "https://docs.google.com/spreadsheets/d/e/{}/pubhtml".format(PUB),
    "https://docs.google.com/document/d/e/{}/pub".format(PUB),
    "https://docs.google.com/presentation/d/e/{}/pub".format(PUB),
    "https://ourworldindata.org/grapher/life-expectancy", "https://gdp-dashboard-template.streamlit.app/",
    "https://huggingface.co/spaces/NimaBoscarino/hotdog-gradio",
    "https://gallery.shinyapps.io/050-kmeans-example/",
    "https://infogram.com/10-years-since-lehman-1hkv2nvzgnop6x3", "https://felt.com/map/" + FELT,
    "https://umap.openstreetmap.fr/en/map/" + UMAP, OSM,
    "https://archive.org/details/night_of_the_living_dead_dvd",
    "https://sketchfab.com/models/" + SKETCH,
    "https://colab.research.google.com/github/" + NB_PATH, "https://github.com/" + NB_PATH,
]


@pytest.mark.parametrize(
    "url,service,embed",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "YouTube", YT),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ&list=x", "YouTube", YT),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "YouTube", YT),
        ("https://youtu.be/dQw4w9WgXcQ", "YouTube", YT),
        ("https://youtu.be/dQw4w9WgXcQ?t=90", "YouTube", YT + "?start=90"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1m30s", "YouTube", YT + "?start=90"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "YouTube", YT),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "YouTube", YT),
        (YT, "YouTube", YT),
        ("https://vimeo.com/76979871", "Vimeo", "https://player.vimeo.com/video/76979871"),
        ("https://vimeo.com/76979871/abc123", "Vimeo",
         "https://player.vimeo.com/video/76979871?h=abc123"),
        ("https://player.vimeo.com/video/76979871?h=abc123&autoplay=1", "Vimeo",
         "https://player.vimeo.com/video/76979871?h=abc123"),
        ("https://datawrapper.dwcdn.net/XSj9X/5/", "Datawrapper",
         "https://datawrapper.dwcdn.net/XSj9X/"),
        ("https://www.datawrapper.de/_/XSj9X/", "Datawrapper",
         "https://datawrapper.dwcdn.net/XSj9X/"),
        ("https://public.flourish.studio/visualisation/11597006/", "Flourish",
         "https://flo.uri.sh/visualisation/11597006/embed"),
        ("https://flo.uri.sh/visualisation/11597006/embed", "Flourish",
         "https://flo.uri.sh/visualisation/11597006/embed"),
        ("https://observablehq.com/@d3/bar-chart", "Observable",
         "https://observablehq.com/embed/@d3/bar-chart"),
        ("https://observablehq.com/embed/@d3/bar-chart?cells=chart", "Observable",
         "https://observablehq.com/embed/@d3/bar-chart?cells=chart"),
        ("https://observablehq.com/@d3/bar-chart/2", "Observable",
         "https://observablehq.com/embed/@d3/bar-chart/2"),
        ("https://old.observablehq.com/embed/%40d3/bar-chart/2?cells=chart", "Observable",
         "https://observablehq.com/embed/@d3/bar-chart/2?cells=chart"),
        ("https://public.tableau.com/app/profile/tableau.docs.team/viz/RegionalSampleWorkbook/Storms",
         "Tableau Public",
         "https://public.tableau.com/views/RegionalSampleWorkbook/Storms?:showVizHome=no&:embed=true"),
        ("https://public.tableau.com/views/RegionalSampleWorkbook/Storms?:language=en-US",
         "Tableau Public",
         "https://public.tableau.com/views/RegionalSampleWorkbook/Storms?:showVizHome=no&:embed=true"),
        ("https://app.powerbi.com/view?r=" + PBI, "Power BI",
         "https://app.powerbi.com/view?r=" + PBI),
        ("https://app.powerbi.com/view?r=" + PBI + "%3D&pageName=ReportSection2&x=1", "Power BI",
         "https://app.powerbi.com/view?r=" + PBI + "%3D&pageName=ReportSection2"),
        (STORY, "ArcGIS StoryMaps", STORY),
        (STORY + "/?cover=false", "ArcGIS StoryMaps", STORY),
        (CARTO, "CARTO", CARTO),
        ("https://app.carto.com/map/5d942679-411f-4ab7-afb7-0f6061c9af63", "CARTO",
         "https://app.carto.com/map/5d942679-411f-4ab7-afb7-0f6061c9af63"),
        ("https://oecd-pension-explorer.plotly.app", "Plotly Cloud",
         "https://oecd-pension-explorer.plotly.app/"),
        ("https://medical-imaging-demo.plotly.app/imaging?x=1#y", "Plotly Cloud",
         "https://medical-imaging-demo.plotly.app/imaging"),
        ("https://docs.google.com/forms/d/e/{}/viewform?usp=sf_link".format(GF_ID),
         "Google Forms", GF),
        ("https://docs.google.com/forms/u/1/d/e/{}/viewform".format(GF_ID), "Google Forms", GF),
        (GF, "Google Forms", GF),
        ("https://form.typeform.com/to/NPUAWX7u?typeform-source=x", "Typeform",
         "https://form.typeform.com/to/NPUAWX7u"),
        ("https://www.arcgis.com/apps/dashboards/" + AG, "ArcGIS Online",
         "https://www.arcgis.com/apps/dashboards/" + AG),
        ("https://csse.maps.arcgis.com/apps/dashboards/" + AG, "ArcGIS Online",
         "https://csse.maps.arcgis.com/apps/dashboards/" + AG),
        ("https://experience.arcgis.com/experience/{}/page/Home/".format(AG), "ArcGIS Online",
         "https://experience.arcgis.com/experience/{}/page/Home/".format(AG)),
        ("https://noaa.maps.arcgis.com/apps/instant/basic/index.html?appid={}&x=1".format(AG),
         "ArcGIS Online",
         "https://noaa.maps.arcgis.com/apps/instant/basic/index.html?appid=" + AG),
        ("https://arcgis.com/home/webmap/viewer.html?webmap={}&center=-118.7,37.2&level=12".format(AG),
         "ArcGIS Online", "https://www.arcgis.com/apps/mapviewer/index.html?configurableview=true"
         "&webmap={}&center=-118.7,37.2&level=12".format(AG)),
        ("https://lookerstudio.google.com/u/0/reporting/{}/page/kIV1C".format(LOOKER), "Looker Studio",
         "https://lookerstudio.google.com/embed/reporting/{}/page/kIV1C".format(LOOKER)),
        ("https://datastudio.google.com/reporting/1se-9ZRSukdgQUiRqYTSjT8nFLTV0v8Uq", "Looker Studio",
         "https://lookerstudio.google.com/embed/reporting/1se-9ZRSukdgQUiRqYTSjT8nFLTV0v8Uq"),
        ("https://docs.google.com/spreadsheets/d/e/{}/pubhtml?gid=7&single=true&x=1".format(PUB),
         "Google Sheets", "https://docs.google.com/spreadsheets/d/e/{}/pubhtml?widget=true"
         "&headers=false&gid=7&single=true".format(PUB)),
        ("https://docs.google.com/document/d/e/{}/pub".format(PUB), "Google Docs",
         "https://docs.google.com/document/d/e/{}/pub?embedded=true".format(PUB)),
        ("https://docs.google.com/presentation/d/e/{}/pub?start=true&delayms=5000".format(PUB),
         "Google Slides", "https://docs.google.com/presentation/d/e/{}/embed?start=true"
         "&loop=false&delayms=5000".format(PUB)),
        ("https://ourworldindata.org/grapher/life-expectancy?tab=chart&country=USA~GBR",
         "Our World in Data",
         "https://ourworldindata.org/grapher/life-expectancy?tab=chart&country=USA~GBR"),
        ("https://ourworldindata.org/explorers/co2?Gas+or+warming=CO2", "Our World in Data",
         "https://ourworldindata.org/explorers/co2?Gas+or+warming=CO2"),
        ("https://gdp-dashboard-template.streamlit.app/?embed_options=dark_theme", "Streamlit",
         "https://gdp-dashboard-template.streamlit.app/?embed=true"),
        ("https://huggingface.co/spaces/NimaBoscarino/hotdog-gradio", "Hugging Face Spaces",
         "https://huggingface.co/spaces/NimaBoscarino/hotdog-gradio/embed"),
        ("https://nimaboscarino-hotdog-gradio.hf.space/", "Hugging Face Spaces",
         "https://nimaboscarino-hotdog-gradio.hf.space/"),
        ("https://x.hf.space/?region=eu", "Hugging Face Spaces", "https://x.hf.space/?region=eu"),
        ("https://lookerstudio.google.com/reporting/{}?params=%7B%22a%22:1%7D".format(LOOKER),
         "Looker Studio",
         "https://lookerstudio.google.com/embed/reporting/{}?params=%7B%22a%22:1%7D".format(LOOKER)),
        ("https://gallery.shinyapps.io/050-kmeans-example", "shinyapps.io",
         "https://gallery.shinyapps.io/050-kmeans-example/"),
        ("https://infogram.com/10-years-since-lehman-1hkv2nvzgnop6x3", "Infogram",
         "https://e.infogram.com/10-years-since-lehman-1hkv2nvzgnop6x3?src=embed"),
        ("https://felt.com/map/{}?loc=20.1,5.3,2.5z&token=secret".format(FELT), "Felt",
         "https://felt.com/embed/map/{}?loc=20.1,5.3,2.5z".format(FELT)),
        ("https://umap.openstreetmap.fr/en/map/" + UMAP, "uMap",
         "https://umap.openstreetmap.fr/en/map/{}?{}".format(UMAP, UMAP_OPTS)),
        (OSM.replace(",", "%2C") + "&marker=48.8584%2C2.2945", "OpenStreetMap",
         OSM + "&marker=48.8584,2.2945"),
        ("https://archive.org/details/night_of_the_living_dead_dvd", "Internet Archive",
         "https://archive.org/embed/night_of_the_living_dead_dvd"),
        ("https://sketchfab.com/3d-models/batmobile-" + SKETCH, "Sketchfab",
         "https://sketchfab.com/models/{}/embed".format(SKETCH)),
        ("https://acme.typeform.com/to/LhgLkLvN", "Typeform",
         "https://acme.typeform.com/to/LhgLkLvN"),
        ("https://colab.research.google.com/github/" + NB_PATH + "#scrollTo=x", "Google Colab", NBV),
        ("https://github.com/" + NB_PATH, "Jupyter", NBV),
        ("https://raw.githubusercontent.com/CamDavidsonPilon/Probabilistic-Programming-and-Bayesian-Methods-for-Hackers/master/Chapter1_Introduction/Ch1_Introduction_PyMC3.ipynb",
         "Jupyter", NBV),
        ("https://gist.github.com/fperez/9716279", "Jupyter",
         "https://nbviewer.org/gist/fperez/9716279"),
        (NBV, "Jupyter", NBV),
        ("https://jakevdp.github.io/downloads/notebooks/XKCD_plots.ipynb", "Jupyter",
         "https://nbviewer.org/urls/jakevdp.github.io/downloads/notebooks/XKCD_plots.ipynb"),
        ("https://github.com/" + NB_PATH + "?plain=1", "Jupyter", NBV),
    ],
)
def test_recognised(url, service, embed):
    found = services.suggest(url)
    assert (found.service, found.embed_url) == (service, embed)
    assert found.preset in frame.PRESETS
    assert frame.is_http_url(found.embed_url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=tooshort",
        "https://www.youtube.com/channel/UC123",
        "https://www.youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
        "https://user@www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com:8443/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com:99999/watch?v=dQw4w9WgXcQ",
        "javascript:alert(1)//youtu.be/dQw4w9WgXcQ",
        "https://vimeo.com/channels/staffpicks",
        "https://datawrapper.dwcdn.net/<x>/",
        "https://observablehq.com/@d3",
        "https://observablehq.com/@d3/bar-chart/latest",
        "https://observablehq.com/@d3/bar-chart/2/x",
        "https://new.observablehq.com/@d3/bar-chart",
        "https://public.tableau.com/app/profile/someone",
        "https://app.powerbi.com/groups/me/reports/1234",
        "https://app.powerbi.com/view?r=<script>",
        "https://app.powerbi.com.evil.example/view?r=" + PBI,
        "https://storymaps.arcgis.com/stories/not-an-id",
        "https://storymaps.arcgis.com/collections/18b624c3fd3b461796341ce3bd2c14a7",
        "https://clausa.app.carto.com/viewer/5d942679-411f-4ab7-afb7-0f6061c9af63",
        "https://evil.example/app.carto.com/map/5d942679-411f-4ab7-afb7-0f6061c9af63",
        "https://a.b.app.carto.com/map/5d942679-411f-4ab7-afb7-0f6061c9af63",
        "https://clausa.app.carto.com/map/not-a-uuid",
        "https://plotly.app/",
        "https://a.b.plotly.app/",
        "https://app.plotly.app.evil.example/",
        "https://x.plotly.app/<script>",
        "https://x.plotly.app/a/../b",
        "https://docs.google.com/forms/d/1mbs8CghPVV5Wx8-knLMqzLHBIiARh6Q2cwSvoeSNPpE/edit",
        "https://docs.google.com/document/d/e/{}/pub".format(GF_ID),
        "https://docs.google.com/forms/d/e/<x>/viewform",
        "https://typeform.com/to/NPUAWX7u",
        "https://form.typeform.com/to/<x>",
        "https://a.b.typeform.com/to/NPUAWX7u",
        "https://colab.research.google.com/drive/1j9Wq1gQo1K9yxOQ9Qd_A1lI2NvE-1V9n",
        "https://colab.research.google.com/github/a/b/blob/main/notes.md",
        "https://github.com/a/b/blob/main/notes.py",
        "https://github.com/a/b/tree/main/x.ipynb",
        "https://github.com/a/b/blob/main/../x.ipynb",
        "https://example.com/a/<script>.ipynb",
        "https://example.com/report.html",
        "https://www.arcgis.com/apps/dashboards/not-an-id",
        "https://www.arcgis.com/apps/mapviewer/index.html",
        "https://evil.example/maps.arcgis.com/apps/dashboards/" + AG,
        "https://lookerstudio.google.com/reporting/<x>",
        "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit",
        "https://docs.google.com/spreadsheets/d/e/{}/pub?output=csv".format(PUB),
        "https://docs.google.com/document/d/e/{}/edit".format(PUB),
        # A query that picks the chart is kept whole, or no suggestion is made.
        "https://ourworldindata.org/grapher/life-expectancy?country=a&country=b",
        "https://ourworldindata.org/grapher/<x>",
        "https://evil.example/x.streamlit.app/",
        "https://x.streamlit.app/a/../b",
        "https://huggingface.co/spaces/a",
        "https://huggingface.co/spaces/a/b/settings",
        "https://huggingface.co/spaces/a/b?region=eu",
        "https://ab.shinyapps.io/x/",
        "https://infogram.com/1hkv2nvzgnop6x3",
        "https://felt.com/map/short",
        "https://umap.example.org/en/map/x_1",
        "https://www.openstreetmap.org/#map=15/48.8584/2.2945",
        "https://www.openstreetmap.org/export/embed.html?layer=mapnik",
        "https://archive.org/details/x",
        "https://sketchfab.com/3d-models/not-a-model",
        # nbviewer's urls/ form would drop a query that may pick the file.
        "https://example.com/get/x.ipynb?download=1",
        "https://example.com/get/x.ipynb?download",
    ],
)
def test_not_recognised(url):
    assert services.suggest(url) is None


def test_names_cover_every_service():
    # The view form lists NAMES; each one must be a name a suggestion carries.
    assert len(services.NAMES) == len(services.SERVICES) == len(set(services.NAMES))
    seen = {found.service for found in (services.suggest(url) for url in RECOGNISED)}
    assert seen == set(services.NAMES)


def test_embed_urls_carry_no_input_text():
    # Only validated ids reach the embed URL.
    found = services.suggest("https://vimeo.com/76979871/x\"><script>")
    assert found is None or "<" not in found.embed_url


def test_video_settings():
    found = services.suggest("https://youtu.be/dQw4w9WgXcQ")
    assert (found.preset, found.height_mode, found.height_value) == ("external", "aspect", "16/9")
    assert services.suggest("https://datawrapper.dwcdn.net/XSj9X/").preset == "interactive"
    assert services.suggest("https://app.powerbi.com/view?r=" + PBI).preset == "interactive"
    assert services.suggest(CARTO).preset == "interactive"
    assert services.suggest(STORY).preset == "external"
    assert services.suggest("https://world-cup-goals.plotly.app/").preset == "external"
    assert services.suggest(GF).preset == "external"
    assert services.suggest("https://form.typeform.com/to/NPUAWX7u").preset == "external"
    assert services.suggest("https://github.com/" + NB_PATH).preset == "interactive"
    for url in ("https://docs.google.com/presentation/d/e/{}/pub".format(PUB),
                "https://ourworldindata.org/grapher/life-expectancy", "https://felt.com/map/" + FELT,
                OSM):
        assert services.suggest(url).preset == "interactive", url
    for url in ("https://www.arcgis.com/apps/dashboards/" + AG,
                "https://lookerstudio.google.com/reporting/" + LOOKER,
                "https://gallery.shinyapps.io/050-kmeans-example/",
                "https://infogram.com/10-years-since-lehman-1hkv2nvzgnop6x3",
                "https://umap.openstreetmap.fr/en/map/" + UMAP,
                "https://sketchfab.com/models/" + SKETCH):
        assert services.suggest(url).preset == "external", url


def test_suggested_heights_are_valid():
    for url in RECOGNISED:
        found = services.suggest(url)
        assert frame.height_value_valid(found.height_mode, found.height_value)


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
class TestAction:
    def _call(self, user, **data):
        return helpers.call_action(
            "webpageviewplus_service_suggest",
            context={"user": user["name"] if user else "", "ignore_auth": False},
            **data,
        )

    def _resource(self):
        return factories.Resource(url="https://pages.example.com/a.html")

    def test_needs_permission_on_the_resource(self):
        dataset = factories.Dataset(owner_org=factories.Organization()["id"])
        resource = factories.Resource(package_id=dataset["id"], url="https://x.example/")
        with pytest.raises(toolkit.NotAuthorized):
            self._call(factories.User(), resource_id=resource["id"], url="https://youtu.be/dQw4w9WgXcQ")

    def test_unknown_page(self):
        assert self._call(factories.Sysadmin(), resource_id=self._resource()["id"],
                          url="https://pages.example.com/") is None

    def test_needs_content_origin_without_isolation(self):
        result = self._call(factories.Sysadmin(), resource_id=self._resource()["id"],
                            url="https://youtu.be/dQw4w9WgXcQ")
        assert result["embed_url"] == YT
        assert result["problem"] == "needs_content_origin"

    def test_sandboxed_services_work_anywhere(self):
        result = self._call(factories.Sysadmin(), resource_id=self._resource()["id"],
                            url="https://datawrapper.dwcdn.net/XSj9X/")
        assert result["problem"] == ""

    def test_works_with_isolation(self, monkeypatch):
        from ckanext.webpageviewplus import settings
        monkeypatch.setattr(settings, "isolating_content_origin",
                            lambda: "https://usercontent.example.net")
        result = self._call(factories.Sysadmin(), resource_id=self._resource()["id"],
                            url="https://youtu.be/dQw4w9WgXcQ")
        assert result["problem"] == ""

    @pytest.mark.ckan_config("ckanext.webpageviewplus.presets", "strict interactive")
    def test_preset_not_allowed(self, monkeypatch):
        from ckanext.webpageviewplus import settings
        monkeypatch.setattr(settings, "isolating_content_origin",
                            lambda: "https://usercontent.example.net")
        result = self._call(factories.Sysadmin(), resource_id=self._resource()["id"],
                            url="https://youtu.be/dQw4w9WgXcQ")
        assert result["problem"] == "preset_not_allowed"

    @pytest.mark.ckan_config("ckanext.webpageviewplus.allowed_hosts", "pages.example.com")
    def test_host_not_allowed(self):
        result = self._call(factories.Sysadmin(), resource_id=self._resource()["id"],
                            url="https://datawrapper.dwcdn.net/XSj9X/")
        assert result["problem"] == "host_not_allowed"


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
def test_form_has_suggestion_region(app):
    sysadmin = factories.SysadminWithToken()
    dataset = factories.Dataset()
    resource = factories.Resource(package_id=dataset["id"], url="https://x.example/")
    url = url_for("{}_resource.edit_view".format(dataset["type"]), id=dataset["name"],
                  resource_id=resource["id"], view_type="webpage_view_plus")
    body = app.get(url, headers={"Authorization": sysadmin["token"]}).body
    assert "data-webpageviewplus-service" in body
    assert "webpageviewplus_service_suggest" in body
    assert 'data-preset-names="{&#34;' in body
