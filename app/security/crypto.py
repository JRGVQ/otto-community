"""Symmetric encryption for OAuth tokens at rest (GDPR: tokens never stored plaintext)."""
from __future__ import annotations

from flask import current_app
from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = current_app.config["TOKEN_ENCRYPTION_KEY"]
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def encrypt(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    return _fernet().decrypt(token.encode()).decode()
