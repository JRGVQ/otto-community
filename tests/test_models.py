"""Model + seed integrity."""
from app.extensions import db
from app.models import Category, Message, Permission, Role, User


def test_roles_and_permissions(app):
    assert db.session.query(Role).count() == 5
    assert db.session.query(Permission).count() == 11


def test_matrix_owner_all_and_readonly_locked(app):
    owner = db.session.query(Role).filter_by(name="Eigenaar").first()
    readonly = db.session.query(Role).filter_by(name="Alleen-lezen").first()
    assert owner.allows("viewAudit") and owner.allows("manageUsers")
    assert not readonly.allows("editDrafts") and not readonly.allows("sendMail")
    assert readonly.allows("viewInbox")


def test_seed_counts(app):
    from app.seed.demo_data import CATEGORIES

    assert db.session.query(Category).count() == len(CATEGORIES)
    assert db.session.query(Message).count() == 10
    assert db.session.query(User).count() == 6


def test_user_perms_and_password(app):
    sam = db.session.query(User).filter_by(email="demo@example.com").first()
    assert sam.role_name == "Beheerder"
    assert sam.has_perm("manageUsers")
    assert sam.has_perm("viewAudit")
    assert sam.check_password("otto-demo")
    assert not sam.check_password("wrong")


def test_cascade_delete_account_removes_messages(app):
    sam = db.session.query(User).filter_by(email="demo@example.com").first()
    account = sam.mail_accounts[0]
    before = db.session.query(Message).filter_by(account_id=account.id).count()
    assert before > 0
    db.session.delete(account)
    db.session.commit()
    assert db.session.query(Message).filter_by(account_id=account.id).count() == 0
