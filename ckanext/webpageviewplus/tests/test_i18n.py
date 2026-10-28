"""The shipped translations: complete, placeholders intact, .mo files in
step with their .po files, and actually loaded by CKAN."""
import gettext
import os
import re

import pytest
from babel.messages.pofile import read_po

from ckan.plugins.toolkit import url_for
from ckan.tests import factories

I18N = os.path.join(os.path.dirname(os.path.dirname(__file__)), "i18n")
DOMAIN = "ckanext-webpageviewplus"
LOCALES = sorted(
    name for name in os.listdir(I18N) if os.path.isdir(os.path.join(I18N, name)))
PLACEHOLDER = re.compile(r"\{\w+\}|%\(\w+\)[sd]")


def _catalog(path):
    with open(path, "rb") as f:
        return read_po(f)


def _forms(value):
    return list(value) if isinstance(value, (tuple, list)) else [value]


def test_six_locales_ship():
    assert {"es", "fr", "pt_BR", "de", "it", "ja"} <= set(LOCALES)


def test_plugin_domain_matches_catalogues():
    from ckanext.webpageviewplus.plugin import WebpageviewplusPlugin

    assert WebpageviewplusPlugin().i18n_domain() == DOMAIN


@pytest.mark.parametrize("locale", LOCALES)
def test_catalogue_complete_and_safe(locale):
    template = {m.id for m in _catalog(os.path.join(I18N, DOMAIN + ".pot")) if m.id}
    catalog = _catalog(os.path.join(I18N, locale, "LC_MESSAGES", DOMAIN + ".po"))
    ids = {m.id for m in catalog if m.id}
    assert ids == template, "out of date: run extract_messages and update_catalog"
    for message in catalog:
        if not message.id:
            continue
        assert not message.fuzzy, message.id
        source = sorted(set(PLACEHOLDER.findall(" ".join(_forms(message.id)))))
        for text in _forms(message.string):
            assert text, "untranslated: {!r}".format(message.id)
            # Every placeholder kept; a plural form may drop the count.
            assert sorted(set(PLACEHOLDER.findall(text))) == source, (message.id, text)
            # CKAN's _() %-formats, so a stray % breaks rendering.
            assert text.count("%") == " ".join(_forms(message.id)).count("%"), text


@pytest.mark.parametrize("locale", LOCALES)
def test_mo_matches_po(locale):
    directory = os.path.join(I18N, locale, "LC_MESSAGES")
    catalog = _catalog(os.path.join(directory, DOMAIN + ".po"))
    with open(os.path.join(directory, DOMAIN + ".mo"), "rb") as f:
        compiled = gettext.GNUTranslations(f)
    for message in catalog:
        if not message.id:
            continue
        if isinstance(message.id, tuple):
            for n, text in zip((1, 2), message.string):
                assert compiled.ngettext(*message.id, n) == text, "recompile the .mo"
        else:
            assert compiled.gettext(message.id) == message.string, "recompile the .mo"


@pytest.mark.ckan_config("ckan.plugins", "webpage_view_plus")
@pytest.mark.ckan_config("ckan.views.default_views", "")
@pytest.mark.usefixtures("non_clean_db", "with_plugins")
def test_pages_are_translated(app):
    from ckan.tests import helpers

    dataset = factories.Dataset()
    resource = factories.Resource(package_id=dataset["id"], url="https://pages.example.com/a")
    helpers.call_action(
        "resource_view_create",
        context={"user": factories.Sysadmin()["name"], "ignore_auth": True},
        resource_id=resource["id"], view_type="webpage_view_plus", title="Report")
    path = url_for("{}_resource.read".format(dataset["type"]), id=dataset["name"],
                   resource_id=resource["id"])
    assert "Open in new tab" in app.get(path).body
    assert "Abrir en una pestaña nueva" in app.get("/es" + path).body
    assert "Ouvrir dans un nouvel onglet" in app.get("/fr" + path).body
    assert "Abrir em nova aba" in app.get("/pt_BR" + path).body
    assert "In neuem Tab öffnen" in app.get("/de" + path).body
    assert "Apri in una nuova scheda" in app.get("/it" + path).body
    assert "新しいタブで開く" in app.get("/ja" + path).body
