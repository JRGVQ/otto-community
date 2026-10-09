# Otto

**A self-hosted AI e-mail assistant for Microsoft 365 and Gmail.**

Otto connects to your mailbox, sorts incoming mail into categories and prepares draft
replies in your own writing style. **Otto never sends anything on its own** — it only
writes drafts in your mailbox; you review, edit and send them yourself.

> Rather not host it yourself? Use [HeyOtto](https://heyotto.eu), the hosted version.

## Features

- **Inbox triage** — every incoming mail lands in a category (to answer, to read,
  newsletters, noise, …) with your own plain-language rules per category, synced back to
  Outlook categories or Gmail labels.
- **Draft replies in your style** — Otto learns your tone, greeting and signature from
  the mail you send, and drafts a reply for the mails that need one, in the sender's
  language. Pick another tone with one click.
- **Follow-ups and commitments** — reminders for sent mail that got no answer, and the
  promises you made in your own mails turned into tasks.
- **Tasks and calendar** — tasks synced with Microsoft To Do / Planner, free-slot
  suggestions and appointments from mail.
- **Daily briefing and analytics** — what needs your attention today, mail volume,
  response times and draft usage.
- **Shared mailboxes (Teams)** — a separate inbox per shared mailbox with its own voice,
  members and settings.
- **Built-in CRM** — contacts and companies collected from your mail, projects, and
  logging to your own Odoo, HubSpot or Pipedrive.
- **E-mail signatures** — central signatures per user, group or domain, a visual builder,
  banners with optional click statistics, and automatic out-of-office replies.
- **Company knowledge** — upload documents (or connect SharePoint) so drafts can use
  your own information.
- **For organisations** — multi-tenant with strict data separation, roles and
  permissions, invitations, SSO (OIDC / SAML 2.0), an audit log, an Outlook add-in, and an
  administrator backoffice for the whole installation.
- **Bring your own AI** — Anthropic Claude, OpenAI, Azure OpenAI or any OpenAI-compatible
  model, configured for the whole installation or per organisation.
- **Three languages** — the interface is available in Dutch, French and English.

## Quick start

```bash
git clone https://github.com/JRGVQ/otto-community.git otto && cd otto
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
flask db upgrade && flask seed && flask run
```

Open <http://localhost:5000> and log in with `demo@example.com` / `otto-demo`. Without
`flask seed` you start with an empty installation and create the first administrator
yourself.

For a production setup with Docker, PostgreSQL and HTTPS, and for connecting Microsoft 365,
Gmail and an AI model, follow **[INSTALL.md](INSTALL.md)**.

## How it works

```
app/
  auth/        login, first-run setup, registration, invitations, OAuth and SSO
  providers/   mailbox adapters: Microsoft Graph, Gmail, IMAP — no "send" in the pipeline
  ai/          LLM service + adapters (Anthropic, OpenAI, Azure, compatible, stub), prompts
  tasks/       the pipeline: sync → categorise → draft → write back; Celery or in-process
  web/         the app screens (inbox, briefing, tasks, Teams, CRM, settings, admin)
  owner/       the administrator backoffice (/owner)
  api/         JSON endpoints for the UI, plus the provider webhooks
  templates/   Jinja2 templates, static/ CSS design tokens + vanilla JavaScript
migrations/    Alembic database migrations
```

Python 3.12, Flask, SQLAlchemy, PostgreSQL (or SQLite locally), Celery + Redis (optional),
Jinja2 and plain JavaScript — no frontend build step.

The "never sends on its own" principle is structural: the mailbox adapters have no send
operation at all, and the Microsoft 365 app is registered without the `Mail.Send`
permission. See `tests/test_never_autosend.py`.

## Development

```bash
pytest            # the test suite (no network or services needed)
ruff check .      # lint
```

Translations live in `app/translations`; Dutch is the source language. After changing
texts run `scripts/i18n.sh extract`, `scripts/i18n.sh update`, translate the new entries
in the French and English catalogues, then `scripts/i18n.sh compile`.

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request, and
[SECURITY.md](SECURITY.md) to report a vulnerability.

## License

Otto is **source-available** under the [PolyForm Shield License 1.0.0](LICENSE.md).

- You **may** use, modify and self-host Otto for free — also inside your company, for
  your own mail and that of your colleagues.
- You **may not** use it to provide a product or service that competes with Otto or
  HeyOtto — for example reselling it, or hosting it for paying customers.

For a commercial license (reselling, hosting Otto for clients, white-labelling, …)
contact **hallo@heyotto.eu**.

"Otto" and "HeyOtto" are names of Digital Genics. The license covers the code, not the
right to present your own service under these names.

The bundled Plus Jakarta Sans font is licensed under the SIL Open Font License 1.1
(`app/static/fonts/OFL.txt`).
