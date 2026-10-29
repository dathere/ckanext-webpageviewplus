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


RECOGNISED = [
    "https://youtu.be/dQw4w9WgXcQ", "https://vimeo.com/76979871",
    "https://datawrapper.dwcdn.net/XSj9X/", "https://flo.uri.sh/visualisation/11597006/embed",
    "https://observablehq.com/@d3/bar-chart", "https://public.tableau.com/views/A/B",
    "https://app.powerbi.com/view?r=" + PBI, STORY, CARTO, "https://x.plotly.app/", GF,
    "https://form.typeform.com/to/NPUAWX7u",
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


def test_suggested_heights_are_valid():
    for url in ("https://youtu.be/dQw4w9WgXcQ", "https://datawrapper.dwcdn.net/XSj9X/",
                "https://public.tableau.com/views/A/B", "https://app.powerbi.com/view?r=" + PBI,
                STORY, CARTO):
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
