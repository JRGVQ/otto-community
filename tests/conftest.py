"""Pytest fixtures: a seeded testing app + client + login helper."""
from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db
from app.seed import seed_all
from app.seed.demo_data import DEMO_PASSWORD


@pytest.fixture()
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        seed_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})
    return client


@pytest.fixture()
def demo_user(app):
    from app.models import User

    return db.session.query(User).filter_by(email="demo@example.com").first()
