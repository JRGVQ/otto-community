"""Pipeline rules: only-new-mail baseline, already-answered guard, dedup, immediate draft.

Covers the rules added for: don't re-import the whole mailbox (R1), don't draft a reply
you already sent in Outlook (R2), no duplicate drafts (R4), don't re-process a duplicate
delivery (R5), and surface a failed draft write-back (R6).
"""
from app.extensions import db
from app.models import Draft, MailAccount, Message, Organization
from app.tasks.draft import generate_draft
from app.tasks.writeback import write_draft


def _org_acct():
    org = db.session.query(Organization).filter_by(is_demo=True).first()
    acct = db.session.query(MailAccount).first()
    return org, acct


def _msg(org, acct, *, pmid, thread="CONV-1", cat_id=None):
    m = Message(organization_id=org.id, account_id=acct.id, user_id=acct.user_id,
                provider_message_id=pmid, provider_thread_id=thread,
                sender="Ann", sender_email="ann@klant.be", subject="Vraag", body="Kan dit?",
                category_id=cat_id)
    db.session.add(m)
    db.session.flush()
    return m


# ── R1: connect sets a baseline, imports no backlog ──────────────────────────
def test_initial_import_sets_baseline_only(app, monkeypatch):
    from app.tasks import sync as sync_mod

    user = db.session.query(MailAccount).first().user
    acct = MailAccount(user_id=user.id, organization_id=user.organization_id,
                       provider="outlook", email="new@acme.be", status="connected")
    db.session.add(acct)
    db.session.commit()
    before = db.session.query(Message).count()

    class _P:
        def is_configured(self):
            return True

        def delta_baseline(self):
            return "DELTA-CURSOR-XYZ"

    monkeypatch.setattr(sync_mod, "get_provider", lambda a: _P())

    assert sync_mod.initial_import(acct.id) == 0          # nothing imported
    db.session.refresh(acct)
    assert acct.sync_cursor == "DELTA-CURSOR-XYZ"          # baseline captured for new-only fetch
    assert db.session.query(Message).count() == before     # no backlog rows created


# ── R2: skip drafting when the owner already replied in Outlook ──────────────
def test_generate_draft_skips_when_owner_replied_in_provider(app, monkeypatch):
    org, acct = _org_acct()
    cat = next(c for c in org.categories if c.draft_enabled)
    m = _msg(org, acct, pmid="r-1", thread="CONV-REPLIED", cat_id=cat.id)
    db.session.commit()

    class _P:
        def is_configured(self):
            return True

        def has_sent_reply_after(self, conversation_id, after_dt=None):
            return True  # the owner already answered this conversation in Outlook

    monkeypatch.setattr("app.providers.get_provider", lambda a: _P())

    assert generate_draft(m.id) is None                    # no concept for an answered thread
    assert generate_draft(m.id, force=True) is not None    # explicit/forced still works


# ── R4: a regenerated draft REPLACES the old Outlook draft (no duplicates) ────
def test_write_draft_replaces_previous_provider_draft(app, monkeypatch):
    org, acct = _org_acct()
    m = _msg(org, acct, pmid="w-1")
    draft = Draft(message_id=m.id, current_text="hallo", provider_draft_id="OLD-DRAFT")
    db.session.add(draft)
    db.session.commit()

    calls = []

    class _P:
        def is_configured(self):
            return True

        def delete_draft(self, did):
            calls.append(("delete", did))

        def create_draft(self, **kw):
            calls.append(("create", kw.get("in_reply_to")))
            return "NEW-DRAFT"

    monkeypatch.setattr("app.tasks.writeback.get_provider", lambda a: _P())

    write_draft(draft)
    assert ("delete", "OLD-DRAFT") in calls                # old one removed first
    assert draft.provider_draft_id == "NEW-DRAFT" and draft.placed is True


# ── R5: a duplicate delivery doesn't re-run the AI ───────────────────────────
def test_process_incoming_is_idempotent(app, monkeypatch):
    from app.tasks import pipeline

    org, acct = _org_acct()
    cat = next(c for c in org.categories if c.draft_enabled)
    m = _msg(org, acct, pmid="dup-1", cat_id=cat.id)
    db.session.add(Draft(message_id=m.id, status="concept"))
    db.session.commit()

    called = []
    monkeypatch.setattr("app.tasks.pipeline.categorize_message", lambda mid: called.append("cat"))
    monkeypatch.setattr("app.tasks.pipeline.generate_draft", lambda mid: called.append("draft"))

    pipeline.process_incoming(m.id)
    assert called == []                                    # already categorised + drafted → no-op


# ── R6: a failed write-back surfaces (draft is marked NOT placed) ─────────────
def test_write_draft_marks_not_placed_on_failure(app, monkeypatch):
    org, acct = _org_acct()
    m = _msg(org, acct, pmid="f-1")
    draft = Draft(message_id=m.id, current_text="hallo", placed=True)
    db.session.add(draft)
    db.session.commit()

    class _P:
        def is_configured(self):
            return True

        def create_draft(self, **kw):
            raise RuntimeError("graph unreachable")

    monkeypatch.setattr("app.tasks.writeback.get_provider", lambda a: _P())

    write_draft(draft)
    assert draft.placed is False                           # not in Outlook → reflected in state
