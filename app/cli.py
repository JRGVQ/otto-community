"""Custom Flask CLI commands."""
from __future__ import annotations

import click
from flask import Flask


def register_cli(app: Flask) -> None:
    @app.cli.command("seed")
    @click.option("--reset", is_flag=True, help="Drop all tables first, then reseed.")
    def seed_command(reset: bool) -> None:
        """Load the Otto demo dataset (fictional org Acme NV)."""
        from app.seed import seed_all

        result = seed_all(reset=reset)
        if result["status"] == "exists":
            click.echo("✓ Demo data already present (use --reset to rebuild).")
        else:
            click.echo("✓ Seeded demo organisation 'Acme NV'.")
        click.echo("")
        click.echo("  Demo login:")
        click.echo(f"    e-mail:   {result.get('email', 'demo@example.com')}")
        click.echo(f"    password: {result.get('password') or '(random — use flask set-password)'}")

    @app.cli.command("check-migration-heads")
    def check_migration_heads() -> None:
        """Fail if the migration graph has more than one head.

        Two heads (two branches each chaining a migration onto the same parent, then
        both merged) make `flask db upgrade` abort — which silently fails every
        deploy. This guard runs in CI (and locally) with NO database needed, so a split
        is caught before it can break a deploy.
        """
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config()
        cfg.set_main_option("script_location", "migrations")
        heads = ScriptDirectory.from_config(cfg).get_heads()
        if len(heads) == 1:
            click.echo(f"✓ Single migration head: {heads[0]}")
            return
        click.echo(f"✗ Expected exactly one migration head, found {len(heads)}: {', '.join(heads) or '(none)'}", err=True)
        raise SystemExit(1)

    @app.cli.command("db-doctor")
    @click.option("--fix", is_flag=True, help="Apply repairs (default is a dry-run report).")
    def db_doctor(fix: bool) -> None:
        """Detect & repair schema drift between the models and the live database.

        When a restore (or a stray `db stamp`) leaves `alembic_version` AHEAD of the real
        schema, `flask db upgrade` becomes a no-op and columns the code expects are never
        created — every request that touches them 500s with "column ... does not exist".

        This walks the model metadata and reports (or, with --fix, repairs) every MISSING
        table and column: tables via create_all (idempotent), columns via ADD COLUMN
        (added nullable so existing rows are safe, keeping any server_default). After a fix
        it stamps `alembic_version` to head so future upgrades line up. Safe & additive —
        it never drops or alters existing columns.
        """
        from sqlalchemy import inspect as sa_inspect, text

        from app.extensions import db

        insp = sa_inspect(db.engine)
        dialect = db.engine.dialect
        try:
            current = db.session.execute(text("select version_num from alembic_version")).scalar()
        except Exception:  # noqa: BLE001 - table may be missing on a bare DB
            current = None
        click.echo(f"alembic_version = {current or '(none)'}")

        existing = set(insp.get_table_names())
        missing_tables = [t.name for t in db.metadata.sorted_tables if t.name not in existing]
        missing_cols = []  # (table, column)
        for table in db.metadata.sorted_tables:
            if table.name in existing:
                have = {c["name"] for c in insp.get_columns(table.name)}
                missing_cols.extend((table, col) for col in table.columns if col.name not in have)

        if not missing_tables and not missing_cols:
            click.echo("✓ Schema matches the models — nothing to repair.")
            return

        for t in missing_tables:
            click.echo(f"  missing table:  {t}")
        for table, col in missing_cols:
            click.echo(f"  missing column: {table.name}.{col.name}")

        if not fix:
            click.echo(f"\n{len(missing_tables)} table(s) + {len(missing_cols)} column(s) missing. "
                       "Re-run with --fix to repair.")
            return

        # 1) Create missing tables — create_all only builds ones that don't exist yet.
        if missing_tables:
            db.metadata.create_all(bind=db.engine)
            click.echo(f"✓ Created {len(missing_tables)} table(s).")

        # 2) Add missing columns — nullable, keeping any server_default so existing rows
        #    backfill. We check existence up front, so no IF NOT EXISTS needed (portable).
        for table, col in missing_cols:
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(dialect=dialect)}'
            if col.server_default is not None:
                try:
                    ddl += f" DEFAULT {col.server_default.arg}"
                except Exception:  # noqa: BLE001 - complex default → add without one
                    pass
            db.session.execute(text(ddl))
        if missing_cols:
            db.session.commit()
            click.echo(f"✓ Added {len(missing_cols)} column(s).")

        # 3) Reconcile alembic to head so `flask db upgrade` lines up again.
        from flask_migrate import stamp

        stamp(revision="head")
        click.echo("✓ Stamped alembic_version to head. Schema repaired.")

    @app.cli.command("dedupe-mailboxes")
    @click.option("--email", default=None, help="Alleen deze gebruiker; standaard iedereen.")
    def dedupe_mailboxes(email: str | None) -> None:
        """Voeg dubbele postvakkoppelingen samen (en hun dubbel geïmporteerde mail).

        Nodig voor omgevingen die al draaiden toen elke koppeling een nieuwe rij maakte.
        Nieuwe koppelingen werken dit vanzelf bij, dus dit hoeft in principe één keer.
        """
        from app.extensions import db
        from app.mailboxes import merge_duplicates
        from app.models import User

        q = db.session.query(User)
        if email:
            q = q.filter_by(email=email.lower().strip())
        totaal_pv = totaal_msg = 0
        for user in q.all():
            postvakken, berichten = merge_duplicates(user)
            if postvakken:
                click.echo(f"  {user.email}: {postvakken} dubbel postvak(ken), "
                           f"{berichten} dubbele berichten opgeruimd")
            totaal_pv += postvakken
            totaal_msg += berichten
        if totaal_pv:
            click.echo(f"✓ {totaal_pv} postvakken samengevoegd, {totaal_msg} berichten opgeruimd.")
        else:
            click.echo("✓ Geen dubbels gevonden.")

    @app.cli.command("grant-owner")
    @click.argument("email")
    def grant_owner(email: str) -> None:
        """Make a user the platform owner (super-admin across all tenants)."""
        from app.extensions import db
        from app.models import User

        user = db.session.query(User).filter_by(email=email.lower().strip()).first()
        if not user:
            click.echo(f"No user with email {email}.")
            return
        user.is_platform_owner = True
        db.session.commit()
        click.echo(f"✓ {email} is now the platform owner — backoffice at /owner.")

    @app.cli.command("revoke-owner")
    @click.argument("email")
    def revoke_owner(email: str) -> None:
        """Remove platform-owner rights from a user."""
        from app.extensions import db
        from app.models import User

        user = db.session.query(User).filter_by(email=email.lower().strip()).first()
        if user:
            user.is_platform_owner = False
            db.session.commit()
            click.echo(f"✓ Revoked platform-owner from {email}.")

    @app.cli.command("verify-user")
    @click.argument("email")
    def verify_user(email: str) -> None:
        """Manually mark a user's e-mail as verified (admin escape hatch, no SMTP needed)."""
        from app.extensions import db
        from app.models import User

        user = db.session.query(User).filter_by(email=email.lower().strip()).first()
        if not user:
            click.echo(f"No user with email {email}.")
            return
        user.email_verified = True
        db.session.commit()
        click.echo(f"✓ {email} is verified — they can log in now.")

    @app.cli.command("set-password")
    @click.argument("email")
    @click.password_option()
    def set_password(email: str, password: str) -> None:
        """Set (reset) a user's password and mark them verified. Login escape hatch."""
        from app.extensions import db
        from app.models import User

        user = db.session.query(User).filter_by(email=email.lower().strip()).first()
        if not user:
            click.echo(f"No user with email {email}. Use create-owner to make one.")
            return
        user.set_password(password)
        user.email_verified = True
        db.session.commit()
        click.echo(f"✓ Password set for {email}; e-mail marked verified — you can log in now.")

    @app.cli.command("create-owner")
    @click.option("--email", prompt="E-mail", help="Login e-mail for the owner.")
    @click.option("--name", prompt="Full name", help="Display name, e.g. 'Sam Verlinden'.")
    @click.option("--company", prompt="Company name", help="The organisation to create.")
    @click.password_option()
    def create_owner(email: str, name: str, company: str, password: str) -> None:
        """Create a real (non-demo) company + its owner with a password you choose.

        If the e-mail already exists, it just sets the password, verifies and grants
        platform-owner — so this one command always gets you into the real app.
        """
        from app.extensions import db
        from app.models import User
        from app.tenancy import add_owner_user, provision_organization

        email = email.lower().strip()
        existing = db.session.query(User).filter_by(email=email).first()
        if existing:
            existing.set_password(password)
            existing.email_verified = True
            existing.is_platform_owner = True
            db.session.commit()
            click.echo(f"✓ {email} already existed — password set, verified, platform owner. Log in now.")
            return

        org = provision_organization(company, domain=email.split("@")[-1], demo=False)
        user = add_owner_user(org, name, email, password, verified=True)
        user.is_platform_owner = True
        db.session.commit()
        click.echo(f"✓ Created '{company}' with owner {email} (verified, platform owner).")
        click.echo(f"  Log in at {app.config.get('PUBLIC_BASE_URL') or '/'} — empty environment, set it up yourself.")

    @app.cli.command("learn-style")
    @click.argument("email")
    def learn_style_cmd(email: str) -> None:
        """Re-learn a user's writing style from their sent drafts and edits."""
        from app.extensions import db
        from app.models import User
        from app.tasks.learn import learn_user_style

        user = db.session.query(User).filter_by(email=email.lower().strip()).first()
        if not user:
            click.echo(f"No user with email {email}.")
            return
        sp = learn_user_style(user.id)
        if sp is None:
            click.echo("Not enough sent mail yet to learn from.")
        else:
            click.echo(f"✓ Learned style for {email} (greeting '{sp.greeting}', "
                       f"{len((sp.raw_profile or {}).get('examples', []))} examples).")

    @app.cli.command("sync-now")
    @click.argument("email")
    @click.option("--initial", is_flag=True, help="Full initial import instead of an incremental sync.")
    def sync_now(email: str, initial: bool) -> None:
        """Import/sync a user's mailbox now and show categorized counts + a sample.

        Diagnostic for verifying the Outlook pipeline against a real mailbox. Never sends.
        """
        from app.extensions import db
        from app.models import MailAccount, Message, User
        from app.tasks.sync import initial_import, sync_account

        user = db.session.query(User).filter_by(email=email.lower().strip()).first()
        if not user:
            click.echo(f"No user with email {email}.")
            return
        account = (
            db.session.query(MailAccount)
            .filter_by(user_id=user.id)
            .order_by(MailAccount.id.desc())
            .first()
        )
        if not account:
            click.echo("No mailbox connected for this user.")
            return

        n = initial_import(account.id) if initial else sync_account(account.id)
        click.echo(f"✓ Processed {n} new message(s) for {email} (provider={account.provider}).")
        click.echo("  Recent messages (category · draft · sender · subject):")
        msgs = (
            db.session.query(Message)
            .filter_by(account_id=account.id)
            .order_by(Message.id.desc())
            .limit(10)
            .all()
        )
        for m in msgs:
            cat = m.category.name if m.category else "—"
            drafted = "draft✓" if (m.draft and m.draft.placed) else ("concept" if m.draft else "—")
            click.echo(f"  [{cat:<16}] {drafted:<8} {(m.sender_email or ''):<28} {(m.subject or '')[:48]}")

    @app.cli.command("erase-user")
    @click.argument("email")
    def erase_user(email: str) -> None:
        """GDPR erasure: delete a user and all their mailboxes/messages/drafts."""
        from app.extensions import db
        from app.models import User

        user = db.session.query(User).filter_by(email=email).first()
        if not user:
            click.echo(f"No user with email {email}.")
            return
        db.session.delete(user)
        db.session.commit()
        click.echo(f"✓ Erased {email} and all associated data.")

    @app.cli.command("export-org")
    @click.argument("slug")
    @click.option("--out", default=None, help="Write JSON to this file instead of stdout.")
    def export_org(slug: str, out: str | None) -> None:
        """GDPR data export: dump an organisation's data as JSON (no secrets)."""
        import json

        from app.extensions import db
        from app.models import Message, Organization, Task

        org = db.session.query(Organization).filter_by(slug=slug).first()
        if not org:
            click.echo(f"No organisation with slug '{slug}'.")
            return
        data = {
            "organization": {"id": org.id, "name": org.name, "slug": org.slug, "domain": org.domain,
                             "created_at": org.created_at},
            "users": [{"email": u.email, "name": u.name, "role": u.role_name, "status": u.status}
                      for u in org.users],
            "categories": [{"key": c.key, "name": c.name} for c in org.categories],
            "messages": [{"from": m.sender_email, "subject": m.subject, "received_at": m.received_at,
                          "category": (m.category.name if m.category else None), "body": m.body}
                         for m in db.session.query(Message).filter_by(organization_id=org.id)],
            "tasks": [{"title": t.title, "done": t.done, "due": t.due_display}
                      for t in db.session.query(Task).filter_by(organization_id=org.id)],
        }
        payload = json.dumps(data, indent=2, default=str, ensure_ascii=False)
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(payload)
            click.echo(f"✓ Exported '{org.name}' ({len(data['users'])} users, "
                       f"{len(data['messages'])} messages) to {out}.")
        else:
            click.echo(payload)

    @app.cli.command("wipe-demo")
    def wipe_demo() -> None:
        """Remove the seeded demo organisation (Acme NV) from this database.

        Use on production once SEED_DEMO=0, so the instance has no demo data. Real
        companies are untouched.
        """
        from app.extensions import db
        from app.models import Organization
        from app.tenancy import delete_org_deep

        orgs = db.session.query(Organization).filter(Organization.is_demo.is_(True)).all()
        if not orgs:
            click.echo("No seeded demo organisation present — nothing to do.")
            return
        for org in orgs:
            click.echo(f"  deleting '{org.name}' (id={org.id}) and all its data…")
            delete_org_deep(org)  # FK-safe (works on Postgres)
        db.session.commit()
        click.echo(f"✓ Wiped {len(orgs)} demo organisation(s). Create your real company with create-owner.")

    @app.cli.command("backfill-timestamps")
    def backfill_timestamps() -> None:
        """Give historical rows a real timestamp so the UI stops showing fuzzy
        'nu'/'zojuist'. Messages: received_at ← time hint, else the import time
        (created_at). Audit: occurred_at ← parseable time label. Idempotent."""
        from app.extensions import db
        from app.models import AuditLog, Message
        from app.timefmt import received_from_hint, reltime

        m_fixed = 0
        for m in db.session.query(Message).filter(Message.received_at.is_(None)):
            m.received_at = received_from_hint(m.time_display) or m.created_at
            if m.received_at:
                m.time_display = reltime(m.received_at, m.time_display)
                m_fixed += 1
        a_fixed = 0
        for a in db.session.query(AuditLog).filter(AuditLog.occurred_at.is_(None)):
            if (ts := received_from_hint(a.occurred_display)):
                a.occurred_at = ts
                a_fixed += 1
        db.session.commit()
        click.echo(f"✓ Backfilled {m_fixed} message + {a_fixed} audit timestamp(s).")

    @app.cli.command("sync-default-categories")
    @click.argument("slug")
    def sync_default_categories(slug: str) -> None:
        """Add any missing DEFAULT categories/subcategories to an existing org.

        Idempotent and non-destructive: never edits or deletes a tenant's own
        categories — only appends defaults they don't have yet (e.g. after the
        default taxonomy grew from 5 to 7).
        """
        from app.extensions import db
        from app.models import Category, Organization, Subcategory
        from app.seed import demo_data as D

        org = db.session.query(Organization).filter_by(slug=slug).first()
        if not org:
            click.echo(f"No organisation with slug '{slug}'.")
            return
        existing = {c.key: c for c in org.categories}
        pos = max((c.position for c in org.categories), default=-1) + 1
        added_c = added_s = 0
        for c in D.resolve(D.CATEGORIES):
            cat = existing.get(c["key"])
            if cat is None:
                cat = Category(
                    organization_id=org.id, key=c["key"], name=c["name"], color=c["color"],
                    ai=c["ai"], draft_enabled=c["draft_enabled"], is_default=c["is_default"],
                    is_org_template=True, position=pos,
                )
                pos += 1
                db.session.add(cat)
                db.session.flush()
                added_c += 1
            have = {s.name for s in cat.subcategories}
            for j, (sname, sai, sde) in enumerate(c["subs"]):
                if sname not in have:
                    db.session.add(Subcategory(category_id=cat.id, name=sname, ai=sai, draft_enabled=sde, position=j))
                    added_s += 1
        db.session.commit()
        click.echo(f"✓ {org.name}: added {added_c} categories + {added_s} subcategories (existing kept).")

    @app.cli.command("go-live-check")
    def go_live_check() -> None:
        """Read-only production preflight: what does this installation still miss?

        Checks secrets, database, background tasks, AI, the mailer, migrations, the
        administrator account and demo data, and reports ✓ (ok), ⚠ (recommended) or
        ✗ (blocker) per item. Changes nothing. Exits 1 when there is a blocker, so it can
        run before a deploy."""
        import os

        from sqlalchemy import inspect as sa_inspect

        from app.extensions import db

        cfg = app.config
        blockers = 0
        warnings = 0

        def ok(msg: str) -> None:
            click.echo(f"  ✓ {msg}")

        def warn(msg: str) -> None:
            nonlocal warnings
            warnings += 1
            click.echo(f"  ⚠ {msg}")

        def bad(msg: str) -> None:
            nonlocal blockers
            blockers += 1
            click.echo(f"  ✗ {msg}")

        # ── Secrets & runtime ────────────────────────────────────────────────
        click.echo("Secrets & runtime")
        sk = str(cfg.get("SECRET_KEY") or "").lower()
        weak = not sk or any(m in sk for m in ("insecure", "change-me", "change_me", "changeme"))
        (bad if weak else ok)("SECRET_KEY is missing or still a placeholder" if weak else "SECRET_KEY set")
        from app.config import _DEFAULT_TOKEN_KEY

        tk = str(cfg.get("TOKEN_ENCRYPTION_KEY") or "")
        (ok if tk and tk != _DEFAULT_TOKEN_KEY else bad)(
            "TOKEN_ENCRYPTION_KEY set (unique)" if tk and tk != _DEFAULT_TOKEN_KEY
            else "TOKEN_ENCRYPTION_KEY is missing or still the public development key")
        base = str(cfg.get("PUBLIC_BASE_URL") or "")
        (ok if base.startswith("https://") else warn)(
            f"PUBLIC_BASE_URL = {base}" if base.startswith("https://")
            else f"PUBLIC_BASE_URL is '{base}' — OAuth, e-mail links and the Outlook add-in need the public https URL")

        db_uri = str(cfg.get("SQLALCHEMY_DATABASE_URI") or "")
        (ok if db_uri.startswith("postgres") else warn)(
            "Database = PostgreSQL" if db_uri.startswith("postgres")
            else f"Database is not PostgreSQL ({db_uri.split(':', 1)[0]}) — recommended for production")

        if cfg.get("REDIS_URL"):
            ok("REDIS_URL set (background worker + shared rate limits)")
        else:
            warn("REDIS_URL not set — tasks run in-process and rate limits are per process")

        # ── AI ───────────────────────────────────────────────────────────────
        click.echo("AI")
        provider = (cfg.get("LLM_PROVIDER") or "stub").lower()
        key_for = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "azure": "AZURE_OPENAI_API_KEY"}
        if provider == "stub":
            warn("LLM_PROVIDER=stub — no instance-wide AI model (organisations can still add their own key)")
        elif provider in key_for and not cfg.get(key_for[provider]):
            bad(f"LLM_PROVIDER={provider} but {key_for[provider]} is missing")
        else:
            ok(f"LLM_PROVIDER={provider} configured")

        # ── Mail ─────────────────────────────────────────────────────────────
        click.echo("System mail (verification, password reset, invitations)")
        if cfg.get("MAIL_CONFIGURED"):
            ok(f"Mailer configured ({cfg.get('MAIL_PROVIDER')}, from {cfg.get('MAIL_FROM')})")
        else:
            bad("No mailer configured — set SMTP_* (or MAIL_PROVIDER=graph + GRAPH_MAIL_*)")

        # ── Data & migrations ────────────────────────────────────────────────
        click.echo("Data & migrations")
        try:
            from alembic.config import Config as _AConfig
            from alembic.script import ScriptDirectory

            _c = _AConfig()
            _c.set_main_option("script_location", "migrations")
            heads = ScriptDirectory.from_config(_c).get_heads()
            (ok if len(heads) == 1 else bad)(
                f"One migration head ({heads[0]})" if len(heads) == 1
                else f"{len(heads)} migration heads — `flask db upgrade` will fail: {', '.join(heads)}")
        except Exception as exc:  # noqa: BLE001
            warn(f"Could not determine migration heads ({exc})")

        try:
            from app.models import Organization, User

            insp = sa_inspect(db.engine)
            if insp.has_table("users"):
                admins = db.session.query(User).filter(User.is_platform_owner.is_(True)).count()
                (ok if admins else warn)(
                    f"{admins} administrator account(s)" if admins
                    else "No administrator yet — open the app to run the setup, or `flask create-owner`")
                demo = (db.session.query(Organization)
                        .filter(Organization.is_demo.is_(True), Organization.deleted_at.is_(None)).count()
                        if insp.has_table("organizations") else 0)
                seed_env = (os.environ.get("SEED_DEMO") or "").strip().lower() in ("1", "true", "yes")
                if demo or seed_env:
                    warn("Demo data present or SEED_DEMO is on — set SEED_DEMO=0 and run `flask wipe-demo`")
                else:
                    ok("No demo organisation in the database")
            else:
                warn("Database not migrated yet — run `flask db upgrade` and repeat this check")
        except Exception as exc:  # noqa: BLE001
            warn(f"Database checks skipped ({exc})")

        # ── Summary ──────────────────────────────────────────────────────────
        click.echo("")
        if blockers:
            click.echo(f"✗ {blockers} blocker(s), {warnings} warning(s). Not ready for production yet.")
            raise SystemExit(1)
        if warnings:
            click.echo(f"✓ No blockers, {warnings} warning(s) to consider.")
        else:
            click.echo("✓ All checks green — ready for production.")
