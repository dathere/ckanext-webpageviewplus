import json
import sys
import time
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

import ckan.plugins as plugins
from ckan.exceptions import CkanConfigurationException
from ckan.logic import ValidationError
from ckan.tests import factories

from ckanext.webpageviewplus import frameability, plugin, repos, settings

pytest.importorskip("nh3")

REPO = "https://github.com/ckan/ckan"
INFO = {
    "full_name": "ckan/ckan", "description": "The open source data portal",
    "language": "Python", "stargazers_count": 5150, "forks_count": 2123,
    "pushed_at": "2026-11-03T12:57:01Z", "default_branch": "master",
    "private": False, "archived": False,
}
LISTING = [
    {"name": "setup.py", "type": "file"},
    {"name": "ckan", "type": "dir"},
    {"name": "README.rst", "type": "file"},
]
README = ('<div id="readme" class="rst" data-path="README.rst"><article>'
          '<h1>CKAN</h1><p>See <a href="doc/index.rst">the docs</a>.</p>'
          '<img src="doc/logo.png"><script>alert(1)</script></article></div>')


@pytest.mark.parametrize("url,expected", [
    (REPO, repos.Repo("ckan", "ckan")),
    ("https://www.github.com/ckan/ckan/", repos.Repo("ckan", "ckan")),
    ("http://GITHUB.com./ckan/ckan.git", repos.Repo("ckan", "ckan")),
    ("https://github.com/ckan/ckan/tree/dev-v2.11", repos.Repo("ckan", "ckan", "dev-v2.11")),
    ("https://github.com/ckan/ckan?tab=readme#top", repos.Repo("ckan", "ckan")),
    # Not a repository's root.
    ("https://github.com/ckan", None),
    ("https://github.com/ckan/ckan/tree/master/doc", None),
    ("https://github.com/ckan/ckan/blob/master/README.rst", None),
    ("https://github.com/ckan/ckan/issues", None),
    ("https://github.com/orgs/ckan", None),
    ("https://github.com/settings/profile", None),
    ("https://gist.github.com/someone/abc", None),
    ("https://github.com/ckan/ckan/tree/..", None),
    ("https://github.com/c%2Fk/ckan", None),
    ("https://user@github.com/ckan/ckan", None),
    ("https://github.com:8443/ckan/ckan", None),
    ("", None),
])
def test_parse(url, expected):
    assert repos.parse(url) == expected


class TestCleanReadme:
    def _clean(self, html, path="README.md"):
        return repos.clean_readme(html, *repos._readme_bases(
            repos.BUILT_IN["github.com"], "o", "r", "main", path))

    def test_relative_links_and_images_from_the_readmes_folder(self):
        out = self._clean('<a href="guide.md">g</a><img src="img/a.png">', "docs/README.md")
        assert 'href="https://github.com/o/r/blob/main/docs/guide.md"' in out
        assert 'src="https://raw.githubusercontent.com/o/r/main/docs/img/a.png"' in out

    def test_links_open_in_a_new_tab_but_in_page_links_scroll(self):
        out = self._clean('<a href="https://ckan.org">a</a><a href="#install">b</a>')
        assert '<a href="https://ckan.org" target="_blank" rel="noopener noreferrer">' in out
        assert '<a href="#user-content-install">b</a>' in out

    def test_in_page_links_scroll_whatever_their_attributes(self):
        out = self._clean(
            '<a id="user-content-install" class="anchor" href="#install">a</a>'
            '<a href="#usage" title="Say &quot;hi&quot; > there">b</a>')
        assert '<a id="user-content-install" href="#user-content-install">a</a>' in out
        assert 'href="#user-content-usage" title=' in out
        assert "target" not in out and "rel=" not in out

    @pytest.mark.parametrize("html", [
        '<a href="javascript:alert(1)">x</a>',
        '<a href=" java&#9;script:alert(1)">x</a>',
        '<img src="x" onerror="alert(1)">',
        "<script>alert(1)</script>",
        "<svg><script>alert(1)</script></svg>",
        '<iframe src="https://x.example"></iframe>',
        '<form action="https://x.example"><button>go</button></form>',
        '<p style="background:url(https://x.example)">x</p>',
    ])
    def test_nothing_active_survives(self, html):
        out = self._clean(html).lower()
        for word in ("script", "onerror", "iframe", "<form", "style=", "javascript"):
            assert word not in out

    def test_task_list_checkboxes_are_disabled(self):
        out = self._clean('<input type="checkbox" checked><input type="text">')
        assert '<input type="checkbox" checked="" disabled="disabled">' in out
        assert '<input disabled="disabled">' in out


@pytest.fixture
def api(monkeypatch):
    """GitHub's answers by API path, counting the calls."""
    answers = {
        "/repos/ckan/ckan": (200, json.dumps(INFO).encode()),
        "/repos/ckan/ckan/contents/?ref=master": (200, json.dumps(LISTING).encode()),
        "/repos/ckan/ckan/readme?ref=master": (200, README.encode()),
    }
    calls = []

    def fake(path, deadline, max_bytes, accept, api=repos.GITHUB_API):
        # GitHub's answers by path; other sites' by their whole address.
        key = path if api == repos.GITHUB_API else api + path
        calls.append(key)
        answer = answers.get(key, (404, b"{}"))
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(repos, "_api", fake)
    return answers, calls


@pytest.fixture
def storage(monkeypatch, tmp_path, ckan_config):
    monkeypatch.setitem(ckan_config, "ckan.storage_path", str(tmp_path))
    return tmp_path


class TestFetch:
    def test_page_data(self, api):
        data = repos.fetch(repos.Repo("ckan", "ckan"), time.monotonic() + 5)
        assert data["full_name"] == "ckan/ckan"
        assert data["stars"] == 5150 and data["ref"] == "master"
        assert [e["name"] for e in data["entries"]] == ["ckan", "README.rst", "setup.py"]
        assert data["entries"][0]["url"] == "https://github.com/ckan/ckan/tree/master/ckan"
        assert data["entries"][2]["url"] == "https://github.com/ckan/ckan/blob/master/setup.py"
        assert "https://github.com/ckan/ckan/blob/master/doc/index.rst" in data["readme"]
        assert "script" not in data["readme"]

    def test_no_readme(self, api):
        answers, _ = api
        del answers["/repos/ckan/ckan/readme?ref=master"]
        assert repos.fetch(repos.Repo("ckan", "ckan"), time.monotonic() + 5)["readme"] == ""

    def test_missing(self, api):
        with pytest.raises(repos.Unavailable) as raised:
            repos.fetch(repos.Repo("ckan", "nope"), time.monotonic() + 5)
        assert raised.value.kind == "missing"

    def test_private_counts_as_missing(self, api):
        answers, _ = api
        answers["/repos/ckan/ckan"] = (200, json.dumps(dict(INFO, private=True)).encode())
        with pytest.raises(repos.Unavailable) as raised:
            repos.fetch(repos.Repo("ckan", "ckan"), time.monotonic() + 5)
        assert raised.value.kind == "missing"


@pytest.mark.usefixtures("storage")
class TestCached:
    def test_fetched_once_and_shared(self, api):
        _, calls = api
        first, problem = repos.cached(repos.Repo("ckan", "ckan"))
        assert problem == "" and first["full_name"] == "ckan/ckan"
        again, _ = repos.cached(repos.Repo("CKAN", "CKAN"))
        assert again == first and len(calls) == 3

    def test_failure_remembered(self, api):
        _, calls = api
        assert repos.cached(repos.Repo("ckan", "nope")) == (None, "missing")
        assert repos.cached(repos.Repo("ckan", "nope")) == (None, "missing")
        assert len(calls) == 1

    def test_rate_limit_remembered_until_reset(self, api, monkeypatch):
        answers, calls = api
        answers["/repos/ckan/ckan"] = repos.Unavailable("rate_limited", time.time() + 3600)
        assert repos.cached(repos.Repo("ckan", "ckan")) == (None, "rate_limited")
        monkeypatch.setattr(repos, "FAILED_SECONDS", 0)
        assert repos.cached(repos.Repo("ckan", "ckan")) == (None, "rate_limited")
        assert len(calls) == 1

    def test_stale_copy_served_when_a_refresh_fails(self, api, ckan_config, monkeypatch):
        answers, _ = api
        fresh, _ = repos.cached(repos.Repo("ckan", "ckan"))
        monkeypatch.setitem(ckan_config, "ckanext.webpageviewplus.repo_cache_seconds", 0)
        answers["/repos/ckan/ckan"] = repos.Unavailable("failed")
        assert repos.cached(repos.Repo("ckan", "ckan")) == (fresh, "")

    def _hold_lock(self, repo):
        import hashlib
        import os
        folder = repos._folder()
        os.makedirs(folder, exist_ok=True)
        open(os.path.join(folder, hashlib.sha256(repo.key.encode()).hexdigest() + ".lock"),
             "w").close()

    def test_another_worker_fetching_and_no_copy_yet(self, api):
        _, calls = api
        self._hold_lock(repos.Repo("ckan", "ckan"))
        assert repos.cached(repos.Repo("ckan", "ckan")) == (None, "failed")
        assert calls == []

    def test_another_worker_fetching_serves_the_old_copy(self, api, ckan_config, monkeypatch):
        _, calls = api
        old, _ = repos.cached(repos.Repo("ckan", "ckan"))
        monkeypatch.setitem(ckan_config, "ckanext.webpageviewplus.repo_cache_seconds", 0)
        self._hold_lock(repos.Repo("ckan", "ckan"))
        fetched = len(calls)
        assert repos.cached(repos.Repo("ckan", "ckan")) == (old, "")
        assert len(calls) == fetched

    def test_no_storage_path(self, api, ckan_config, monkeypatch):
        monkeypatch.setitem(ckan_config, "ckan.storage_path", "")
        assert repos.cached(repos.Repo("ckan", "ckan")) == (None, "no_storage")


def _page(view):
    return "/webpageviewplus/repo/" + view["id"]


def _read(dataset, resource, view):
    return "/dataset/{}/resource/{}?view_id={}".format(dataset["name"], resource["id"], view["id"])


def _view(resource, **fields):
    return factories.ResourceView(resource_id=resource["id"], view_type=repos.VIEW_TYPE,
                                  title="Repository", **fields)


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus repo_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "storage")
class TestView:
    def test_offered_for_repository_links_only(self):
        plugin = plugins.get_plugin(repos.VIEW_TYPE)
        assert plugin.can_view({"resource": {"url": REPO}})
        assert not plugin.can_view({"resource": {"url": REPO + "/issues"}})
        assert not plugin.can_view({"resource": {"url": "page.html", "url_type": "upload"}})

    def test_save_refuses_a_non_repository(self):
        resource = factories.Resource(url="https://example.com/")
        with pytest.raises(ValidationError):
            _view(resource)
        assert _view(resource, repo_url=REPO)["repo_url"] == REPO

    @pytest.mark.parametrize("mode, value, message", [
        ("fixed", "tall", "Fixed takes a number of pixels"),
        ("viewport", "150", "A share of the window takes a number from 1 to 100"),
    ])
    def test_save_refuses_a_bad_height(self, mode, value, message):
        resource = factories.Resource(url=REPO)
        with pytest.raises(ValidationError) as raised:
            _view(resource, height_mode=mode, height_value=value)
        assert any(message in error for error in raised.value.error_dict["height_value"])

    @pytest.mark.parametrize("mode, value", [("fixed", "600"), ("viewport", "80")])
    def test_save_keeps_a_good_height(self, mode, value):
        view = _view(factories.Resource(url=REPO), height_mode=mode, height_value=value)
        assert (view["height_mode"], view["height_value"]) == (mode, value)

    def test_page(self, app, api):
        view = _view(factories.Resource(url=REPO))
        response = app.get(_page(view))
        assert response.status_code == 200
        csp = response.headers["Content-Security-Policy"]
        assert csp.startswith("sandbox allow-popups allow-popups-to-escape-sandbox;")
        assert "default-src 'none'" in csp and "script-src" not in csp
        page = BeautifulSoup(response.body, "html.parser")
        assert page.select_one("h1").get_text(strip=True) == "ckan/ckan"
        assert [a.get_text() for a in page.select(".repo-files a")] == [
            "ckan", "README.rst", "setup.py"]
        assert page.select_one(".repo-readme h1").get_text() == "CKAN"
        assert "Updated November 3, 2026" in page.select_one(".repo-facts").get_text()
        assert page.find("script") is None

    def test_style_matches_its_hash(self, app, api):
        import base64
        import hashlib
        view = _view(factories.Resource(url=REPO))
        response = app.get(_page(view))
        style = BeautifulSoup(response.body, "html.parser").find("style").string
        digest = base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
        assert "'sha256-{}'".format(digest) in response.headers["Content-Security-Policy"]

    def test_card_when_it_cant_be_fetched(self, app, api):
        view = _view(factories.Resource(url="https://github.com/ckan/nope"))
        response = app.get(_page(view))
        page = BeautifulSoup(response.body, "html.parser")
        assert "wasn't found" in page.select_one(".repo-card").get_text()
        assert page.select_one(".repo-card a")["href"] == "https://github.com/ckan/nope"

    def test_private_dataset_needs_access(self, app, api):
        org = factories.Organization()
        dataset = factories.Dataset(private=True, owner_org=org["id"])
        view = _view(factories.Resource(package_id=dataset["id"], url=REPO))
        url = _page(view)
        assert app.get(url).status_code == 404
        sysadmin = factories.SysadminWithToken()
        assert app.get(url, headers={"Authorization": sysadmin["token"]}).status_code == 200

    def test_other_view_types_refused(self, app):
        view = factories.ResourceView(resource_id=factories.Resource(url=REPO)["id"],
                                      view_type="webpage_view_plus")
        assert app.get(_page(view)).status_code == 404

    def test_rendered_in_a_frame_without_scripts(self, app):
        dataset = factories.Dataset()
        resource = factories.Resource(package_id=dataset["id"], url=REPO)
        view = _view(resource)
        body = app.get(_read(dataset, resource, view)).body
        iframe = BeautifulSoup(body, "html.parser").select_one(".webpageviewplus iframe")
        assert " ".join(iframe["sandbox"]) == "allow-popups allow-popups-to-escape-sandbox"
        assert iframe["src"].endswith("/webpageviewplus/repo/" + view["id"])
        # GitHub refuses framing, so Reload must load CKAN's page, not GitHub's.
        reload = BeautifulSoup(body, "html.parser").select_one('[data-webpageviewplus-action="reload"]')
        assert reload["data-reload-url"] == iframe["src"]

    @pytest.mark.ckan_config("ckanext.webpageviewplus.privacy_profile", "eu")
    def test_consent_card_names_github(self, app):
        dataset = factories.Dataset()
        resource = factories.Resource(package_id=dataset["id"], url=REPO)
        view = _view(resource)
        body = app.get(_read(dataset, resource, view)).body
        assert "github.com" in BeautifulSoup(body, "html.parser").select_one(
            ".webpageviewplus").get_text()


@pytest.mark.parametrize("key", ["repo_cache_seconds", "pdf_cache_seconds"])
def test_negative_cache_seconds_stop_startup(key, ckan_config, monkeypatch):
    monkeypatch.setitem(ckan_config, "ckanext.webpageviewplus." + key, "-1")
    with pytest.raises(CkanConfigurationException, match=key):
        settings.check()


class _Response:
    def __init__(self, status=200, body=b"{}", headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.is_redirect = status in (301, 302, 307, 308)
        self._body = body

    def iter_content(self, size):
        for start in range(0, len(self._body), size):
            yield self._body[start:start + size]

    def close(self):
        pass


@pytest.fixture
def http(monkeypatch):
    """The real repos._api over faked HTTP: answers by URL, in order of
    request, recording each request's URL and headers."""
    answers: dict = {}
    requests_made: list = []

    def get(url, stream, timeout, allow_redirects, headers):
        requests_made.append((url, dict(headers)))
        answer = answers.get(url, _Response(404))
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(frameability, "_address_kind", lambda url, deadline: "public")
    monkeypatch.setattr(frameability, "_http_get", get)
    return answers, requests_made


def _call(path="/repos/o/r", max_bytes=1000):
    return repos._api(path, time.monotonic() + 10, max_bytes, "application/json")


class TestApi:
    @pytest.mark.ckan_config("ckanext.webpageviewplus.github_token", "github_pat_x")
    def test_token_sent_and_renamed_repository_followed(self, http):
        answers, made = http
        answers[repos.GITHUB_API + "/repos/o/r"] = _Response(
            301, headers={"Location": repos.GITHUB_API + "/repositories/42"})
        answers[repos.GITHUB_API + "/repositories/42"] = _Response(200, b'{"ok": 1}')
        assert _call() == (200, b'{"ok": 1}')
        assert [url for url, _ in made] == [repos.GITHUB_API + "/repos/o/r",
                                            repos.GITHUB_API + "/repositories/42"]
        assert all(h["Authorization"] == "Bearer github_pat_x" for _, h in made)

    @pytest.mark.ckan_config("ckanext.webpageviewplus.github_token", "github_pat_x")
    @pytest.mark.parametrize("location", [
        "https://evil.example/steal",
        "https://api.github.com.evil.example/x",
        "http://api.github.com/repos/o/r",
        "//evil.example/x",
    ])
    def test_redirect_off_the_api_is_refused_before_the_token_leaves(self, http, location):
        answers, made = http
        answers[repos.GITHUB_API + "/repos/o/r"] = _Response(302, headers={"Location": location})
        with pytest.raises(repos.Unavailable) as raised:
            _call()
        assert raised.value.kind == "failed"
        assert len(made) == 1

    def test_too_many_redirects(self, http):
        answers, made = http
        answers[repos.GITHUB_API + "/repos/o/r"] = _Response(
            302, headers={"Location": repos.GITHUB_API + "/repos/o/r"})
        with pytest.raises(repos.Unavailable):
            _call()
        assert len(made) == frameability.MAX_REDIRECTS + 1

    @pytest.mark.parametrize("status, headers", [
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1900000000"}),
        (429, {"X-RateLimit-Reset": "1900000000"}),
    ])
    def test_rate_limited_until_reset(self, http, status, headers):
        answers, _ = http
        answers[repos.GITHUB_API + "/repos/o/r"] = _Response(status, headers=headers)
        with pytest.raises(repos.Unavailable) as raised:
            _call()
        assert (raised.value.kind, raised.value.until) == ("rate_limited", 1900000000.0)

    def test_rate_limit_with_a_bad_reset_header(self, http):
        answers, _ = http
        answers[repos.GITHUB_API + "/repos/o/r"] = _Response(
            429, headers={"X-RateLimit-Reset": "soon"})
        with pytest.raises(repos.Unavailable) as raised:
            _call()
        assert (raised.value.kind, raised.value.until) == ("rate_limited", 0.0)

    def test_other_403_is_not_a_rate_limit(self, http):
        answers, _ = http
        answers[repos.GITHUB_API + "/repos/o/r"] = _Response(
            403, headers={"X-RateLimit-Remaining": "12"})
        assert _call()[0] == 403
        with pytest.raises(repos.Unavailable) as raised:
            repos._json("/repos/o/r", time.monotonic() + 10)
        assert raised.value.kind == "failed"

    def test_body_over_the_cap(self, http):
        answers, _ = http
        answers[repos.GITHUB_API + "/repos/o/r"] = _Response(200, b"x" * 1001)
        with pytest.raises(repos.Unavailable):
            _call(max_bytes=1000)

    def test_private_address_never_requested(self, http, monkeypatch):
        _, made = http
        monkeypatch.setattr(frameability, "_address_kind", lambda url, deadline: "private")
        with pytest.raises(repos.Unavailable):
            _call()
        assert made == []

    def test_connection_error_is_failed(self, http):
        import requests
        answers, _ = http
        answers[repos.GITHUB_API + "/repos/o/r"] = requests.ConnectionError()
        with pytest.raises(repos.Unavailable) as raised:
            _call()
        assert raised.value.kind == "failed"

    @pytest.mark.parametrize("status, body, kind", [
        (404, b"{}", "missing"),
        (451, b"{}", "missing"),
        (500, b"{}", "failed"),
        (200, b"<html>not json", "failed"),
    ])
    def test_json_answers(self, http, status, body, kind):
        answers, _ = http
        answers[repos.GITHUB_API + "/repos/o/r"] = _Response(status, body)
        with pytest.raises(repos.Unavailable) as raised:
            repos._json("/repos/o/r", time.monotonic() + 10)
        assert raised.value.kind == kind


class TestStartup:
    def _plugins(self, monkeypatch, loaded):
        # Patches plugin.py's own name only, as test_pdf.test_needs_main_plugin
        # explains: patching ckan.plugins.plugin_loaded fools with_plugins.
        monkeypatch.setattr(plugin, "plugins", SimpleNamespace(
            plugin_loaded=lambda name: loaded, SingletonPlugin=plugin.plugins.SingletonPlugin))

    def test_needs_main_plugin(self, monkeypatch):
        self._plugins(monkeypatch, False)
        with pytest.raises(CkanConfigurationException, match="webpage_view_plus"):
            plugin.RepoViewPlusPlugin().configure({})

    def test_needs_nh3(self, monkeypatch):
        self._plugins(monkeypatch, True)
        monkeypatch.setitem(sys.modules, "nh3", None)
        with pytest.raises(CkanConfigurationException, match="nh3"):
            plugin.RepoViewPlusPlugin().configure({})

    def test_starts_with_both(self, monkeypatch):
        self._plugins(monkeypatch, True)
        plugin.RepoViewPlusPlugin().configure({})


# Other sites

CODEBERG = repos.BUILT_IN["codeberg.org"]
GITLAB = repos.BUILT_IN["gitlab.com"]
BITBUCKET = repos.BUILT_IN["bitbucket.org"]


@pytest.mark.parametrize("url,expected", [
    ("https://codeberg.org/forgejo/forgejo", repos.Repo("forgejo", "forgejo", "", CODEBERG)),
    ("https://codeberg.org/forgejo/forgejo.git", repos.Repo("forgejo", "forgejo", "", CODEBERG)),
    ("https://codeberg.org/forgejo/forgejo/src/branch/v9.0",
     repos.Repo("forgejo", "forgejo", "v9.0", CODEBERG)),
    ("https://gitlab.com/gitlab-org/gitlab-runner",
     repos.Repo("gitlab-org", "gitlab-runner", "", GITLAB)),
    ("https://gitlab.com/a/b/c/project", repos.Repo("a/b/c", "project", "", GITLAB)),
    ("https://gitlab.com/a/b/project/-/tree/main", repos.Repo("a/b", "project", "main", GITLAB)),
    ("https://bitbucket.org/atlassian/connect", repos.Repo("atlassian", "connect", "", BITBUCKET)),
    ("https://bitbucket.org/atlassian/connect/src/master/",
     repos.Repo("atlassian", "connect", "master", BITBUCKET)),
    # Not a repository's root, or a site's own pages.
    ("https://codeberg.org/forgejo", None),
    ("https://codeberg.org/forgejo/forgejo/tree/main", None),
    ("https://codeberg.org/forgejo/forgejo/src/branch/main/docs", None),
    ("https://codeberg.org/explore/repos", None),
    ("https://codeberg.org/forgejo/forgejo/issues", None),
    ("https://gitlab.com/gitlab-org", None),
    ("https://gitlab.com/gitlab-org/gitlab-runner/-/issues", None),
    ("https://gitlab.com/gitlab-org/gitlab-runner/-/tree/main/docs", None),
    ("https://gitlab.com/explore/projects", None),
    ("https://gitlab.com/" + "/".join(["g"] * 22), None),
    ("https://bitbucket.org/atlassian", None),
    ("https://bitbucket.org/atlassian/connect/src/master/lib/", None),
    ("https://bitbucket.org/account/signin", None),
    # A site that isn't in repo_hosts.
    ("https://git.example.org/o/r", None),
])
def test_parse_other_sites(url, expected):
    assert repos.parse(url) == expected


def test_built_in_hosts_match_settings():
    assert tuple(repos.BUILT_IN) == settings.REPO_BUILT_IN_HOSTS
    assert set(repos._SELF_HOSTED_API) == set(settings.REPO_HOST_KINDS)


def test_github_cache_key_unchanged():
    assert repos.Repo("CKAN", "CKAN", "dev").key == "github.com/ckan/ckan@dev"


@pytest.mark.parametrize("value,url,host", [
    ("git.example.org gitea", "https://git.example.org/o/r/src/branch/main",
     repos.Host("gitea", "git.example.org", "https://git.example.org/api/v1", "git.example.org")),
    ("GITLAB.example.org gitlab", "https://gitlab.example.org/g/s/p",
     repos.Host("gitlab", "gitlab.example.org", "https://gitlab.example.org/api/v4",
                "gitlab.example.org")),
    ("ghe.example.org github", "https://ghe.example.org/o/r/tree/main",
     repos.Host("github", "ghe.example.org", "https://ghe.example.org/api/v3", "ghe.example.org")),
])
def test_repo_hosts(value, url, host, ckan_config, monkeypatch):
    monkeypatch.setitem(ckan_config, "ckanext.webpageviewplus.repo_hosts", value)
    settings.check()
    found = repos.parse(url)
    assert found is not None and found.host == host
    assert found.html_url == url


@pytest.mark.parametrize("value,message", [
    ("git.example.org", "give each host with its kind"),
    ("git.example.org bitbucket", "the kind must be one of github, gitea, gitlab"),
    ("https://git.example.org gitea", "give a host name only"),
    ("git.example.org:8443 gitea", "give a host name only"),
    ("git.example.org/x gitea", "give a host name only"),
    ("gitlab.com gitlab", "is already supported"),
    ("www.github.com github", "is already supported"),
    ("a.example.org gitea a.example.org gitlab", "is listed twice"),
])
def test_bad_repo_hosts_stop_startup(value, message, ckan_config, monkeypatch):
    monkeypatch.setitem(ckan_config, "ckanext.webpageviewplus.repo_hosts", value)
    with pytest.raises(CkanConfigurationException, match="repo_hosts: .*" + message):
        settings.check()


def test_ckan_host_refused_in_repo_hosts(ckan_config, monkeypatch):
    monkeypatch.setitem(ckan_config, "ckanext.webpageviewplus.site_aliases", "data.example.org")
    monkeypatch.setitem(ckan_config, "ckanext.webpageviewplus.repo_hosts", "DATA.example.org gitea")
    with pytest.raises(CkanConfigurationException, match="one of CKAN's own hosts"):
        settings.check()


def _site_answers(answers, api, entries):
    for path, answer in entries.items():
        answers[api + path] = answer if isinstance(answer, (tuple, Exception)) else (
            200, answer if isinstance(answer, bytes) else json.dumps(answer).encode())


GITEA_INFO = {"full_name": "forgejo/forgejo", "description": "Beyond coding", "language": "Go",
              "stars_count": 3000, "forks_count": 500, "updated_at": "2026-11-01T10:00:00Z",
              "default_branch": "forgejo", "private": False, "archived": False, "empty": False}
GITEA_LISTING = [{"name": "docs", "type": "dir"}, {"name": "README.md", "type": "file"},
                 {"name": "main.go", "type": "file"}, {"name": "README.txt", "type": "file"}]
MARKDOWN = (b'<div align="center"><img src="./assets/logo.svg" onerror="x()"></div>\n\n'
            b"# Welcome\n\nSee [the docs](docs/README.md) and [install](#install).\n\n"
            b"## Install\n\n- [x] done\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
            b"<script>alert(1)</script>\n[x](javascript:alert(1))\n")


class TestFetchOtherSites:
    def test_codeberg(self, api):
        answers, calls = api
        _site_answers(answers, CODEBERG.api, {
            "/repos/forgejo/forgejo": GITEA_INFO,
            "/repos/forgejo/forgejo/contents/?ref=forgejo": GITEA_LISTING,
            "/repos/forgejo/forgejo/raw/README.md?ref=forgejo": MARKDOWN,
        })
        data = repos.fetch(repos.parse("https://codeberg.org/forgejo/forgejo"), time.monotonic() + 5)
        assert data["site"] == "Codeberg" and data["full_name"] == "forgejo/forgejo"
        assert (data["stars"], data["forks"], data["language"]) == (3000, 500, "Go")
        assert data["html_url"] == "https://codeberg.org/forgejo/forgejo"
        assert data["entries"][0] == {
            "name": "docs", "type": "dir",
            "url": "https://codeberg.org/forgejo/forgejo/src/branch/forgejo/docs"}
        readme = data["readme"]
        assert '<h1 id="user-content-welcome">Welcome</h1>' in readme
        assert 'href="#user-content-install"' in readme and 'id="user-content-install"' in readme
        assert 'href="https://codeberg.org/forgejo/forgejo/src/branch/forgejo/docs/README.md"' \
            in readme
        assert 'src="https://codeberg.org/forgejo/forgejo/raw/branch/forgejo/assets/logo.svg"' \
            in readme
        assert '<input type="checkbox" checked="" disabled="disabled">' in readme
        assert "<table>" in readme
        for word in ("<script", "onerror", "javascript:"):
            assert word not in readme
        assert len(calls) == 3

    def test_empty_codeberg_repository(self, api):
        answers, calls = api
        _site_answers(answers, CODEBERG.api, {
            "/repos/o/r": dict(GITEA_INFO, full_name="o/r", empty=True)})
        data = repos.fetch(repos.parse("https://codeberg.org/o/r"), time.monotonic() + 5)
        assert data["entries"] == [] and data["readme"] == "" and len(calls) == 1

    def test_gitlab_nested_group(self, api):
        answers, calls = api
        info = {"path_with_namespace": "group/sub/project", "description": "D",
                "star_count": 7, "forks_count": 2, "last_activity_at": "2026-10-01T00:00:00Z",
                "default_branch": "main", "visibility": "public"}
        listing = [{"name": "src", "type": "tree"}, {"name": "README", "type": "blob"}]
        _site_answers(answers, GITLAB.api, {
            "/projects/group%2Fsub%2Fproject": info,
            "/projects/group%2Fsub%2Fproject/repository/tree?per_page=100&ref=main": listing,
            "/projects/group%2Fsub%2Fproject/repository/files/README/raw?ref=main":
                b"Plain <b>text</b>",
        })
        data = repos.fetch(repos.parse("https://gitlab.com/group/sub/project"),
                           time.monotonic() + 5)
        assert data["full_name"] == "group/sub/project" and data["site"] == "GitLab"
        assert data["language"] == "" and (data["stars"], data["forks"]) == (7, 2)
        assert data["entries"][0]["url"] == "https://gitlab.com/group/sub/project/-/tree/main/src"
        assert data["entries"][1]["url"] == "https://gitlab.com/group/sub/project/-/blob/main/README"
        assert data["readme"] == "<pre>Plain &lt;b&gt;text&lt;/b&gt;</pre>"
        assert not data["truncated"]

    def test_gitlab_full_page_is_truncated(self, api):
        answers, _ = api
        listing = [{"name": "f{}".format(i), "type": "blob"} for i in range(repos.PAGE_ENTRIES)]
        _site_answers(answers, GITLAB.api, {
            "/projects/g%2Fp": {"path_with_namespace": "g/p", "default_branch": "main"},
            "/projects/g%2Fp/repository/tree?per_page=100&ref=main": listing,
        })
        data = repos.fetch(repos.parse("https://gitlab.com/g/p"), time.monotonic() + 5)
        assert data["truncated"] and data["readme"] == ""

    def test_gitlab_empty_project(self, api):
        answers, calls = api
        _site_answers(answers, GITLAB.api, {
            "/projects/g%2Fp": {"path_with_namespace": "g/p", "default_branch": None}})
        data = repos.fetch(repos.parse("https://gitlab.com/g/p"), time.monotonic() + 5)
        assert data["entries"] == [] and len(calls) == 1

    def test_gitlab_private_counts_as_missing(self, api):
        answers, _ = api
        _site_answers(answers, GITLAB.api, {
            "/projects/g%2Fp": {"path_with_namespace": "g/p", "visibility": "internal"}})
        with pytest.raises(repos.Unavailable) as raised:
            repos.fetch(repos.parse("https://gitlab.com/g/p"), time.monotonic() + 5)
        assert raised.value.kind == "missing"

    def test_bitbucket(self, api):
        answers, _ = api
        info = {"full_name": "atlassian/connect", "description": "Connect", "language": "js",
                "is_private": False, "updated_on": "2026-09-01T00:00:00+00:00",
                "mainbranch": {"name": "master"}}
        page = {"values": [{"path": "lib", "type": "commit_directory"},
                           {"path": "README.md", "type": "commit_file"}], "next": "https://x"}
        _site_answers(answers, BITBUCKET.api, {
            "/repositories/atlassian/connect": info,
            "/repositories/atlassian/connect/src/master/?pagelen=100": page,
            "/repositories/atlassian/connect/src/master/README.md": b"# Connect\n\n![](img/a.png)",
        })
        data = repos.fetch(repos.parse("https://bitbucket.org/atlassian/connect"),
                           time.monotonic() + 5)
        assert (data["stars"], data["forks"]) == (None, None) and data["truncated"]
        assert data["entries"][0]["url"] == "https://bitbucket.org/atlassian/connect/src/master/lib/"
        assert 'src="https://bitbucket.org/atlassian/connect/raw/master/img/a.png"' in data["readme"]

    def test_bitbucket_private_counts_as_missing(self, api):
        answers, _ = api
        _site_answers(answers, BITBUCKET.api, {
            "/repositories/w/r": {"full_name": "w/r", "is_private": True}})
        with pytest.raises(repos.Unavailable) as raised:
            repos.fetch(repos.parse("https://bitbucket.org/w/r"), time.monotonic() + 5)
        assert raised.value.kind == "missing"

    def test_readme_failing_to_load_is_failed(self, api):
        answers, _ = api
        _site_answers(answers, CODEBERG.api, {
            "/repos/forgejo/forgejo": GITEA_INFO,
            "/repos/forgejo/forgejo/contents/?ref=forgejo": GITEA_LISTING,
            "/repos/forgejo/forgejo/raw/README.md?ref=forgejo": (500, b""),
        })
        with pytest.raises(repos.Unavailable):
            repos.fetch(repos.parse("https://codeberg.org/forgejo/forgejo"), time.monotonic() + 5)


@pytest.mark.parametrize("names,expected", [
    (["README.txt", "README.md", "readme"], "README.md"),
    (["README", "README.rst"], "README"),
    (["readme.markdown", "main.go"], "readme.markdown"),
    (["READMEFIRST", "notes.md"], None),
])
def test_pick_readme(names, expected):
    assert repos._pick_readme([(n, "file") for n in names]) == expected


def test_heading_ids_like_githubs():
    out = repos.render_readme("# Hello, World! (v2)\n\n## Hello, World! (v2)\n\n# `code` & more\n",
                              "README.md")
    assert '<h1 id="user-content-hello-world-v2">' in out
    assert '<h2 id="user-content-hello-world-v2-1">' in out
    assert '<h1 id="user-content-code--more">' in out
    raw = repos.render_readme('<h2><span title="a<b">Hello</span></h2>\n', "README.md")
    assert '<h2 id="user-content-hello">' in raw


def test_heading_ids_never_repeat():
    out = repos.render_readme("# Foo\n\n# Foo-1\n\n# Foo\n\n# Foo\n", "README.md")
    assert [h["id"] for h in BeautifulSoup(out, "html.parser").find_all("h1")] == [
        "user-content-foo", "user-content-foo-1", "user-content-foo-2", "user-content-foo-3"]


def test_raw_html_ids_match_the_rewritten_links():
    html = repos.render_readme('<h2 id="install">Install</h2>\n\n[go](#install) '
                               '<a name="top"></a>\n', "README.md")
    out = repos.clean_readme(html, "https://x.example/b/", "https://x.example/r/")
    assert 'id="user-content-install"' in out and 'href="#user-content-install"' in out
    assert 'name="user-content-top"' in out


@pytest.mark.parametrize("heading", [
    "<h1>" * (repos.MAX_README // 4),  # unclosed: none may rescan the rest
    "<h2>x</h2>" * (repos.MAX_README // 10),  # all the same slug
    "<h2>" + "<" * repos.MAX_README + "</h2>",  # no tag ever closes inside
], ids=["unclosed", "repeated", "unmatched-lt"])
def test_heading_ids_stay_linear(heading):
    # A second or less; the quadratic versions took minutes at this size.
    start = time.monotonic()
    repos._heading_ids(heading)
    assert time.monotonic() - start < 5


class TestRateLimits:
    NOW = 1_800_000_000.0

    @pytest.fixture(autouse=True)
    def clock(self, monkeypatch):
        monkeypatch.setattr(repos.time, "time", lambda: self.NOW)

    @pytest.mark.parametrize("status, headers, until", [
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1900000000"}, 1900000000.0),
        (429, {"ratelimit-remaining": "0", "ratelimit-reset": "1900000000"}, 1900000000.0),
        (429, {"RateLimit": '"baseline";r=0;t=600'}, NOW + 600),
        (403, {"RateLimit": '"baseline";r=0;t=60'}, NOW + 60),
        (429, {"Retry-After": "120"}, NOW + 120),
        (429, {}, 0.0),
    ])
    def test_used_up(self, status, headers, until):
        assert repos._rate_limit(status, headers) == until

    def test_retry_after_a_date(self):
        found = repos._rate_limit(429, {"Retry-After": "Sat, 16 Jan 2027 08:00:00 GMT"})
        assert found == 1800086400.0

    @pytest.mark.parametrize("status, headers", [
        (403, {}),
        (403, {"RateLimit": '"baseline";r=12;t=600'}),
        (200, {"X-RateLimit-Remaining": "0"}),
        (404, {"Retry-After": "60"}),
    ])
    def test_not_used_up(self, status, headers):
        assert repos._rate_limit(status, headers) is None


def _call_site(api, path="/projects/g%2Fp"):
    return repos._api(path, time.monotonic() + 10, 1000, "application/json", api)


class TestOtherSitesApi:
    @pytest.mark.ckan_config("ckanext.webpageviewplus.github_token", "github_pat_x")
    @pytest.mark.parametrize("api", [GITLAB.api, CODEBERG.api, BITBUCKET.api,
                                     "https://ghe.example.org/api/v3"])
    def test_github_token_never_sent(self, http, api):
        answers, made = http
        answers[api + "/projects/g%2Fp"] = _Response(200, b"{}")
        assert _call_site(api)[0] == 200
        assert "Authorization" not in made[0][1]

    @pytest.mark.parametrize("location", [
        "https://evil.example/x",
        "https://git.example.org/o/r",
        "https://git.example.org/api/v1x/repos",
        "http://git.example.org/api/v1/repos/o/r",
    ])
    def test_self_hosted_redirect_off_its_api_refused(self, http, location):
        answers, made = http
        api = "https://git.example.org/api/v1"
        answers[api + "/repos/o/r"] = _Response(302, headers={"Location": location})
        with pytest.raises(repos.Unavailable) as raised:
            _call_site(api, "/repos/o/r")
        assert raised.value.kind == "failed" and len(made) == 1

    def test_redirect_within_its_api_followed(self, http):
        answers, made = http
        api = "https://git.example.org/api/v1"
        answers[api + "/repos/o/r"] = _Response(301, headers={"Location": "/api/v1/repos/o/new"})
        answers[api + "/repos/o/new"] = _Response(200, b"{}")
        assert _call_site(api, "/repos/o/r") == (200, b"{}")

    def test_private_address_never_requested(self, http, monkeypatch):
        _, made = http
        monkeypatch.setattr(frameability, "_address_kind", lambda url, deadline: "private")
        with pytest.raises(repos.Unavailable):
            _call_site("https://git.example.org/api/v4")
        assert made == []

    def test_rate_limited(self, http):
        answers, _ = http
        answers[GITLAB.api + "/projects/g%2Fp"] = _Response(
            429, headers={"RateLimit-Reset": "1900000000"})
        with pytest.raises(repos.Unavailable) as raised:
            _call_site(GITLAB.api)
        assert (raised.value.kind, raised.value.until) == ("rate_limited", 1900000000.0)


GITLAB_REPO = "https://gitlab.com/g/p"


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus repo_view_plus")
@pytest.mark.usefixtures("non_clean_db", "with_plugins", "storage")
class TestOtherSitesView:
    def test_offered_for_each_sites_links(self):
        plugin = plugins.get_plugin(repos.VIEW_TYPE)
        for url in ("https://codeberg.org/o/r", GITLAB_REPO, "https://bitbucket.org/w/r"):
            assert plugin.can_view({"resource": {"url": url}})

    def test_bitbucket_page_has_no_stars_or_forks(self, app, api):
        answers, _ = api
        _site_answers(answers, BITBUCKET.api, {
            "/repositories/w/r": {"full_name": "w/r", "is_private": False,
                                  "mainbranch": {"name": "main"}},
            "/repositories/w/r/src/main/?pagelen=100": {"values": []},
        })
        view = _view(factories.Resource(url="https://bitbucket.org/w/r"))
        page = BeautifulSoup(app.get(_page(view)).body, "html.parser")
        facts = page.select_one(".repo-facts").get_text()
        assert "star" not in facts and "fork" not in facts and "Branch: main" in facts

    def test_cards_name_the_site(self, app, api):
        answers, _ = api
        answers[GITLAB.api + "/projects/g%2Fp"] = repos.Unavailable("rate_limited")
        view = _view(factories.Resource(url=GITLAB_REPO))
        card = BeautifulSoup(app.get(_page(view)).body, "html.parser").select_one(".repo-card")
        assert "requests to GitLab" in card.get_text()
        assert card.select_one("a").get_text() == "Open on GitLab"
        assert card.select_one("a")["href"] == GITLAB_REPO

    @pytest.mark.ckan_config("ckanext.webpageviewplus.privacy_profile", "eu")
    def test_consent_card_names_the_repositorys_host(self, app):
        dataset = factories.Dataset()
        resource = factories.Resource(package_id=dataset["id"], url=GITLAB_REPO)
        view = _view(resource)
        text = BeautifulSoup(app.get(_read(dataset, resource, view)).body,
                             "html.parser").select_one(".webpageviewplus").get_text()
        assert "gitlab.com" in text and "github.com" not in text
