"""A pasted e-mail address keeps its stray whitespace — Otto must clean it, not reject it.

The signup form validated the raw field, so "jij@bedrijf.be " (one trailing space, the
usual leftover of a copy-paste or a mobile keyboard) was reported as an invalid address
with nothing on screen to show the difference. Every entry point normalises now.
"""
from app.domains import domain_of, normalize_email
from app.email import make_verify_token
from app.extensions import db
from app.models import User


# ── The normaliser itself ────────────────────────────────────────────────────
def test_normalize_strips_the_whitespace_people_actually_paste():
    for raw in ("sam@acme-corp.be ", " sam@acme-corp.be", "\tsam@acme-corp.be\n",
                "sam@acme-corp.be ", "sam @ acme-corp.be", "​sam@acme-corp.be"):
        assert normalize_email(raw) == "sam@acme-corp.be", raw


def test_normalize_unwraps_mail_client_copies_and_lowercases():
    assert normalize_email("<Sam@Acme-Corp.BE>") == "sam@acme-corp.be"
    assert normalize_email("mailto:sam@acme-corp.be") == "sam@acme-corp.be"
    assert normalize_email("MAILTO:<sam@acme-corp.be> ") == "sam@acme-corp.be"


def test_normalize_survives_nothing_at_all():
    assert normalize_email(None) == "" and normalize_email("") == "" and normalize_email("   ") == ""


def test_domain_of_uses_the_cleaned_address():
    assert domain_of("sam@Acme-Corp.be ") == "acme-corp.be"
    assert domain_of("geen-adres") == ""


# ── Signup ───────────────────────────────────────────────────────────────────
def test_register_accepts_an_address_with_a_trailing_space(client, app):
    """The exact failure that was reported: a correct address, one trailing space."""
    r = client.post("/auth/register", data={
        "company": "Acme Corp", "name": "Sam", "email": "sam@acme-corp.be ",
        "password": "geheim123", "confirm": "geheim123",
    })
    body = r.get_data(as_text=True)
    assert "Check je mailbox" in body
    assert "Invalid email address" not in body and "ongeldig" not in body.lower()
    # Stored clean, so the verification link and every later lookup match.
    user = db.session.query(User).filter_by(email="sam@acme-corp.be").first()
    assert user is not None and user.organization.name == "Acme Corp"


def test_register_name_and_company_are_trimmed(client, app):
    client.post("/auth/register", data={
        "company": "  Ruimte BV  ", "name": "  Jan Jansen  ", "email": "jan@ruimtebv.be",
        "password": "geheim123", "confirm": "geheim123",
    })
    user = db.session.query(User).filter_by(email="jan@ruimtebv.be").first()
    assert user.name == "Jan Jansen" and user.organization.name == "Ruimte BV"


def test_verified_user_can_log_in_with_a_trailing_space(client, app):
    client.post("/auth/register", data={
        "company": "Spatie BV", "name": "Sam", "email": "sam@spatiebv.be",
        "password": "geheim123", "confirm": "geheim123",
    })
    client.get(f"/auth/verify/{make_verify_token('sam@spatiebv.be')}")
    client.get("/auth/logout")

    r = client.post("/auth/login", data={"email": " sam@spatiebv.be ", "password": "geheim123"},
                    follow_redirects=False)
    assert r.status_code == 302 and "/auth/login" not in r.headers["Location"]


def test_forgot_password_accepts_a_padded_address(client, app):
    client.post("/auth/register", data={
        "company": "Reset BV", "name": "Rik", "email": "rik@resetbv.be",
        "password": "geheim123", "confirm": "geheim123",
    })
    r = client.post("/auth/forgot", data={"email": "rik@resetbv.be "})
    assert "Invalid email address" not in r.get_data(as_text=True)


# ── The other entry points ───────────────────────────────────────────────────
def test_invite_accepts_a_padded_address(auth_client, app):
    r = auth_client.post("/api/admin/users/invite",
                         json={"email": " collega@example.com ", "role": "Gebruiker"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert db.session.query(User).filter_by(email="collega@example.com").first() is not None


def test_imap_connect_stores_a_clean_address(auth_client, app):
    from app.models import MailAccount

    r = auth_client.post("/api/accounts/imap", json={
        "email": " postvak@bedrijf.be ", "host": " mail.bedrijf.be ", "password": "x",
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    account = db.session.query(MailAccount).filter_by(provider="imap").order_by(MailAccount.id.desc()).first()
    assert account.email == "postvak@bedrijf.be"
    assert account.provider_config["host"] == "mail.bedrijf.be"
    assert account.provider_config["username"] == "postvak@bedrijf.be"
