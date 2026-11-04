"""The Repository (Plus) view (repo_view_plus): a public repository's root
the way its own site shows it (name, description, facts, root listing,
README), for sites that can't be framed.

CKAN fetches the site's API, never the visitor's browser, and keeps a copy
per repository (the shape of pdf.cached_pdf). The page is static: no
scripts, served on CKAN's host under a CSP sandbox and framed with
``allow-popups`` only, so there is nothing for the frame invariant to guard
and no content origin is needed. GitHub only for now.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import posixpath
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote, urljoin, urlsplit

from flask import Blueprint, Response, abort

import ckan.plugins.toolkit as toolkit

from ckanext.webpageviewplus import events, frame, frameability, pdf, settings

VIEW_TYPE = "repo_view_plus"
GITHUB_API = "https://api.github.com"
USER_AGENT = "ckanext-webpageviewplus repo_view_plus"
FETCH_SECONDS = 20  # all of one repository's calls
FAILED_SECONDS = 300
MAX_JSON = 2 * 1024 * 1024
MAX_README = 1024 * 1024
MAX_ENTRIES = 1000
HEIGHT_MODES = ("fixed", "viewport")
DEFAULT_HEIGHT = ("fixed", "800")
# The frame: no scripts, no same origin; links open in a new tab.
SANDBOX = "allow-popups allow-popups-to-escape-sandbox"

# github.com/<name> paths that aren't owners.
_RESERVED = frozenset("""
about account apps blog codespaces collections contact customer-stories dashboard
enterprise events explore features git-guides github issues join login logout
marketplace new notifications orgs organizations pricing pulls readme search security
sessions settings signup site sponsors team topics trending users
""".split())
_NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")
_REF = re.compile(r"[A-Za-z0-9._-]{1,250}")


@dataclass(frozen=True)
class Repo:
    owner: str
    name: str
    ref: str = ""  # "" is the default branch

    @property
    def key(self) -> str:
        return "github.com/{}/{}@{}".format(self.owner.lower(), self.name.lower(), self.ref)

    @property
    def html_url(self) -> str:
        url = "https://github.com/{}/{}".format(self.owner, self.name)
        return url + "/tree/" + self.ref if self.ref else url


def parse(url: str) -> Optional[Repo]:
    """The repository a GitHub link shows at its root:
    ``github.com/<owner>/<repo>``, optionally ``.git`` or ``/tree/<branch>``
    (a branch without slashes). None for anything else: files, folders,
    issues, gists and GitHub's own pages."""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or parts.username or parts.password \
            or port not in (None, 80, 443):
        return None
    if (parts.hostname or "").lower().rstrip(".").removeprefix("www.") != "github.com":
        return None
    segments = parts.path.strip("/").split("/")
    ref = ""
    if len(segments) == 4 and segments[2] == "tree":
        ref = segments[3]
        if not _REF.fullmatch(ref) or ref in (".", ".."):
            return None
    elif len(segments) != 2:
        return None
    owner, name = segments[0], segments[1]
    if len(segments) == 2 and name.lower().endswith(".git"):
        name = name[:-4]
    if not _NAME.fullmatch(owner) or not _NAME.fullmatch(name) \
            or owner.lower() in _RESERVED or name in (".", ".."):
        return None
    return Repo(owner, name, ref)


def repo_url(config: dict[str, Any], resource: dict[str, Any]) -> str:
    """The link a view shows: its own repo_url, else the resource's URL."""
    return (config or {}).get("repo_url") or resource.get("url") or ""


# Fetching

class Unavailable(Exception):
    """Why a repository couldn't be fetched: ``rate_limited`` (with
    ``until``, when GitHub's limit resets), ``missing`` (no such public
    repository) or ``failed``."""

    def __init__(self, kind: str, until: float = 0.0):
        super().__init__(kind)
        self.kind = kind
        self.until = until


def _api(path: str, deadline: float, max_bytes: int, accept: str) -> tuple[int, bytes]:
    """GET one GitHub API path with frameability's guards (public addresses,
    the deadline, a byte cap). Redirects (a renamed repository) are followed
    only within api.github.com, so the token never leaves it."""
    url = GITHUB_API + path
    headers = {"User-Agent": USER_AGENT, "Accept": accept,
               "X-GitHub-Api-Version": "2022-11-28"}
    token = settings.github_token()
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with frameability._guarded(deadline):
            for _ in range(frameability.MAX_REDIRECTS + 1):
                if frameability._address_kind(url, deadline) != "public":
                    raise Unavailable("failed")
                timeout = min(frameability.TIMEOUT, deadline - time.monotonic())
                if timeout <= 0:
                    raise Unavailable("failed")
                response = frameability._http_get(
                    url, stream=True, timeout=timeout, allow_redirects=False, headers=headers)
                try:
                    location = response.headers.get("Location")
                    if response.is_redirect and location:
                        url = urljoin(url, location)
                        if not url.startswith(GITHUB_API + "/"):
                            raise Unavailable("failed")
                        continue
                    if response.status_code in (403, 429) and (
                            response.headers.get("X-RateLimit-Remaining") == "0"
                            or response.status_code == 429):
                        try:
                            until = float(response.headers.get("X-RateLimit-Reset") or 0)
                        except ValueError:
                            until = 0.0
                        raise Unavailable("rate_limited", until)
                    data = bytearray()
                    for chunk in response.iter_content(64 * 1024):
                        data += chunk
                        if len(data) > max_bytes or time.monotonic() > deadline:
                            raise Unavailable("failed")
                    return response.status_code, bytes(data)
                finally:
                    response.close()
    except Unavailable:
        raise
    except Exception:
        raise Unavailable("failed")
    raise Unavailable("failed")


def _json(path: str, deadline: float) -> Any:
    status, body = _api(path, deadline, MAX_JSON, "application/vnd.github+json")
    if status in (404, 451):
        raise Unavailable("missing")
    if status != 200:
        raise Unavailable("failed")
    try:
        return json.loads(body)
    except ValueError:
        raise Unavailable("failed")


def fetch(repo: Repo, deadline: float) -> dict[str, Any]:
    """What the page shows, from three calls: the repository, its root
    listing and its README (rendered by GitHub, then cleaned here)."""
    base = "/repos/{}/{}".format(quote(repo.owner), quote(repo.name))
    info = _json(base, deadline)
    if not isinstance(info, dict) or info.get("private"):
        raise Unavailable("missing")
    ref = repo.ref or str(info.get("default_branch") or "")
    listing = _json(base + "/contents/?ref=" + quote(ref, safe=""), deadline)
    if not isinstance(listing, list):
        raise Unavailable("failed")
    status, readme = _api(base + "/readme?ref=" + quote(ref, safe=""), deadline,
                          MAX_README, "application/vnd.github.html+json")
    if status not in (200, 404):
        raise Unavailable("failed")
    owner, name = _owner_and_name(info, repo)
    entries = []
    for item in listing[:MAX_ENTRIES]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        kind = "dir" if item.get("type") == "dir" else "file"
        entries.append({"name": item["name"], "type": kind,
                        "url": _site_url(owner, name, ref, item["name"], kind)})
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    readme_html = ""
    if status == 200:
        text = readme.decode("utf-8", "replace")
        found = re.search(r'data-path="([^"]+)"', text)
        readme_html = clean_readme(text, owner, name, ref,
                                   found.group(1) if found else "README.md")
    return {
        "site": "GitHub",
        "full_name": "{}/{}".format(owner, name),
        "html_url": _site_url(owner, name, ref if repo.ref else "", "", "dir"),
        "description": info.get("description") or "",
        "language": info.get("language") or "",
        "stars": int(info.get("stargazers_count") or 0),
        "forks": int(info.get("forks_count") or 0),
        "pushed_at": str(info.get("pushed_at") or ""),
        "archived": bool(info.get("archived")),
        "ref": ref,
        "entries": entries,
        "truncated": len(listing) > MAX_ENTRIES,
        "readme": readme_html,
    }


def _owner_and_name(info: dict[str, Any], repo: Repo) -> tuple[str, str]:
    """The repository's current names (after a rename), if they're sane."""
    full = str(info.get("full_name") or "")
    owner, _, name = full.partition("/")
    if _NAME.fullmatch(owner) and _NAME.fullmatch(name):
        return owner, name
    return repo.owner, repo.name


def _site_url(owner: str, name: str, ref: str, path: str, kind: str) -> str:
    url = "https://github.com/{}/{}".format(owner, name)
    if not path:
        return url + "/tree/" + quote(ref, safe="") if ref else url
    return "{}/{}/{}/{}".format(url, "tree" if kind == "dir" else "blob",
                                quote(ref, safe=""), quote(path))


# Cleaning the README

_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "details", "div", "dl",
    "dt", "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "input", "ins",
    "kbd", "li", "ol", "p", "pre", "q", "s", "samp", "span", "strike", "strong", "sub",
    "summary", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "tt", "ul",
}
_ATTRIBUTES = {
    "a": {"href", "title", "id", "name"},
    "img": {"src", "alt", "title", "width", "height", "align"},
    "input": {"type", "checked"},
    "td": {"align", "colspan", "rowspan"},
    "th": {"align", "colspan", "rowspan"},
    "ol": {"start"},
    "details": {"open"},
    "div": {"align"},
    "p": {"align"},
    "h1": {"id", "align"}, "h2": {"id", "align"}, "h3": {"id", "align"},
    "h4": {"id", "align"}, "h5": {"id", "align"}, "h6": {"id", "align"},
}


def clean_readme(html: str, owner: str, name: str, ref: str, path: str) -> str:
    """GitHub's rendered README, cleaned with nh3 to a fixed list of tags and
    attributes. Relative links point at the file on GitHub and relative
    images at its raw address, from the README's own folder; in-page links
    at GitHub's ``user-content-`` ids."""
    import nh3

    folder = posixpath.dirname(path)
    quoted_ref = quote(ref, safe="")
    blob = "https://github.com/{}/{}/blob/{}/{}".format(
        owner, name, quoted_ref, folder + "/" if folder else "")
    raw = "https://raw.githubusercontent.com/{}/{}/{}/{}".format(
        owner, name, quoted_ref, folder + "/" if folder else "")

    def rewrite(tag: str, attribute: str, value: str) -> Optional[str]:
        if tag == "input" and attribute == "type":
            return value if value == "checkbox" else None
        if attribute not in ("href", "src"):
            return value
        value = value.strip()
        if attribute == "href" and value.startswith("#"):
            fragment = value[1:]
            return value if fragment.startswith("user-content-") else "#user-content-" + fragment
        if urlsplit(value).scheme:
            return value
        return urljoin(raw if attribute == "src" else blob, value)

    cleaned = nh3.clean(
        html,
        tags=_TAGS,
        attributes=_ATTRIBUTES,
        attribute_filter=rewrite,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
        set_tag_attribute_values={"a": {"target": "_blank"},
                                  "input": {"disabled": "disabled"}},
        strip_comments=True,
    )
    # In-page links scroll the page instead of opening a new tab.
    return _IN_PAGE.sub(r"\1>", cleaned)


_IN_PAGE = re.compile(r'(<a href="#[^"]*") target="_blank" rel="noopener noreferrer">')


# The copy

def _folder() -> Optional[str]:
    root = toolkit.config.get("ckan.storage_path")
    return os.path.join(root, "webpageviewplus", "repo") if root else None


def cached(repo: Repo, view_id: str = "") -> tuple[Optional[dict[str, Any]], str]:
    """The repository's page data and "" when there is a copy, else None and
    why: ``rate_limited``, ``missing``, ``failed`` or ``no_storage``.

    Kept for repo_cache_seconds and shared by every view of the same
    repository and branch. One worker fetches a repository at a time (a lock
    file); the others serve the copy being refreshed. A failure is
    remembered for FAILED_SECONDS, or until GitHub's limit resets; when a
    refresh fails, the old copy keeps being served, as the repository is
    public at its source."""
    folder = _folder()
    if folder is None:
        return None, "no_storage"
    base = os.path.join(folder, hashlib.sha256(repo.key.encode()).hexdigest())
    path = base + ".json"
    stale = _read(path)
    if stale is not None and pdf._age(path) < settings.repo_cache_seconds():
        return stale, ""
    failed = _read(base + ".failed")
    if failed is not None and time.time() < float(failed.get("until") or 0):
        return (stale, "") if stale is not None else (None, str(failed.get("kind")))
    os.makedirs(folder, exist_ok=True)
    if not pdf._lock(base + ".lock"):
        return (stale, "") if stale is not None else (None, "failed")
    try:
        try:
            data = fetch(repo, time.monotonic() + FETCH_SECONDS)
        except Unavailable as problem:
            until = max(time.time() + FAILED_SECONDS, problem.until)
            _write(base + ".failed", {"kind": problem.kind, "until": until})
            events.emit("repo_fetch_failed", view_id=view_id, reason=problem.kind,
                        host="github.com")
            return (stale, "") if stale is not None else (None, problem.kind)
        _write(path, data)
        pdf._remove(base + ".failed")
        return data, ""
    finally:
        pdf._remove(base + ".lock")


def _read(path: str) -> Optional[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(path: str, data: dict[str, Any]) -> None:
    # A name of its own, so a reader never sees half a file.
    fd, temp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(temp, path)
    except OSError:
        pdf._remove(temp)


# The page

def _style() -> str:
    with open(os.path.join(os.path.dirname(__file__), "assets", "repo_page.css"),
              encoding="utf-8") as f:
        return f.read()


STYLE = _style()
# The page's one stylesheet is inline, allowed by its hash: a sandboxed page
# has an opaque origin, so 'self' can't be relied on to mean CKAN's.
CSP = "; ".join([
    "sandbox " + SANDBOX,
    "default-src 'none'",
    "img-src https: data:",
    "style-src 'sha256-{}'".format(
        base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()),
    "base-uri 'none'",
    "form-action 'none'",
])

repo = Blueprint("webpageviewplus_repo", __name__)


@repo.route("/webpageviewplus/repo/<view_id>")
def page(view_id: str) -> Response:
    """The page a Repository (Plus) view frames. Shown to whoever may see
    the view (resource_view_show checks the dataset, private ones too), so
    it's served on CKAN's host, where the visitor's session is."""
    user = toolkit.current_user
    context = {"user": user.name if getattr(user, "is_authenticated", False) else ""}
    try:
        view = toolkit.get_action("resource_view_show")(context, {"id": view_id})
        if view.get("view_type") != VIEW_TYPE:
            abort(404)
        resource = toolkit.get_action("resource_show")(context, {"id": view["resource_id"]})
    except (toolkit.ObjectNotFound, toolkit.NotAuthorized):
        abort(404)
    url = repo_url(view, resource)
    found = parse(url)
    data, problem = (None, "not_a_repo") if found is None else cached(found, view_id)
    body = toolkit.render("webpageviewplus/repo_page.html", {
        "repo": data,
        "updated": _when(str((data or {}).get("pushed_at") or "")),
        "problem": problem,
        "link": found.html_url if found is not None else url,
        "style": STYLE,
        "lang": toolkit.h.lang(),
    })
    response = Response(body, mimetype="text/html")
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "private, no-cache"
    return response


def _when(value: str) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def page_path(view_id: str) -> str:
    return toolkit.url_for("webpageviewplus_repo.page", view_id=view_id)


def is_repo_link(url: str) -> bool:
    return frame.is_http_url(url) and parse(url) is not None
