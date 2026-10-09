"""HTML e-mail body → readable text."""
from app.htmltext import html_to_text

OUTLOOK = (
    '<html><head><meta http-equiv="Content-Type" content="text/html">'
    "<style><!--\n@font-face {font-family:Wingdings}\n"
    "p.MsoNormal {margin:0cm; font-size:12.0pt}\n--></style></head>"
    "<body><p class=MsoNormal>Hallo Charlotte,</p>"
    "<p class=MsoNormal>Bedankt voor uw bericht.</p>"
    "<p>Groeten,<br>Sam</p></body></html>"
)


def test_strips_html_head_and_style_noise():
    out = html_to_text(OUTLOOK)
    assert "font-face" not in out and "MsoNormal" not in out and "<" not in out
    assert "Hallo Charlotte," in out
    assert "Bedankt voor uw bericht." in out
    assert "Groeten,\nSam" in out  # <br> becomes a line break


def test_plain_text_is_left_intact():
    plain = "Hallo,\n\nDit is gewoon tekst.\nMet regels."
    assert html_to_text(plain) == plain


def test_decodes_entities():
    assert html_to_text("<p>R&amp;D &lt;3 caf&eacute;</p>") == "R&D <3 café"


def test_empty_and_none():
    assert html_to_text("") == ""
    assert html_to_text(None) == ""


def test_reading_pane_renders_clean_body(auth_client, app):
    """End-to-end: a stored HTML body shows as text in the reading pane, not markup."""
    from app.extensions import db
    from app.models import Message

    msg = db.session.query(Message).first()
    msg.body = OUTLOOK
    db.session.commit()
    body = auth_client.get(f"/app/inbox?m={msg.id}").get_data(as_text=True)
    assert "@font-face" not in body and "MsoNormal" not in body
    assert "Bedankt voor uw bericht." in body
