"""Regels tonen mapnamen, nooit Graph-id's.

Twee dingen liepen samen mis. `list_folders` gaf alleen het bovenste niveau terug, dus
een regel naar een submap kon zijn map niet terugvinden. En als dat opzoeken misging,
bewaarde `from_graph` het ruwe Graph-id alsof het de naam was — waarna dat hele ding
("AAMkADBiZmM1…") op het regelscherm verscheen.
"""
from __future__ import annotations

from app import mailrules
from app.models import MailRule

FOLDER_ID = "AAMkADBiZmM1ZWUyLTI1MGQtNDhhMi05MmFlLTU0OWZlZGUxMjVmYQAuAAAAAAAJ_HhqM9H6Q7gbH2toP6jLAQA"


def _rule(actions):
    return MailRule(name="Test", conditions={}, actions=actions)


# ── Inlezen uit Outlook ──────────────────────────────────────────────────────
def test_a_known_folder_is_stored_by_name():
    _c, actions = mailrules.from_graph(
        {"actions": {"moveToFolder": FOLDER_ID}}, folder_names={FOLDER_ID: "Facturen"},
    )
    assert actions["move_to_folder"] == "Facturen"
    assert "move_to_folder_id" not in actions


def test_an_unknown_folder_is_never_stored_as_if_it_were_a_name():
    """Dit was de bug: het id belandde onder de naam-sleutel."""
    _c, actions = mailrules.from_graph({"actions": {"moveToFolder": FOLDER_ID}}, folder_names={})

    assert "move_to_folder" not in actions
    assert actions["move_to_folder_id"] == FOLDER_ID


def test_the_same_holds_for_copying():
    _c, actions = mailrules.from_graph({"actions": {"copyToFolder": FOLDER_ID}}, folder_names={})
    assert "copy_to_folder" not in actions and actions["copy_to_folder_id"] == FOLDER_ID


# ── Wat de gebruiker leest ───────────────────────────────────────────────────
def test_the_screen_never_shows_a_graph_id():
    _conds, acts = mailrules.describe(_rule({"move_to_folder_id": FOLDER_ID}))

    tekst = " ".join(acts)
    assert FOLDER_ID not in tekst
    assert "een map in Outlook" in tekst


def test_a_known_folder_is_shown_by_its_name():
    _conds, acts = mailrules.describe(_rule({"move_to_folder": "Facturen"}))
    assert "Verplaats naar map: Facturen" in acts


# ── Terugschrijven naar Outlook ──────────────────────────────────────────────
def test_a_rule_to_an_unknown_folder_keeps_working():
    """De naam kennen we niet, maar het id wel — de regel mag niet stilvallen."""
    payload = mailrules.to_graph(_rule({"move_to_folder_id": FOLDER_ID}), folder_ids={})
    assert payload["actions"]["moveToFolder"] == FOLDER_ID


def test_a_known_name_wins_over_a_remembered_id():
    """Is de map intussen wél op naam te vinden, dan gebruiken we die — dan volgt de
    regel de map ook als Outlook het id ooit anders teruggeeft."""
    rule = _rule({"move_to_folder": "Facturen", "move_to_folder_id": "OUD-ID"})
    payload = mailrules.to_graph(rule, folder_ids={"Facturen": "NIEUW-ID"})
    assert payload["actions"]["moveToFolder"] == "NIEUW-ID"


# ── Submappen ────────────────────────────────────────────────────────────────
class _FakeGraph:
    """Een postvak met één submap onder één hoofdmap."""

    def __init__(self):
        self.calls = []

    def _get(self, url):
        self.calls.append(url)
        if "/childFolders" in url:
            return {"value": [{"id": "sub-1", "displayName": "Facturen", "childFolderCount": 0}]}
        return {"value": [{"id": "top-1", "displayName": "Klanten", "childFolderCount": 1}]}


def test_subfolders_are_listed_too(app):
    """Zonder dit vond een regel naar een submap zijn map nooit terug."""
    from app.providers.outlook import OutlookProvider

    provider = OutlookProvider.__new__(OutlookProvider)
    fake = _FakeGraph()
    provider._get = fake._get
    provider.account = None

    folders = provider.list_folders()
    namen = {f["name"]: f["id"] for f in folders}

    assert namen == {"Klanten": "top-1", "Klanten / Facturen": "sub-1"}


def test_a_deep_tree_cannot_loop_forever(app):
    """Elke map meldt kinderen; de dieptegrens moet dat afkappen."""
    from app.providers.outlook import OutlookProvider

    class _Endless:
        def _get(self, url):
            return {"value": [{"id": f"f{len(url)}", "displayName": "Map",
                               "childFolderCount": 1}]}

    provider = OutlookProvider.__new__(OutlookProvider)
    provider._get = _Endless()._get
    provider.account = None

    assert len(provider.list_folders()) == OutlookProvider._FOLDER_DEPTH
