"""The Repository (Plus) view (repo_view_plus): a public repository's root
the way its own site shows it (name, description, facts, root listing,
README), for sites that can't be framed: GitHub, Codeberg (Forgejo, with
Gitea's API), GitLab and Bitbucket, plus self-hosted GitHub Enterprise,
Gitea/Forgejo and GitLab named in ``repo_hosts``.

CKAN fetches the site's API, never the visitor's browser, and keeps a copy
per repository (the shape of pdf.cached_pdf). The page is static: no
scripts, served on CKAN's host under a CSP sandbox and framed with
``allow-popups`` only, so there is nothing for the frame invariant to guard
and no content origin is needed.
"""
from __future__ import annotations

import base64
import datetime
import email.utils
import hashlib
import html as html_lib
import json
import os
import posixpath
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
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
# GitLab and Bitbucket list a folder a page at a time: the root's first
# page only, with a link to the site for the rest.
PAGE_ENTRIES = 100
HEIGHT_MODES = ("fixed", "viewport")
DEFAULT_HEIGHT = ("fixed", "800")
# The frame: no scripts, no same origin; links open in a new tab.
SANDBOX = "allow-popups allow-popups-to-escape-sandbox"


@dataclass(frozen=True)
class Host:
    """A site repositories are shown from: its kind (which API it speaks),
    its web host, its API's base address and the name the page shows."""
    kind: str  # github, gitea, gitlab or bitbucket
    host: str
    api: str
    site: str


BUILT_IN = {
    "github.com": Host("github", "github.com", GITHUB_API, "GitHub"),
    "codeberg.org": Host("gitea", "codeberg.org", "https://codeberg.org/api/v1", "Codeberg"),
    "gitlab.com": Host("gitlab", "gitlab.com", "https://gitlab.com/api/v4", "GitLab"),
    "bitbucket.org": Host("bitbucket", "bitbucket.org", "https://api.bitbucket.org/2.0",
                          "Bitbucket"),
}
# Where each self-hostable kind's API lives on its own host.
_SELF_HOSTED_API = {"github": "/api/v3", "gitea": "/api/v1", "gitlab": "/api/v4"}


def host_for(hostname: str) -> Optional[Host]:
    """The site a link's host is: a built-in one, or one in repo_hosts."""
    name = (hostname or "").lower().rstrip(".")
    found = BUILT_IN.get(name.removeprefix("www."))
    if found is not None:
        return found
    kind = settings.repo_hosts().get(name)
    if kind not in _SELF_HOSTED_API:
        return None
    return Host(kind, name, "https://" + name + _SELF_HOSTED_API[kind], name)


# First path segments that are the site's own pages, not owners.
_RESERVED = {
    "github": frozenset("""
        about account apps blog codespaces collections contact customer-stories dashboard
        enterprise events explore features git-guides github issues join login logout
        marketplace new notifications orgs organizations pricing pulls readme search
        security sessions settings signup site sponsors team topics trending users
        """.split()),
    "gitea": frozenset("""
        - admin api assets attachments avatars captcha explore issues login milestones
        notifications org pulls repo swagger.v1.json user
        """.split()),
    "gitlab": frozenset("""
        - admin api assets dashboard explore groups help oauth projects public search
        snippets uploads users
        """.split()),
    "bitbucket": frozenset("""
        account blog dashboard product repo site socialauth support
        """.split()),
}
_NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")
_REF = re.compile(r"[A-Za-z0-9._-]{1,250}")
# GitLab nests groups up to 20 deep.
_MAX_GROUPS = 20


@dataclass(frozen=True)
class Repo:
    owner: str  # on GitLab, the group path: "group/subgroup"
    name: str
    ref: str = ""  # "" is the default branch
    host: Host = BUILT_IN["github.com"]

    @property
    def key(self) -> str:
        # GitHub's is unchanged from 0.11.0, so copies already kept stay valid.
        return "{}/{}/{}@{}".format(
            self.host.host, self.owner.lower(), self.name.lower(), self.ref)

    @property
    def html_url(self) -> str:
        return _site_url(self.host, self.owner, self.name, self.ref, "", "dir")


def _segments_ok(segments: list[str]) -> bool:
    return all(_NAME.fullmatch(s) and s not in (".", "..") for s in segments)


def _ref_ok(ref: str) -> bool:
    return bool(_REF.fullmatch(ref)) and ref not in (".", "..")


def _shape(kind: str, segments: list[str]) -> Optional[tuple[list[str], str]]:
    """The repository's path segments and branch, from a link's path, for
    each site's own address of a repository's root."""
    if kind == "gitlab":
        ref = ""
        if "-" in segments:
            at = segments.index("-")
            if segments[at + 1:at + 2] != ["tree"] or len(segments) != at + 3:
                return None
            ref, segments = segments[at + 2], segments[:at]
        if not 2 <= len(segments) <= _MAX_GROUPS + 1:
            return None
        return segments, ref
    marker = {"github": ["tree"], "gitea": ["src", "branch"], "bitbucket": ["src"]}[kind]
    if len(segments) == 2:
        return segments, ""
    if len(segments) == 3 + len(marker) and segments[2:-1] == marker:
        return segments[:2], segments[-1]
    return None


def parse(url: str) -> Optional[Repo]:
    """The repository a link shows at its root, on a site this view knows:
    ``github.com/<owner>/<repo>`` (or ``/tree/<branch>``),
    ``codeberg.org/<owner>/<repo>`` (or ``/src/branch/<branch>``),
    ``gitlab.com/<group>/.../<project>`` (or ``/-/tree/<branch>``),
    ``bitbucket.org/<workspace>/<repo>`` (or ``/src/<branch>/``), each
    optionally ending ``.git`` without a branch, and the same on a
    repo_hosts site of that kind. A branch has no slashes. None for anything
    else: files, folders, issues and the sites' own pages."""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or parts.username or parts.password \
            or port not in (None, 80, 443):
        return None
    host = host_for(parts.hostname or "")
    if host is None:
        return None
    shaped = _shape(host.kind, parts.path.strip("/").split("/"))
    if shaped is None:
        return None
    segments, ref = shaped
    if ref and not _ref_ok(ref):
        return None
    if not ref and segments[-1].lower().endswith(".git"):
        segments = segments[:-1] + [segments[-1][:-4]]
    if not _segments_ok(segments) or segments[0].lower() in _RESERVED[host.kind]:
        return None
    return Repo("/".join(segments[:-1]), segments[-1], ref, host)


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


def _api(path: str, deadline: float, max_bytes: int, accept: str,
         api: str = GITHUB_API) -> tuple[int, bytes]:
    """GET one API path with frameability's guards (public addresses, the
    deadline, a byte cap). Redirects (a renamed repository) are followed
    only within that API's base address. github_token goes to
    api.github.com only, never to another site or GitHub Enterprise."""
    url = api + path
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if api == GITHUB_API:
        headers["X-GitHub-Api-Version"] = "2022-11-28"
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
                        if not url.startswith(api + "/"):
                            raise Unavailable("failed")
                        continue
                    until = _rate_limit(response.status_code, response.headers)
                    if until is not None:
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


# Codeberg's (IETF draft) RateLimit field: "baseline";r=<left>;t=<seconds>.
_RATELIMIT_FIELD = re.compile(r";\s*(r|t)=(\d+)")


def _rate_limit(status: int, headers: Any) -> Optional[float]:
    """When the site's limit resets, if this answer says it's used up: any
    429, or a 403 saying no requests are left. Each site says when in its
    own way: GitHub's X-RateLimit-Reset and GitLab's RateLimit-Reset (a
    time), Codeberg's RateLimit ``t=`` and Retry-After (seconds, or a
    date); 0.0 when none is readable."""
    found = {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}
    field = dict(_RATELIMIT_FIELD.findall(found.get("ratelimit", "")))
    left = found.get("x-ratelimit-remaining", found.get("ratelimit-remaining", field.get("r")))
    if status != 429 and not (status == 403 and left == "0"):
        return None
    now = time.time()
    times = []
    for name in ("x-ratelimit-reset", "ratelimit-reset"):
        try:
            value = float(found[name])
        except (KeyError, ValueError):
            continue
        # A time since 1970, or (the IETF draft's) seconds from now.
        times.append(value if value > 1e9 else now + value)
    if "t" in field:
        times.append(now + float(field["t"]))
    retry = found.get("retry-after", "").strip()
    if retry.isdigit():
        times.append(now + float(retry))
    elif retry:
        try:
            times.append(email.utils.parsedate_to_datetime(retry).timestamp())
        except (TypeError, ValueError):
            pass
    return max(times, default=0.0)


def _json(path: str, deadline: float, api: str = GITHUB_API) -> Any:
    accept = "application/vnd.github+json" if api == GITHUB_API else "application/json"
    status, body = _api(path, deadline, MAX_JSON, accept, api)
    if status in (404, 451):
        raise Unavailable("missing")
    if status != 200:
        raise Unavailable("failed")
    try:
        return json.loads(body)
    except ValueError:
        raise Unavailable("failed")


def fetch(repo: Repo, deadline: float) -> dict[str, Any]:
    """What the page shows, from the site's API: the repository, its root
    listing and its README, rendered and cleaned here."""
    return _FETCHERS[repo.host.kind](repo, deadline)


def _q(value: str) -> str:
    return quote(value, safe="")


def _page_data(repo: Repo, owner: str, name: str, ref: str, info: dict[str, Any],
               items: list[tuple[str, str]], truncated: bool, readme: str) -> dict[str, Any]:
    """The page's data, the same shape for every site. ``info`` holds the
    facts; stars and forks are None where the site doesn't say."""
    entries = [{"name": item, "type": kind,
                "url": _site_url(repo.host, owner, name, ref, item, kind)}
               for item, kind in items[:MAX_ENTRIES]]
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return {
        "site": repo.host.site,
        "full_name": "{}/{}".format(owner, name),
        "html_url": _site_url(repo.host, owner, name, ref if repo.ref else "", "", "dir"),
        "description": str(info.get("description") or ""),
        "language": str(info.get("language") or ""),
        "stars": info.get("stars"),
        "forks": info.get("forks"),
        "pushed_at": str(info.get("pushed_at") or ""),
        "archived": bool(info.get("archived")),
        "ref": ref,
        "entries": entries,
        "truncated": truncated or len(items) > MAX_ENTRIES,
        "readme": readme,
    }


def _count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _fetch_github(repo: Repo, deadline: float) -> dict[str, Any]:
    """Three calls; the README comes rendered by GitHub."""
    api = repo.host.api
    base = "/repos/{}/{}".format(quote(repo.owner), quote(repo.name))
    info = _json(base, deadline, api)
    if not isinstance(info, dict) or info.get("private"):
        raise Unavailable("missing")
    ref = repo.ref or str(info.get("default_branch") or "")
    listing = _json(base + "/contents/?ref=" + _q(ref), deadline, api)
    if not isinstance(listing, list):
        raise Unavailable("failed")
    status, readme = _api(base + "/readme?ref=" + _q(ref), deadline, MAX_README,
                          "application/vnd.github.html+json", api)
    if status not in (200, 404):
        raise Unavailable("failed")
    owner, name = _owner_and_name(info.get("full_name"), repo)
    readme_html = ""
    if status == 200:
        text = readme.decode("utf-8", "replace")
        found = re.search(r'data-path="([^"]+)"', text)
        readme_html = clean_readme(text, *_readme_bases(
            repo.host, owner, name, ref, found.group(1) if found else "README.md"))
    facts = {"description": info.get("description"), "language": info.get("language"),
             "stars": _count(info.get("stargazers_count")),
             "forks": _count(info.get("forks_count")),
             "pushed_at": info.get("pushed_at"), "archived": info.get("archived")}
    return _page_data(repo, owner, name, ref, facts, _items(listing, "name", ("dir",)),
                      len(listing) > MAX_ENTRIES, readme_html)


def _fetch_gitea(repo: Repo, deadline: float) -> dict[str, Any]:
    """Codeberg, Forgejo and Gitea: two or three calls; the README is
    raw (the render endpoint needs a token), rendered here."""
    api = repo.host.api
    base = "/repos/{}/{}".format(_q(repo.owner), _q(repo.name))
    info = _json(base, deadline, api)
    if not isinstance(info, dict) or info.get("private") or info.get("internal"):
        raise Unavailable("missing")
    ref = repo.ref or str(info.get("default_branch") or "")
    listing: Any = []
    if not info.get("empty") and ref:
        listing = _json(base + "/contents/?ref=" + _q(ref), deadline, api)
    if not isinstance(listing, list):
        raise Unavailable("failed")
    owner, name = _owner_and_name(info.get("full_name"), repo)
    items = _items(listing, "name", ("dir",))
    readme = _readme(repo.host, owner, name, ref, items, deadline,
                     lambda path: base + "/raw/" + quote(path) + "?ref=" + _q(ref))
    facts = {"description": info.get("description"), "language": info.get("language"),
             "stars": _count(info.get("stars_count")), "forks": _count(info.get("forks_count")),
             "pushed_at": info.get("updated_at"), "archived": info.get("archived")}
    return _page_data(repo, owner, name, ref, facts, items, False, readme)


def _fetch_gitlab(repo: Repo, deadline: float) -> dict[str, Any]:
    """GitLab: two or three calls. Its anonymous project answer has no
    language or archived flag; the root's first PAGE_ENTRIES only."""
    api = repo.host.api
    base = "/projects/" + _q(repo.owner + "/" + repo.name)
    info = _json(base, deadline, api)
    if not isinstance(info, dict) or info.get("visibility", "public") != "public":
        raise Unavailable("missing")
    ref = repo.ref or str(info.get("default_branch") or "")
    listing: Any = []
    if ref:  # An empty project has no default branch.
        listing = _json("{}/repository/tree?per_page={}&ref={}".format(
            base, PAGE_ENTRIES, _q(ref)), deadline, api)
    if not isinstance(listing, list):
        raise Unavailable("failed")
    owner, name = _owner_and_name(info.get("path_with_namespace"), repo)
    items = _items(listing, "name", ("tree",))
    readme = _readme(repo.host, owner, name, ref, items, deadline,
                     lambda path: "{}/repository/files/{}/raw?ref={}".format(
                         base, _q(path), _q(ref)))
    facts = {"description": info.get("description"),
             "stars": _count(info.get("star_count")), "forks": _count(info.get("forks_count")),
             "pushed_at": info.get("last_activity_at"), "archived": info.get("archived")}
    return _page_data(repo, owner, name, ref, facts, items,
                      len(listing) >= PAGE_ENTRIES, readme)


def _fetch_bitbucket(repo: Repo, deadline: float) -> dict[str, Any]:
    """Bitbucket: two or three calls. It counts no stars, and forks would
    take another call, so neither is shown; the root's first PAGE_ENTRIES."""
    api = repo.host.api
    base = "/repositories/{}/{}".format(_q(repo.owner), _q(repo.name))
    info = _json(base, deadline, api)
    if not isinstance(info, dict) or info.get("is_private") is not False:
        raise Unavailable("missing")
    branch = info.get("mainbranch")
    ref = repo.ref or (str(branch.get("name") or "") if isinstance(branch, dict) else "")
    page: Any = {"values": []}
    if ref:  # An empty repository has no main branch.
        page = _json("{}/src/{}/?pagelen={}".format(base, _q(ref), PAGE_ENTRIES), deadline, api)
    if not isinstance(page, dict) or not isinstance(page.get("values"), list):
        raise Unavailable("failed")
    owner, name = _owner_and_name(info.get("full_name"), repo)
    items = _items(page["values"], "path", ("commit_directory",))
    readme = _readme(repo.host, owner, name, ref, items, deadline,
                     lambda path: "{}/src/{}/{}".format(base, _q(ref), quote(path)))
    facts = {"description": info.get("description"), "language": info.get("language"),
             "pushed_at": info.get("updated_on")}
    return _page_data(repo, owner, name, ref, facts, items, bool(page.get("next")), readme)


_FETCHERS: dict[str, Callable[[Repo, float], dict[str, Any]]] = {
    "github": _fetch_github, "gitea": _fetch_gitea,
    "gitlab": _fetch_gitlab, "bitbucket": _fetch_bitbucket,
}


def _items(listing: list[Any], name_key: str, dir_types: tuple[str, ...]
           ) -> list[tuple[str, str]]:
    """``(name, "dir" | "file")`` for each listed entry with a usable name."""
    items = []
    for item in listing:
        if not isinstance(item, dict) or not isinstance(item.get(name_key), str):
            continue
        name = item[name_key]
        if not name or "/" in name:
            continue
        items.append((name, "dir" if item.get("type") in dir_types else "file"))
    return items


def _owner_and_name(full: Any, repo: Repo) -> tuple[str, str]:
    """The repository's current names (after a rename), if they're sane."""
    owner, _, name = str(full or "").rpartition("/")
    if owner and _segments_ok(owner.split("/") + [name]) \
            and (repo.host.kind == "gitlab" or "/" not in owner):
        return owner, name
    return repo.owner, repo.name


def _site_url(host: Host, owner: str, name: str, ref: str, path: str, kind: str) -> str:
    """The site's own page for the repository (no path), a folder or a file."""
    url = "https://{}/{}/{}".format(host.host, owner, name)
    ref = quote(ref, safe="")
    if host.kind == "gitea":
        if not path:
            return url + "/src/branch/" + ref if ref else url
        return "{}/src/branch/{}/{}".format(url, ref, quote(path))
    if host.kind == "gitlab":
        if not path:
            return url + "/-/tree/" + ref if ref else url
        return "{}/-/{}/{}/{}".format(url, "tree" if kind == "dir" else "blob", ref, quote(path))
    if host.kind == "bitbucket":
        if not path:
            return url + "/src/" + ref + "/" if ref else url
        return "{}/src/{}/{}{}".format(url, ref, quote(path), "/" if kind == "dir" else "")
    if not path:
        return url + "/tree/" + ref if ref else url
    return "{}/{}/{}/{}".format(url, "tree" if kind == "dir" else "blob", ref, quote(path))


def _readme_bases(host: Host, owner: str, name: str, ref: str, path: str) -> tuple[str, str]:
    """The folder addresses the README's relative links (the site's file
    pages) and images (raw files) are resolved against."""
    folder = posixpath.dirname(path)
    tail = "{}/{}".format(quote(ref, safe=""), quote(folder) + "/" if folder else "")
    url = "https://{}/{}/{}".format(host.host, owner, name)
    if host.kind == "gitea":
        return url + "/src/branch/" + tail, url + "/raw/branch/" + tail
    if host.kind == "gitlab":
        return url + "/-/blob/" + tail, url + "/-/raw/" + tail
    if host.kind == "bitbucket":
        return url + "/src/" + tail, url + "/raw/" + tail
    if host.host == "github.com":
        return (url + "/blob/" + tail,
                "https://raw.githubusercontent.com/{}/{}/{}".format(owner, name, tail))
    return url + "/blob/" + tail, url + "/raw/" + tail  # GitHub Enterprise


# The README, on sites that send it raw

_MARKDOWN = (".md", ".markdown", ".mdown", ".mkd")
_README_NAME = re.compile(r"readme(\.[a-z]{1,8})?", re.IGNORECASE)


def _pick_readme(items: list[tuple[str, str]]) -> Optional[str]:
    """The root's README file, Markdown first, as the sites pick it."""
    found = [n for n, kind in items if kind == "file" and _README_NAME.fullmatch(n)]
    found.sort(key=lambda n: (not n.lower().endswith(_MARKDOWN), len(n), n))
    return found[0] if found else None


def _readme(host: Host, owner: str, name: str, ref: str, items: list[tuple[str, str]],
            deadline: float, raw_path: Callable[[str], str]) -> str:
    """The README's cleaned HTML, "" when there is none. A README that
    fails to load leaves the rest of the page showing."""
    path = _pick_readme(items)
    if path is None:
        return ""
    status, body = _api(raw_path(path), deadline, MAX_README, "text/plain", host.api)
    if status == 404:
        return ""
    if status != 200:
        raise Unavailable("failed")
    return clean_readme(render_readme(body.decode("utf-8", "replace"), path),
                        *_readme_bases(host, owner, name, ref, path))


def render_readme(text: str, path: str) -> str:
    """A raw README as HTML, not yet cleaned: Markdown with GitHub's own
    cmark-gfm (tables, task lists, autolinks, strikethrough), anything else
    as preformatted text."""
    if not path.lower().endswith(_MARKDOWN):
        return "<pre>{}</pre>".format(html_lib.escape(text))
    import cmarkgfm
    from cmarkgfm.cmark import Options

    # UNSAFE keeps a README's raw HTML (a centred logo, <details>) for nh3
    # to clean, as GitHub does; without it cmark drops all of it.
    rendered = cmarkgfm.github_flavored_markdown_to_html(text, options=Options.CMARK_OPT_UNSAFE)
    return _heading_ids(rendered)


_HEADING = re.compile(r"<h([1-6])>(.*?)</h\1>", re.DOTALL)
_TAG = re.compile(r"<[^>]*>")
_SLUG_DROP = re.compile(r"[^\w\- ]")


def _heading_ids(rendered: str) -> str:
    """GitHub's heading ids (``user-content-`` and a slug, ``-1`` and so on
    for repeats), so the README's ``#`` links scroll as on GitHub."""
    seen: dict[str, int] = {}

    def add(match: "re.Match[str]") -> str:
        text = html_lib.unescape(_TAG.sub("", match.group(2)))
        slug = _SLUG_DROP.sub("", text.strip().lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        if count:
            slug = "{}-{}".format(slug, count)
        return '<h{0} id="user-content-{1}">{2}</h{0}>'.format(
            match.group(1), html_lib.escape(slug), match.group(2))

    return _HEADING.sub(add, rendered)


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


def clean_readme(html: str, blob: str, raw: str) -> str:
    """A rendered README, cleaned with nh3 to a fixed list of tags and
    attributes. Relative links resolve against ``blob`` (the README's folder
    on the site) and relative images against ``raw`` (its raw files); in-page
    links point at ``user-content-`` ids, as GitHub's headings have."""
    import nh3

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
    return _ANCHOR.sub(_in_page, cleaned)


# nh3 serializes every attribute double-quoted, with any '"' escaped, so this
# matches whole <a> tags whatever their attributes or order.
_ANCHOR = re.compile(r'<a((?:\s+[\w-]+="[^"]*")*)\s*>')
_ATTRIBUTE = re.compile(r'\s+([\w-]+)="([^"]*)"')


def _in_page(match: "re.Match[str]") -> str:
    attributes = _ATTRIBUTE.findall(match.group(1))
    if not any(name == "href" and value.startswith("#") for name, value in attributes):
        return match.group(0)
    return "<a{}>".format("".join(
        ' {}="{}"'.format(name, value) for name, value in attributes
        if name not in ("target", "rel")))


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
    remembered for FAILED_SECONDS, or until the site's limit resets; when a
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
                        host=repo.host.host)
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
        "site": found.host.site if found is not None else "",
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
