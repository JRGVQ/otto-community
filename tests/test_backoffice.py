"""The raw cross-tenant data console (/backoffice) has been REMOVED.

It used to let the platform owner browse/edit every tenant's data (incl. mailbox
content). It's gone so that no one — owner included — can read another company's
data. Operational management lives in the curated /owner console (companies,
licenses, billing, support, health), which exposes no tenant mailbox content.
"""
from __future__ import annotations

from app.seed.demo_data import DEMO_PASSWORD


def test_backoffice_data_console_is_removed(client, app):
    # No /backoffice routes exist anymore — 404 for anonymous and platform owner alike.
    assert client.get("/backoffice/").status_code == 404
    assert client.get("/backoffice/message/").status_code == 404
    assert client.get("/backoffice/mailaccount/").status_code == 404

    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    assert client.get("/backoffice/").status_code == 404
    assert client.get("/backoffice/organization/").status_code == 404
