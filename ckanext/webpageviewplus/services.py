"""Well-known services: the page URL an editor pastes (a YouTube watch page,
a Tableau Public profile link) to the service's embed URL and the settings
its embed needs. Pure code with no CKAN imports.

The view form suggests these; nothing is rewritten at render, so what's
saved is what's shown. Each service's embed was loaded in Chrome under each
preset: Datawrapper, Flourish, Power BI, CARTO, Google Sheets, Docs and
Slides, Our World in Data, Felt, OpenStreetMap and Jupyter notebooks
(through nbviewer, also for Colab, which refuses every frame) work sandboxed
(interactive); YouTube, Vimeo, Observable, Tableau Public, ArcGIS StoryMaps
and Online, Looker Studio, Plotly Cloud, Streamlit, Hugging Face and
shinyapps.io apps, Google Forms, Typeform, Infogram, uMap, the Internet
Archive and Sketchfab need scripts with their own origin, so they need
external, which needs a content origin.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import parse_qs, urlencode, urlsplit


@dataclass(frozen=True)
class Suggestion:
    service: str
    embed_url: str
    preset: str
    height_mode: str = ""
    height_value: str = ""


_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
                  "youtube-nocookie.com", "www.youtube-nocookie.com"}


def _seconds(value: str) -> Optional[int]:
    """YouTube's t= / start=: 90, 90s or 1h2m3s."""
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", value or "")
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mi * 60 + s


def _youtube(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    video = None
    if host == "youtu.be":
        video = path.strip("/").split("/")[0]
    elif host in _YOUTUBE_HOSTS:
        parts = path.strip("/").split("/")
        if parts == ["watch"]:
            video = (query.get("v") or [""])[0]
        elif len(parts) >= 2 and parts[0] in ("embed", "shorts", "live", "v"):
            video = parts[1]
    if not video or not _YOUTUBE_ID.match(video):
        return None
    url = "https://www.youtube-nocookie.com/embed/" + video
    start = _seconds((query.get("t") or query.get("start") or [""])[0])
    if start:
        url += "?" + urlencode({"start": start})
    return Suggestion("YouTube", url, "external", "aspect", "16/9")


def _vimeo(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    parts = path.strip("/").split("/")
    if host in ("vimeo.com", "www.vimeo.com") and parts and parts[0].isdigit():
        # Unlisted videos: vimeo.com/<id>/<hash>
        video, unlisted = parts[0], parts[1] if len(parts) > 1 else ""
    elif host == "player.vimeo.com" and len(parts) >= 2 and parts[0] == "video" \
            and parts[1].isdigit():
        video, unlisted = parts[1], (query.get("h") or [""])[0]
    else:
        return None
    url = "https://player.vimeo.com/video/" + video
    if unlisted and re.fullmatch(r"[0-9a-f]+", unlisted):
        url += "?" + urlencode({"h": unlisted})
    return Suggestion("Vimeo", url, "external", "aspect", "16/9")


def _datawrapper(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    parts = path.strip("/").split("/")
    chart = None
    if host == "datawrapper.dwcdn.net" and parts and parts[0]:
        chart = parts[0]
    elif host in ("datawrapper.de", "www.datawrapper.de") and len(parts) >= 2 and parts[0] == "_":
        chart = parts[1]
    if not chart or not re.fullmatch(r"[A-Za-z0-9]{5}", chart):
        return None
    # Without the version number, so the view follows republished charts.
    return Suggestion("Datawrapper", "https://datawrapper.dwcdn.net/{}/".format(chart),
                      "interactive", "fixed", "500")


def _flourish(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    parts = path.strip("/").split("/")
    if host not in ("public.flourish.studio", "flo.uri.sh"):
        return None
    if len(parts) >= 2 and parts[0] == "visualisation" and parts[1].isdigit():
        return Suggestion(
            "Flourish", "https://flo.uri.sh/visualisation/{}/embed".format(parts[1]),
            "interactive", "fixed", "600")
    return None


def _observable(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # old.observablehq.com: where embeds live since the new Observable (2026),
    # so links copied from its embed menu come from there.
    if host not in ("observablehq.com", "www.observablehq.com", "old.observablehq.com"):
        return None
    parts = path.strip("/").split("/")
    if parts and parts[0] == "embed":
        parts = parts[1:]
    # old.observablehq.com writes the @ as %40.
    if parts and parts[0][:3].lower() == "%40":
        parts[0] = "@" + parts[0][3:]
    # A notebook may carry a version segment: @d3/bar-chart/2.
    if len(parts) in (2, 3) and re.fullmatch(r"@[A-Za-z0-9_-]+", parts[0]) \
            and re.fullmatch(r"[A-Za-z0-9_-]+", parts[1]) \
            and (len(parts) == 2 or re.fullmatch(r"[0-9]{1,6}", parts[2])):
        notebook = "/".join(parts)
    elif len(parts) == 2 and parts[0] == "d" and re.fullmatch(r"[0-9a-f]{16}", parts[1]):
        notebook = "d/" + parts[1]
    else:
        return None
    # observablehq.com/embed/ redirects to old.observablehq.com for now.
    url = "https://observablehq.com/embed/" + notebook
    cells = (query.get("cells") or [""])[0]
    if cells and re.fullmatch(r"[A-Za-z0-9_,]+", cells):
        url += "?" + urlencode({"cells": cells})
    return Suggestion("Observable", url, "external", "fixed", "600")


def _tableau(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    if host != "public.tableau.com":
        return None
    parts = path.strip("/").split("/")
    # /app/profile/<user>/viz/<workbook>/<sheet> or /views/<workbook>/<sheet>
    if len(parts) >= 6 and parts[:2] == ["app", "profile"] and parts[3] == "viz":
        workbook, sheet = parts[4], parts[5]
    elif len(parts) >= 3 and parts[0] == "views":
        workbook, sheet = parts[1], parts[2]
    else:
        return None
    name = re.compile(r"^[A-Za-z0-9_-]+$")
    if not (name.match(workbook) and name.match(sheet)):
        return None
    return Suggestion(
        "Tableau Public",
        "https://public.tableau.com/views/{}/{}?:showVizHome=no&:embed=true".format(
            workbook, sheet),
        "external", "fixed", "800")


_POWER_BI_TOKEN = re.compile(r"^[A-Za-z0-9+/_=-]{1,2000}$")


def _power_bi(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # Publish to web only: other report links need the viewer's Microsoft login.
    if host != "app.powerbi.com" or path.rstrip("/") != "/view":
        return None
    token = (query.get("r") or [""])[0]
    if not _POWER_BI_TOKEN.match(token):
        return None
    params = {"r": token}
    page = (query.get("pageName") or [""])[0]
    if re.fullmatch(r"[A-Za-z0-9_]{1,100}", page):
        params["pageName"] = page
    return Suggestion("Power BI", "https://app.powerbi.com/view?" + urlencode(params),
                      "interactive", "aspect", "16/9")


def _storymaps(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    if host != "storymaps.arcgis.com":
        return None
    parts = path.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "stories" or not re.fullmatch(r"[0-9a-f]{32}", parts[1]):
        return None
    return Suggestion("ArcGIS StoryMaps",
                      "https://storymaps.arcgis.com/stories/" + parts[1],
                      "external", "fixed", "800")


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _carto(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # app.carto.com or a regional tenant such as clausa.app.carto.com.
    if not re.fullmatch(r"(?:[a-z0-9-]{1,63}\.)?app\.carto\.com", host):
        return None
    parts = path.strip("/").split("/")
    # Public maps only: /viewer/ links to private maps need an access token.
    if len(parts) != 2 or parts[0] != "map" or not _UUID.match(parts[1]):
        return None
    return Suggestion("CARTO", "https://{}/map/{}".format(host, parts[1]),
                      "interactive", "fixed", "640")


def _plotly_cloud(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # Dash apps on Plotly Cloud, including those Plotly Studio publishes. Some
    # work in interactive and some don't (it depends on the app), so external.
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.plotly\.app", host):
        return None
    path = path or "/"
    if not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*/?", path) or "/." in path:
        return None
    return Suggestion("Plotly Cloud", "https://{}{}".format(host, path),
                      "external", "fixed", "800")


def _google_forms(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # A published form's /d/e/<id>/ address; /d/<id>/edit is the editor.
    # It renders without an origin, but its inputs stay disabled: external.
    if host != "docs.google.com":
        return None
    m = re.fullmatch(r"/forms(?:/u/\d+)?/d/e/([A-Za-z0-9_-]{20,128})(?:/[a-zA-Z]*)?/?", path)
    if not m:
        return None
    return Suggestion(
        "Google Forms",
        "https://docs.google.com/forms/d/e/{}/viewform?embedded=true".format(m.group(1)),
        "external", "fixed", "800")


def _typeform(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # form.typeform.com or an account's own <name>.typeform.com.
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.typeform\.com", host):
        return None
    m = re.fullmatch(r"/to/([A-Za-z0-9]{4,32})/?", path)
    if not m:
        return None
    return Suggestion("Typeform", "https://{}/to/{}".format(host, m.group(1)),
                      "external", "fixed", "600")


_ARCGIS_ID = re.compile(r"[0-9a-f]{32}")
_FLOAT = r"-?\d{1,4}(?:\.\d{1,12})?"


def _kept(query: dict[str, list[str]], patterns: dict[str, str]) -> dict[str, str]:
    """The query's values for these keys that match their pattern."""
    kept = {}
    for key, pattern in patterns.items():
        value = (query.get(key) or [""])[0]
        if value and re.fullmatch(pattern, value):
            kept[key] = value
    return kept


def _state(query: dict[str, list[str]]) -> Optional[dict[str, str]]:
    """A query that picks what a page shows (a chart's countries, an app's
    inputs), kept whole; None if it can't be, so no suggestion drops it."""
    if len(query) > 30:
        return None
    kept = {}
    for key, values in query.items():
        value = values[0]
        if len(values) != 1 or not re.fullmatch(r"[A-Za-z0-9_.-][A-Za-z0-9 _.-]{0,63}", key) \
                or len(value) > 500 or re.search(r"[\x00-\x1f\x7f]", value):
            return None
        kept[key] = value
    return kept


def _with_query(url: str, params: dict[str, str]) -> str:
    return url + ("?" + urlencode(params, safe="~,:") if params else "")


def _arcgis(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # Apps embed with their own address; a web map through Map Viewer's
    # embeddable view (Map Viewer Classic's /apps/Embed/ is retired).
    parts = path.strip("/").split("/")
    if host == "experience.arcgis.com":
        if len(parts) >= 2 and parts[0] == "experience" and _ARCGIS_ID.fullmatch(parts[1]):
            url = "https://experience.arcgis.com/experience/" + parts[1]
            if len(parts) == 4 and parts[2] == "page" and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", parts[3]):
                url += "/page/{}/".format(parts[3])
            return Suggestion("ArcGIS Online", url, "external", "fixed", "800")
        return None
    if host == "arcgis.com":
        host = "www.arcgis.com"
    if host != "www.arcgis.com" and not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.maps\.arcgis\.com", host):
        return None
    base = "https://" + host
    if len(parts) == 3 and parts[:2] == ["apps", "dashboards"] and _ARCGIS_ID.fullmatch(parts[2]):
        return Suggestion("ArcGIS Online", "{}/apps/dashboards/{}".format(base, parts[2]),
                          "external", "fixed", "800")
    if len(parts) == 4 and parts[:2] == ["apps", "instant"] and parts[3] == "index.html" \
            and re.fullmatch(r"[a-z0-9-]{1,50}", parts[2]):
        app = (query.get("appid") or [""])[0]
        if _ARCGIS_ID.fullmatch(app):
            return Suggestion("ArcGIS Online", "{}/apps/instant/{}/index.html?appid={}".format(
                base, parts[2], app), "external", "fixed", "800")
        return None
    if parts in (["apps", "mapviewer", "index.html"], ["home", "webmap", "viewer.html"]):
        webmap = (query.get("webmap") or [""])[0]
        if not _ARCGIS_ID.fullmatch(webmap):
            return None
        params = {"configurableview": "true", "webmap": webmap}
        params.update(_kept(query, {
            "center": "{0},{0}".format(_FLOAT), "scale": r"\d{1,12}(?:\.\d{1,6})?",
            "level": r"\d{1,2}", "theme": "light|dark"}))
        return Suggestion("ArcGIS Online",
                          _with_query(base + "/apps/mapviewer/index.html", params),
                          "external", "fixed", "640")
    return None


def _looker_studio(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # The owner must allow embedding; otherwise the embed shows an error.
    if host not in ("lookerstudio.google.com", "datastudio.google.com"):
        return None
    m = re.fullmatch(
        r"(?:/u/\d+)?(?:/embed)?/reporting/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
        r"[0-9a-f]{12}|[A-Za-z0-9_-]{25,44})(?:/page/([A-Za-z0-9_]{1,40}))?/?", path)
    if not m:
        return None
    url = "https://lookerstudio.google.com/embed/reporting/" + m.group(1)
    if m.group(2):
        url += "/page/" + m.group(2)
    return Suggestion("Looker Studio", url, "external", "aspect", "4/3")


def _published(kind: str, path: str) -> Optional[tuple[str, str]]:
    # "Publish to the web" addresses only (/d/e/2PACX-...): /d/<id>/edit
    # links need the viewer's Google login.
    m = re.fullmatch(r"/{}(?:/u/\d+)?/d/e/(2PACX-[A-Za-z0-9_-]{{20,200}})/([a-z]+)/?".format(kind),
                     path)
    return (m.group(1), m.group(2)) if m else None


def _google_sheets(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    found = _published("spreadsheets", path) if host == "docs.google.com" else None
    if not found or found[1] not in ("pubhtml", "pub") \
            or (query.get("output") or ["html"])[0] != "html":
        return None
    params = {"widget": "true", "headers": "false"}
    params.update(_kept(query, {"gid": r"\d{1,12}", "single": "true|false",
                                "range": r"[A-Za-z]{1,3}\d{1,7}(?::[A-Za-z]{1,3}\d{1,7})?"}))
    return Suggestion("Google Sheets", _with_query(
        "https://docs.google.com/spreadsheets/d/e/{}/pubhtml".format(found[0]), params),
        "interactive", "fixed", "600")


def _google_docs(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    found = _published("document", path) if host == "docs.google.com" else None
    if not found or found[1] != "pub":
        return None
    return Suggestion("Google Docs", "https://docs.google.com/document/d/e/{}/pub?embedded=true"
                      .format(found[0]), "interactive", "fixed", "800")


def _google_slides(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # /pub refuses framing; /embed is the published deck's player.
    found = _published("presentation", path) if host == "docs.google.com" else None
    if not found or found[1] not in ("pub", "embed"):
        return None
    params = {"start": "false", "loop": "false", "delayms": "3000"}
    params.update(_kept(query, {"start": "true|false", "loop": "true|false",
                                "delayms": r"\d{1,7}", "slide": r"id\.[A-Za-z0-9_]{1,60}"}))
    return Suggestion("Google Slides", _with_query(
        "https://docs.google.com/presentation/d/e/{}/embed".format(found[0]), params),
        "interactive", "aspect", "960/569")


def _our_world_in_data(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # A chart's page is its embed; its query picks countries, years and tab.
    if host not in ("ourworldindata.org", "www.ourworldindata.org"):
        return None
    m = re.fullmatch(r"/(grapher|explorers)/([a-z0-9-]{1,100})/?", path)
    state = _state(query)
    if not m or state is None:
        return None
    return Suggestion("Our World in Data", _with_query(
        "https://ourworldindata.org/{}/{}".format(m.group(1), m.group(2)), state),
        "interactive", "fixed", "600")


def _app_path(path: str) -> Optional[str]:
    path = path or "/"
    if not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*/?", path) or "/." in path:
        return None
    return path


def _streamlit(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # Without ?embed=true an app bounces through share.streamlit.io for a cookie.
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.streamlit\.app", host):
        return None
    path = _app_path(path)
    state = _state({k: v for k, v in query.items() if k not in ("embed", "embed_options")})
    if path is None or state is None:
        return None
    return Suggestion("Streamlit", _with_query("https://{}{}".format(host, path),
                                               dict(state, embed="true")),
                      "external", "fixed", "800")


def _hugging_face(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # A Space's own host can't be worked out from its name (long or clashing
    # names get a hash), so the page's /embed, which redirects there.
    if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.hf\.space", host):
        path = _app_path(path)
        return None if path is None else Suggestion(
            "Hugging Face Spaces", "https://{}{}".format(host, path), "external", "fixed", "800")
    if host not in ("huggingface.co", "www.huggingface.co"):
        return None
    parts = path.strip("/").split("/")
    name = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
    if len(parts) in (3, 4) and parts[0] == "spaces" and name.fullmatch(parts[1]) \
            and name.fullmatch(parts[2]) and parts[3:] in ([], ["embed"]):
        return Suggestion("Hugging Face Spaces",
                          "https://huggingface.co/spaces/{}/{}/embed".format(parts[1], parts[2]),
                          "external", "fixed", "800")
    return None


def _shinyapps(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,61}[a-z0-9]\.shinyapps\.io", host):
        return None
    m = re.fullmatch(r"/([A-Za-z0-9_-]{1,64})/?", path)
    state = _state(query)
    if not m or state is None:
        return None
    return Suggestion("shinyapps.io", _with_query("https://{}/{}/".format(host, m.group(1)), state),
                      "external", "fixed", "800")


def _infogram(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    if host not in ("infogram.com", "www.infogram.com", "e.infogram.com"):
        return None
    m = re.fullmatch(r"/([a-z0-9-]{0,200}-1[a-z0-9]{11,16}|[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}"
                     r"[0-9a-f]{12})/?", path)
    if not m:
        return None
    # Without an origin the embed never gets past its loading spinner.
    return Suggestion("Infogram", "https://e.infogram.com/{}?src=embed".format(m.group(1)),
                      "external", "fixed", "600")


def _felt(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    if host not in ("felt.com", "www.felt.com"):
        return None
    m = re.fullmatch(r"(?:/embed)?/map/((?:[A-Za-z0-9-]{0,200}-)?[A-Za-z0-9]{20,30})/?", path)
    if not m:
        return None
    flag = "0|1"
    params = _kept(query, {
        "loc": r"{0},{0},\d{{1,2}}(?:\.\d{{1,6}})?z(?:,{0}b)?".format(_FLOAT), "legend": flag,
        "cooperativeGestures": flag, "link": flag, "geolocation": flag, "zoomControls": flag,
        "scaleBar": flag, "rotation": flag, "contextMenu": flag})
    return Suggestion("Felt", _with_query("https://felt.com/embed/map/" + m.group(1), params),
                      "interactive", "fixed", "600")


_UMAP_EMBED = {
    "scaleControl": "false", "miniMapControl": "false", "scrollWheelZoom": "false",
    "zoomControl": "true", "editMode": "disabled", "moreControl": "true",
    "searchControl": "null", "tilelayersControl": "null", "embedControl": "null",
    "datalayersControl": "true", "onLoadPanel": "none", "captionBar": "false",
    "captionMenus": "true",
}


def _umap(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # The two big public instances; the options are uMap's own embed defaults.
    if host not in ("umap.openstreetmap.fr", "umap.openstreetmap.de"):
        return None
    m = re.fullmatch(r"(/[a-z]{2,3}(?:-[a-z]{2,4})?)?/map/([A-Za-z0-9-]{0,200}_\d{1,12})/?", path)
    if not m:
        return None
    params = dict(_UMAP_EMBED)
    params.update(_kept(query, {"feature": r"[A-Za-z0-9_-]{1,100}",
                                "datalayers": r"[A-Za-z0-9_,-]{1,500}"}))
    return Suggestion("uMap", _with_query("https://{}{}/map/{}".format(
        host, m.group(1) or "", m.group(2)), params), "external", "fixed", "600")


def _openstreetmap(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # Only the Share dialog's embed address: a map page keeps its place in the
    # URL's #fragment, which never reaches the server.
    if host not in ("openstreetmap.org", "www.openstreetmap.org") or path != "/export/embed.html":
        return None
    params = _kept(query, {
        "bbox": "{0},{0},{0},{0}".format(r"-?\d{1,3}(?:\.\d{1,12})?"),
        "layer": "mapnik|cyclosm|cyclemap|transportmap|hot|shortbread",
        "marker": "{0},{0}".format(r"-?\d{1,3}(?:\.\d{1,12})?"), "theme": "light|dark"})
    if "bbox" not in params:
        return None
    return Suggestion("OpenStreetMap", _with_query(
        "https://www.openstreetmap.org/export/embed.html", params),
        "interactive", "fixed", "500")


def _internet_archive(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    if host not in ("archive.org", "www.archive.org"):
        return None
    parts = path.strip("/").split("/")
    if len(parts) < 2 or parts[0] not in ("details", "embed") \
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{4,99}", parts[1]) \
            or (len(parts) > 2 and not _segments_ok(parts[2:])):
        return None
    return Suggestion("Internet Archive", "https://archive.org/embed/" + "/".join(parts[1:]),
                      "external", "fixed", "600")


def _sketchfab(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    if host not in ("sketchfab.com", "www.sketchfab.com"):
        return None
    uid = r"([0-9a-f]{32}|[0-9A-Za-z]{27})"
    m = re.fullmatch(r"/3d-models/(?:[A-Za-z0-9-]{0,200}-)?" + uid + "/?", path) \
        or re.fullmatch(r"/models/" + uid + r"(?:/embed)?/?", path)
    if not m:
        return None
    params = _kept(query, {"autostart": "0|1", "autospin": r"-?\d{1,2}(?:\.\d{1,3})?"})
    return Suggestion("Sketchfab", _with_query(
        "https://sketchfab.com/models/{}/embed".format(m.group(1)), params),
        "external", "aspect", "16/9")


# Jupyter notebooks, shown read-only through nbviewer (the Jupyter project's
# renderer): it needs scripts for MathJax but no origin, so interactive.
NBVIEWER = "https://nbviewer.org/"
_SEGMENT = re.compile(r"[A-Za-z0-9._~%+-]{1,200}")


def _segments_ok(parts: list[str]) -> bool:
    return bool(parts) and all(
        _SEGMENT.fullmatch(p) and p not in (".", "..") for p in parts)


def _notebook(parts: list[str]) -> bool:
    return _segments_ok(parts) and parts[-1].lower().endswith(".ipynb")


def _nbviewer(name: str, rest: str) -> Suggestion:
    return Suggestion(name, NBVIEWER + rest, "interactive", "fixed", "800")


def _colab(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    # Colab refuses every frame (X-Frame-Options: DENY), so a notebook it
    # opens from GitHub is shown through nbviewer. Drive notebooks can't be.
    if host != "colab.research.google.com":
        return None
    parts = path.strip("/").split("/")
    if len(parts) >= 6 and parts[0] == "github" and parts[3] == "blob" \
            and _notebook(parts[1:3] + parts[4:]):
        return _nbviewer("Google Colab", "github/" + "/".join(parts[1:]))
    return None


def _jupyter(host: str, path: str, query: dict[str, list[str]]) -> Optional[Suggestion]:
    parts = path.strip("/").split("/")
    if host in ("github.com", "www.github.com"):
        # /<owner>/<repo>/blob/<ref>/<path>.ipynb
        if len(parts) >= 5 and parts[2] == "blob" and _notebook(parts[:2] + parts[3:]):
            return _nbviewer("Jupyter", "github/" + "/".join(parts))
        return None
    if host == "raw.githubusercontent.com":
        # /<owner>/<repo>/<ref>/<path>.ipynb
        if len(parts) >= 4 and _notebook(parts):
            return _nbviewer("Jupyter", "github/{}/{}/blob/{}".format(
                parts[0], parts[1], "/".join(parts[2:])))
        return None
    if host == "gist.github.com":
        if len(parts) == 2 and _segments_ok(parts[:1]) \
                and re.fullmatch(r"[0-9a-f]{1,64}", parts[1]):
            return _nbviewer("Jupyter", "gist/" + "/".join(parts))
        return None
    if host == "nbviewer.org":
        if len(parts) >= 2 and parts[0] in ("github", "gist", "url", "urls") \
                and _segments_ok(parts[1:]):
            return _nbviewer("Jupyter", "/".join(parts))
        return None
    # Any other public notebook file, fetched by nbviewer over https. Its
    # urls/ form drops the query, which could pick the file, so none is taken.
    if not query and _notebook(parts) and re.fullmatch(r"[a-z0-9.-]{1,253}", host):
        return _nbviewer("Jupyter", "urls/{}/{}".format(host, "/".join(parts)))
    return None


SERVICES: tuple[Callable[[str, str, dict[str, list[str]]], Optional[Suggestion]], ...] = (
    _youtube, _vimeo, _datawrapper, _flourish, _observable, _tableau,
    _power_bi, _storymaps, _carto, _plotly_cloud, _google_forms, _typeform,
    _arcgis, _looker_studio, _google_sheets, _google_docs, _google_slides,
    _our_world_in_data, _streamlit, _hugging_face, _shinyapps, _infogram,
    _felt, _umap, _openstreetmap, _internet_archive, _sketchfab,
    # Last: _jupyter takes any public .ipynb URL.
    _colab, _jupyter,
)

# The names suggestions carry, in SERVICES order, for the view form's help.
NAMES: tuple[str, ...] = (
    "YouTube", "Vimeo", "Datawrapper", "Flourish", "Observable", "Tableau Public",
    "Power BI", "ArcGIS StoryMaps", "CARTO", "Plotly Cloud", "Google Forms", "Typeform",
    "ArcGIS Online", "Looker Studio", "Google Sheets", "Google Docs", "Google Slides",
    "Our World in Data", "Streamlit", "Hugging Face Spaces", "shinyapps.io", "Infogram",
    "Felt", "uMap", "OpenStreetMap", "Internet Archive", "Sketchfab",
    "Google Colab", "Jupyter",
)


def suggest(url: str) -> Optional[Suggestion]:
    """The embed settings for a well-known service's page, or None."""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password or port not in (None, 80, 443):
        return None
    host = parts.hostname.lower().rstrip(".")
    query = parse_qs(parts.query, keep_blank_values=True)
    for service in SERVICES:
        found = service(host, parts.path, query)
        if found:
            return found
    return None
