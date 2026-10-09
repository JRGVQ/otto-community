"""One draft in the preferred tone (not four), on-demand tone switch, default reply, learning."""
from app.constants import DRAFT_SENT
from app.extensions import db
from app.models import Category, Draft, Message
from app.seed.demo_data import DEMO_PASSWORD


def _login(client):
    client.post("/auth/login", data={"email": "demo@example.com", "password": DEMO_PASSWORD})


def _draftable_message(org_id):
    return (
        db.session.query(Message)
        .join(Category, Message.category_id == Category.id)
        .filter(Message.organization_id == org_id, Category.draft_enabled.is_(True))
        .first()
    )


# ── One draft, preferred tone ────────────────────────────────────────────────
def test_generate_draft_makes_one_variant_in_preferred_tone(app, demo_user):
    from app.tasks.draft import generate_draft

    demo_user.style_profile.tone = "kort"
    db.session.commit()
    msg = _draftable_message(demo_user.organization_id)
    did = generate_draft(msg.id, force=True)
    draft = db.session.get(Draft, did)
    assert len(draft.tone_variants) == 1          # not four
    assert draft.current_tone == "kort"           # the user's preferred tone


def test_set_tone_generates_alternative_on_demand(client, app, demo_user):
    from app.tasks.draft import generate_draft

    _login(client)
    msg = _draftable_message(demo_user.organization_id)
    generate_draft(msg.id, force=True)
    draft = msg.draft
    other = "formeel" if draft.current_tone != "formeel" else "kort"
    assert draft.variant(other) is None  # only the preferred tone exists up front

    r = client.post(f"/api/drafts/{msg.id}/tone", json={"tone": other})
    assert r.status_code == 200
    db.session.refresh(draft)
    assert draft.current_tone == other
    assert draft.variant(other) is not None  # generated on demand


# ── Default reply ("standaard antwoord") ─────────────────────────────────────
def test_default_reply_flows_into_prompt(app, demo_user):
    from app.ai.style import profile_to_text

    sp = demo_user.style_profile
    sp.default_reply = "altijd een korte call voorstellen"
    assert "altijd een korte call voorstellen" in profile_to_text(sp)


def test_save_reply_prefs_route(client, app, demo_user):
    _login(client)
    r = client.post("/app/settings/voorkeuren",
                    data={"tone": "formeel", "default_reply": "kort en bondig"}, follow_redirects=False)
    assert r.status_code == 302
    db.session.refresh(demo_user.style_profile)
    assert demo_user.style_profile.tone == "formeel"
    assert demo_user.style_profile.default_reply == "kort en bondig"


# ── Learning from communication ──────────────────────────────────────────────
def test_learn_user_style_from_sent_drafts(app, demo_user):
    from app.tasks.learn import learn_user_style

    msg = _draftable_message(demo_user.organization_id)
    draft = msg.draft or Draft(message_id=msg.id, lang=msg.lang)
    draft.status = DRAFT_SENT
    draft.current_text = "Dag Marlies,\n\nDat lukt zeker, ik plan graag een korte call deze week in.\n\nGroeten"
    db.session.add(draft)
    db.session.commit()

    sp = learn_user_style(demo_user.id)
    assert sp is not None and sp.learned_at is not None
    assert (sp.raw_profile or {}).get("examples")  # captured a representative phrasing
