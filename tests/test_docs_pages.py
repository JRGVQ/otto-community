"""The expanded documentation site renders every page + the new nav groups."""
import pytest

NEW_PAGES = ["inbox", "categorieen", "concepten", "mappen", "taken", "analyse",
             "instellingen"]


@pytest.mark.parametrize("key", NEW_PAGES)
def test_doc_page_renders(client, key):
    assert client.get(f"/docs/{key}").status_code == 200


def test_docs_nav_groups_present(client):
    home = client.get("/docs/").get_data(as_text=True)
    assert "Dagelijks gebruik" in home and "Beheer" in home
    assert "Automatisch mappen" in home and "Analyse" in home
