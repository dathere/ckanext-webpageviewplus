"""Well-known services: the page URL an editor pastes (a YouTube watch page,
a Tableau Public profile link) to the service's embed URL and the settings
its embed needs. Pure code with no CKAN imports.

The view form suggests these; nothing is rewritten at render, so what's
saved is what's shown. Each service's embed was loaded in Chrome under each
preset: Datawrapper, Flourish, Power BI, CARTO and Jupyter notebooks
(through nbviewer, also for Colab, which refuses every frame) work sandboxed
(interactive); YouTube, Vimeo, Observable, Tableau Public, ArcGIS
StoryMaps, Plotly Cloud apps, Google Forms and Typeform need scripts with
their own origin, so they need external, which needs a content origin.
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
    # Last: _jupyter takes any public .ipynb URL.
    _colab, _jupyter,
)

# The names suggestions carry, in SERVICES order, for the view form's help.
NAMES: tuple[str, ...] = (
    "YouTube", "Vimeo", "Datawrapper", "Flourish", "Observable", "Tableau Public",
    "Power BI", "ArcGIS StoryMaps", "CARTO", "Plotly Cloud", "Google Forms", "Typeform",
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
