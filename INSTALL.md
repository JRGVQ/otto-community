# Installing Otto

This guide takes you from nothing to a working Otto installation: first a local trial
run on your own computer, then a production setup on a server with Docker, and finally
connecting Microsoft 365, Gmail and an AI model.

- [1. Try it locally (5 minutes)](#1-try-it-locally-5-minutes)
- [2. Production with Docker](#2-production-with-docker)
- [3. First login and the administrator](#3-first-login-and-the-administrator)
- [4. Connect Microsoft 365](#4-connect-microsoft-365)
- [5. Connect Gmail / Google Workspace](#5-connect-gmail--google-workspace)
- [6. Connect an AI model](#6-connect-an-ai-model)
- [7. System e-mail](#7-system-e-mail)
- [8. Optional features](#8-optional-features)
- [9. Operations: updates, backups, monitoring](#9-operations-updates-backups-monitoring)
- [10. Configuration reference](#10-configuration-reference)
- [11. Command reference](#11-command-reference)
- [12. Troubleshooting](#12-troubleshooting)

---

## 1. Try it locally (5 minutes)

You need **Python 3.12** (3.13 works too) and git. No database server, Redis or API keys
are needed: locally Otto runs on SQLite, executes background work in-process and uses a
built-in "stub" AI that produces placeholder drafts.

```bash
git clone https://github.com/JRGVQ/otto-community.git otto
cd otto
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
flask db upgrade        # creates instance/otto.db
flask seed              # optional: loads the fictional demo company "Acme NV"
flask run
```

Open <http://localhost:5000>.

- **With the demo data** (`flask seed`): log in as `demo@example.com` / `otto-demo`.
  That account is also the administrator, so you can open the backoffice at `/owner`.
- **Without the demo data**: the first page you see is the **setup screen**. The account
  you create there becomes the administrator of this installation.

Want to try real mailboxes and AI locally? Install the full set of integrations with
`pip install -r requirements-prod.txt` and continue with sections 4–6, using
`http://localhost:5000` as your base URL.

Run the test suite with `pytest` and the linter with `ruff check .`.

---

## 2. Production with Docker

### What you need

- A Linux server (2 GB RAM is enough to start) with **Docker** and the **Docker Compose
  plugin**. Any provider works.
- A **domain name** pointing at the server, e.g. `otto.example.com` (an `A` record to the
  server's IP address).
- **HTTPS**. Microsoft, Google and the Outlook add-in only talk to an https address.
  The bundled Caddy proxy gets a free Let's Encrypt certificate automatically; you can
  also use your own reverse proxy.
- An **SMTP account** (or a Microsoft 365 tenant) to send system e-mail — verification
  links, password resets and invitations. See [section 7](#7-system-e-mail).

### Install

```bash
git clone https://github.com/JRGVQ/otto-community.git otto
cd otto
cp .env.production.example .env
```

Edit `.env` and fill in at least:

| Setting | Value |
|---|---|
| `PUBLIC_BASE_URL` | `https://otto.example.com` — your public address, no trailing slash |
| `OTTO_DOMAIN` | `otto.example.com` — only needed for the bundled Caddy |
| `POSTGRES_PASSWORD` | a long random password: `openssl rand -base64 24` |
| `SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `TOKEN_ENCRYPTION_KEY` | see below |
| `SMTP_*`, `MAIL_FROM` | your mail server (section 7) |

Generate the encryption key (it needs the `cryptography` package; if you don't have it,
leave the value empty — the container then generates one on first start and stores it in
the `otto_data` volume):

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> **Keep `SECRET_KEY` and `TOKEN_ENCRYPTION_KEY` safe and never change them.**
> `TOKEN_ENCRYPTION_KEY` encrypts the stored mailbox tokens and AI keys: lose or change
> it and every mailbox has to be reconnected. Store both in your password manager.

Start everything. With the bundled Caddy (ports 80 and 443 must be free and reachable):

```bash
docker compose --profile caddy up -d --build
```

Or behind your own reverse proxy (nginx, Traefik, …) that forwards to `127.0.0.1:8000`:

```bash
docker compose up -d --build
```

The first start builds the image and runs the database migrations; follow it with
`docker compose logs -f web` until you see gunicorn listening. Then open your domain in a
browser and complete the setup ([section 3](#3-first-login-and-the-administrator)).

The stack consists of:

| Service | Role |
|---|---|
| `web` | the Flask app (gunicorn); runs migrations on start |
| `worker` | Celery worker: mailbox sync, categorisation, drafts |
| `beat` | Celery beat: the periodic jobs (polling, clean-up, follow-ups, …) |
| `db` | PostgreSQL 16 |
| `redis` | Redis 7 (task queue and rate limits) |
| `caddy` | optional HTTPS reverse proxy (`--profile caddy`) |

**Using your own reverse proxy?** Forward to `127.0.0.1:8000`, pass the usual
`X-Forwarded-For`, `X-Forwarded-Proto` and `X-Forwarded-Host` headers, and keep it to
**one** proxy hop (Otto trusts exactly one). If you put e.g. Cloudflare *and* nginx in
front, client IPs in the logs and rate limits will be those of the inner proxy.

**Smaller setup without Redis.** Otto also runs as a single container: leave `REDIS_URL`
unset and it executes tasks in-process with a built-in scheduler. The provided compose
file always includes Redis, which is the recommended setup.

---

## 3. First login and the administrator

On a fresh installation every page redirects to **`/auth/setup`**. Fill in your company
name, your name, e-mail address and a password. This creates:

- the first **organisation** (your company), with you as its owner, and
- your account as **administrator** of the installation, with access to the backoffice
  at **`/owner`** (shown as *Platform* in the app).

After that the setup page is gone for good. Do this right after the first start: until
it's done, whoever opens the site first becomes the administrator. Prefer the command
line? Create the administrator before opening the site:

```bash
docker compose exec web flask-admin create-owner
```

### Organisations and users

- **Colleagues** are invited by an organisation's owner or admins under
  *Beheer → Gebruikers*. They get an e-mail with a link to set their password.
- **More organisations** (e.g. you host Otto for several companies): the administrator
  creates them in **`/owner` → Bedrijven → Nieuw bedrijf**.
- **Self-registration** is off by default. Set `REGISTRATION_OPEN=1` to let anyone create
  their own organisation at `/auth/register` (they confirm their e-mail address first).

The backoffice also lets the administrator suspend or re-activate an organisation, move
it to the trash (restorable for 30 days, then deleted), look at the system status, the
scheduler, the request log and server errors, and edit the system e-mail templates.

### Language

The interface is available in Dutch, French and English; every user picks their own
language with the globe icon. Set the default with `BABEL_DEFAULT_LOCALE` (`nl`, `fr`
or `en`) and the timezone with `APP_TIMEZONE` (default `Europe/Brussels`).

---

## 4. Connect Microsoft 365

Otto connects mailboxes through an **app registration** in Microsoft Entra ID. You
register it once; after that every user connects their mailbox with one click and a
consent screen.

> An organisation can also use **its own** app registration instead of the instance-wide
> one: its owner opens *Integraties → Eigen Microsoft/Google-app gebruiken*. That keeps
> consent inside their own tenant. The steps are the same.

### 4.1 Register the app

1. Go to the [Entra admin center](https://entra.microsoft.com) →
   **Identity → Applications → App registrations → New registration**.
2. **Name**: `Otto`.
3. **Supported account types**:
   - only your own company → *Accounts in this organizational directory only*
     (then set `MS_TENANT_ID` to your tenant ID);
   - mailboxes from several companies → *Accounts in any organizational directory
     (Multitenant)* (keep `MS_TENANT_ID=common`).
4. **Redirect URI**: platform **Web**, value exactly
   `https://otto.example.com/auth/microsoft/callback` (your `PUBLIC_BASE_URL` +
   `/auth/microsoft/callback`).
5. **Register**, and copy the **Application (client) ID** from the overview page.

### 4.2 API permissions

**API permissions → Add a permission → Microsoft Graph → Delegated permissions**. Add
them all at once: a permission added later means every connected user has to reconnect.

| Permission | Used for |
|---|---|
| `Mail.ReadWrite` | read mail, apply categories, write draft replies |
| `Mail.ReadWrite.Shared` | shared mailboxes (Teams) |
| `MailboxSettings.ReadWrite` | Outlook categories and inbox rules |
| `Tasks.ReadWrite` | tasks in Microsoft To Do / Planner |
| `Calendars.ReadWrite` | propose free slots, add appointments |
| `User.Read` | the signed-in user's profile |
| `offline_access` | refresh tokens for background sync |

These need no administrator: each user consents for themselves.

Optional, **admin consent required** — an administrator approves them once for the
whole organisation via the *Goedkeuring voor je organisatie* button under *Integraties*:

| Permission | Used for |
|---|---|
| `User.ReadBasic.All` | pick a colleague when assigning a Planner task |
| `GroupMember.Read.All` | assign signatures per Microsoft 365 group |
| `OnlineMeetingTranscript.Read.All` | action items from Teams meeting transcripts |

**Never add `Mail.Send`.** Otto never sends mail on a user's behalf — it only writes
drafts — and an app without that permission makes this verifiable.

### 4.3 Client secret

**Certificates & secrets → Client secrets → New client secret**, expiry e.g. 24 months.
Copy the **Value** immediately (it is shown only once). Put the expiry date in your
calendar: when the secret expires, mailbox sync stops until you create a new one.

### 4.4 Configure Otto

```ini
MS_CLIENT_ID=<Application (client) ID>
MS_CLIENT_SECRET=<secret value>
MS_TENANT_ID=common            # or your tenant ID for a single-tenant app
```

Restart (`docker compose up -d`). In the app, *Onboarding* or *Integraties* now shows
**Verbinden met Microsoft 365**.

---

## 5. Connect Gmail / Google Workspace

### 5.1 Project and API

1. [Google Cloud Console](https://console.cloud.google.com) → **New project** (`Otto`).
2. **APIs & Services → Library** → **Gmail API** → **Enable**.

### 5.2 OAuth consent screen

**APIs & Services → OAuth consent screen**:

- **User type**:
  - **Internal** if only accounts of your own Google Workspace organisation will connect.
    This is the easiest route: no Google verification needed.
  - **External** for personal @gmail.com accounts or other organisations. Otto uses the
    `gmail.modify` scope, which Google classifies as *restricted*: until your app has
    passed Google's verification and security assessment, it stays in *Testing* mode and
    only works for the test users you list (max. 100).
- **Scopes**: add `https://www.googleapis.com/auth/gmail.modify` (read, label and write
  drafts). Otto never asks for `gmail.send`.

### 5.3 OAuth client

**APIs & Services → Credentials → Create credentials → OAuth client ID**:

- **Application type**: Web application.
- **Authorized redirect URI**: `https://otto.example.com/auth/google/callback`.

Copy the client ID and secret into `.env`:

```ini
GOOGLE_CLIENT_ID=<client id>
GOOGLE_CLIENT_SECRET=<client secret>
```

Restart. For a local test over plain http, also set `OAUTHLIB_INSECURE_TRANSPORT=1`
(never in production) and use `http://localhost:5000/auth/google/callback`.

---

## 6. Connect an AI model

Otto categorises mail and writes drafts with a large language model. There are two ways
to provide one, and they can be combined:

1. **One model for the whole installation**, configured in `.env`:

   ```ini
   LLM_PROVIDER=anthropic          # anthropic | openai | azure | stub
   ANTHROPIC_API_KEY=sk-ant-...
   # LLM_MODEL=claude-sonnet-4-6   # optional, otherwise the provider default
   ```

   For OpenAI use `LLM_PROVIDER=openai` + `OPENAI_API_KEY`; for Azure OpenAI use
   `LLM_PROVIDER=azure` + `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` and
   `AZURE_OPENAI_DEPLOYMENT`.

2. **A key per organisation**, set by its admins in the app under
   *Beheer → AI & model*: Anthropic, OpenAI, Azure OpenAI or any OpenAI-compatible endpoint
   (Mistral, Groq, OpenRouter, a local server, …) with its base URL. An organisation's own
   key always takes precedence over the instance-wide model.

Without either, Otto runs on the **stub**: simple built-in rules and placeholder drafts,
handy to explore the app. Admins see a banner pointing them to the AI settings.

**Costs.** Otto meters the token usage per organisation and month (visible under
*Analyse*). To cap what an organisation may spend on the instance-wide key, set
`LLM_MONTHLY_BUDGET_EUR` (e.g. `50`); once reached, that organisation falls back to the
stub until the next month. Keys set by an organisation itself are never capped.

---

## 7. System e-mail

Otto sends a few e-mails of its own: address verification, password resets, invitations
and notices to the administrator. (It never sends mail *from users' mailboxes*.)

**SMTP** — any provider that supports STARTTLS on port 587:

```ini
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=otto@example.com
SMTP_PASSWORD=...
SMTP_USE_TLS=true
MAIL_FROM=Otto <otto@example.com>
```

Make sure `MAIL_FROM` is a domain your provider may send for (SPF/DKIM), or the mail
ends up in spam.

**Microsoft 365 without SMTP** — Microsoft has switched off password-based SMTP for most
tenants. Use an app-only registration instead (separate from the one in section 4):

1. Entra → **New registration** (`Otto mailer`, single tenant), create a client secret.
2. **API permissions → Microsoft Graph → Application permissions → `Mail.Send`**, then
   **Grant admin consent**.
3. Restrict it to the sender mailbox — otherwise the app may send as *any* mailbox in your
   tenant. In Exchange Online PowerShell:
   ```powershell
   New-ApplicationAccessPolicy -AppId <client-id> `
     -PolicyScopeGroupId <mail-enabled group containing otto@example.com> `
     -AccessRight RestrictAccess -Description "Otto mailer: only otto@example.com"
   ```
4. Configure:
   ```ini
   MAIL_PROVIDER=graph
   GRAPH_MAIL_TENANT_ID=<directory (tenant) id>
   GRAPH_MAIL_CLIENT_ID=<application (client) id>
   GRAPH_MAIL_CLIENT_SECRET=<secret value>
   GRAPH_MAIL_SENDER=otto@example.com
   ```

Test it with *Wachtwoord vergeten* on the login page, or from `/owner` → *E-mail &
sjablonen*. Without a mailer, use `flask-admin verify-user` and `flask-admin set-password`
to activate accounts by hand.

---

## 8. Optional features

### Realtime sync instead of polling

By default Otto polls every connected mailbox (every 5 minutes, adjustable per mailbox).

- **Microsoft 365 webhooks**: set `GRAPH_CLIENT_STATE` to a long random string. Needs a
  public https `PUBLIC_BASE_URL` and the worker/beat setup (Redis); Otto then creates and
  renews the Graph subscriptions itself. Shared mailboxes keep polling.
- **Gmail push**: create a Pub/Sub topic, give
  `gmail-api-push@system.gserviceaccount.com` the *Pub/Sub Publisher* role on it, add an
  authenticated push subscription to `https://otto.example.com/api/webhooks/gmail` (with
  that URL as audience), and set all three of `GMAIL_PUBSUB_TOPIC`,
  `GMAIL_PUSH_AUDIENCE` and `GMAIL_PUSH_SA_EMAIL`.

### Company knowledge from SharePoint

Drafts can draw on documents in SharePoint. Create another app-only registration with
the application permission `Sites.Read.All` (admin consent) and set
`GRAPH_KNOWLEDGE_TENANT_ID`, `GRAPH_KNOWLEDGE_CLIENT_ID`,
`GRAPH_KNOWLEDGE_CLIENT_SECRET` and `GRAPH_KNOWLEDGE_REGION` (e.g. `EUR`). Organisations
can also upload documents themselves under *Beheer → Bedrijfskennis*.

### Single sign-on

Each organisation can configure OIDC or SAML 2.0 login under *Beheer → Aanmelden &
beveiliging*. Redirect / ACS URLs: `<PUBLIC_BASE_URL>/auth/sso/callback` (OIDC) and
`<PUBLIC_BASE_URL>/auth/saml/acs` (SAML; metadata at `/auth/saml/metadata`).

### Outlook add-in

Otto includes a task pane for Outlook (signatures, drafts). Its manifest is served at
`<PUBLIC_BASE_URL>/addin/manifest.xml`; a Microsoft 365 admin installs it under
*Microsoft 365 admin center → Settings → Integrated apps → Upload custom apps*. The
add-in id is derived from `SECRET_KEY`; set `ADDIN_ID` to a fixed GUID if you ever need
to rotate that key.

### CRM

Organisations can log customer mail to their own Odoo, HubSpot or Pipedrive, or to any
webhook, under *Integraties*. No server configuration needed.

### Error alerts

`SENTRY_DSN` sends errors to Sentry. Or let Otto open a GitHub issue when an error
spikes: set `GITHUB_ISSUE_REPO` (`owner/repo`) and a `GITHUB_TOKEN` with only
`issues: write` on that repository. Every server error is also visible in `/owner` →
*Debug & errors*.

---

## 9. Operations: updates, backups, monitoring

### Updates

```bash
cd otto
git pull
docker compose --profile caddy up -d --build     # or without --profile caddy
docker image prune -f
```

Migrations run automatically when the `web` container starts.

### Backups

Back up the PostgreSQL database **and** keep a copy of your `.env` (or at least
`SECRET_KEY` and `TOKEN_ENCRYPTION_KEY`). If you let the container generate the secrets,
back up the `otto_data` volume too.

```bash
# one-off dump
docker compose exec -T db pg_dump -U otto otto | gzip > otto-$(date +%F).sql.gz
# restore
gunzip -c otto-2026-01-31.sql.gz | docker compose exec -T db psql -U otto otto
```

`scripts/backup.sh` makes a dump and keeps the 14 most recent ones. Schedule it with cron:

```cron
30 3 * * * cd /home/otto/otto && OTTO_BACKUP_DIR=/opt/otto-backups bash scripts/backup.sh >> /var/log/otto-backup.log 2>&1
```

Copy the backup directory off the server as well.

### Health checks

| Endpoint | Meaning |
|---|---|
| `/healthz` | the process serves requests; always `200`, with `"db": "error"` when the database is down. Used as the container health check. |
| `/readyz` | `503` when the database is unreachable. For external monitoring only. |

`docker compose exec web flask-admin go-live-check` lists what the installation still
misses (secrets, mailer, AI, migrations, administrator, demo data).

### Data protection

Otto stores e-mail content of connected mailboxes. Per organisation you can export all
data (`flask-admin export-org <slug>`) and remove a person (`flask-admin erase-user
<email>`). Deleted organisations are wiped for good 30 days after they were moved to the
trash. As the operator you are responsible for the GDPR obligations towards your users.

---

## 10. Configuration reference

All settings are environment variables (in `.env`). Only the first block is required
for production.

### Core

| Variable | Default | Description |
|---|---|---|
| `PUBLIC_BASE_URL` | `http://localhost:5000` | Public address; OAuth redirects, e-mail links, webhooks, add-in. **Required.** |
| `SECRET_KEY` | dev value | Signs sessions and links. **Required** in production. |
| `TOKEN_ENCRYPTION_KEY` | dev value | Fernet key for stored tokens and keys. **Required**, never change it. |
| `DATABASE_URL` | SQLite in `instance/` | `postgresql+psycopg://user:pass@host:5432/db` |
| `REDIS_URL` | — | Enables the Celery worker/beat and shared rate limits. |
| `FLASK_CONFIG` | `dev` | `prod` in production (the compose file sets it). |
| `OTTO_DOMAIN` | — | Domain for the bundled Caddy. |
| `OTTO_PORT` | `8000` | Port on 127.0.0.1 where the web container is published. |
| `WEB_CONCURRENCY` | `2` | Gunicorn workers. |

### Accounts and access

| Variable | Default | Description |
|---|---|---|
| `REGISTRATION_OPEN` | `0` | `1` lets anyone create an organisation at `/auth/register`. |
| `PLATFORM_OWNER_EMAIL` | — | Restricts the `/owner` backoffice to this one address. |
| `SHOW_VERIFY_LINK` | `0` (`1` in dev) | Show verification links on screen when no mail is sent (test setups only). |

### AI

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `stub` | `anthropic`, `openai`, `azure` or `stub`. |
| `LLM_MODEL` | provider default | Model id (or leave empty). |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | — | Key for the chosen provider. |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT` | — | Azure OpenAI. |
| `LLM_MONTHLY_BUDGET_EUR` | — | Optional monthly cap per organisation on the instance key. |
| `LLM_USD_EUR` | `0.95` | Exchange rate used to price token usage. |

### Mailbox providers

| Variable | Default | Description |
|---|---|---|
| `MS_CLIENT_ID`, `MS_CLIENT_SECRET` | — | Microsoft 365 app (section 4). |
| `MS_TENANT_ID` | `common` | `common` for multi-tenant, or your tenant id. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | — | Google OAuth client (section 5). |
| `GRAPH_CLIENT_STATE` | — | Enables Microsoft 365 webhooks. |
| `GMAIL_PUBSUB_TOPIC`, `GMAIL_PUSH_AUDIENCE`, `GMAIL_PUSH_SA_EMAIL` | — | Gmail push (all three). |
| `GRAPH_TIMEOUT_SECONDS` | `20` | Timeout per Microsoft Graph call. |
| `GRAPH_KNOWLEDGE_TENANT_ID`, `_CLIENT_ID`, `_CLIENT_SECRET`, `_REGION` | — | SharePoint knowledge search. |
| `SYNC_INTERVAL_SECONDS` | `300` | Default polling interval per mailbox. |
| `SYNC_MIN_INTERVAL_SECONDS` | `60` | Lowest interval a mailbox may be set to. |

### System e-mail

| Variable | Default | Description |
|---|---|---|
| `MAIL_PROVIDER` | `smtp` | `smtp` or `graph`. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | —, `587` | SMTP server (STARTTLS). |
| `SMTP_USE_TLS` | `true` | STARTTLS on/off. |
| `MAIL_FROM` | `Otto <otto@example.com>` | Sender of system e-mail. |
| `GRAPH_MAIL_TENANT_ID`, `_CLIENT_ID`, `_CLIENT_SECRET`, `_SENDER` | — | Microsoft 365 app-only mailer. |
| `DAILY_EMAIL_HOUR` | `7` | Hour for the optional daily task digest. |

### Interface, logging and monitoring

| Variable | Default | Description |
|---|---|---|
| `BABEL_DEFAULT_LOCALE` | `nl` | Default UI language: `nl`, `fr`, `en`. |
| `APP_TIMEZONE` | `Europe/Brussels` | Timezone for displayed times. |
| `LOG_LEVEL` | `INFO` | Application log level. |
| `REQUEST_LOG_ENABLED` | `1` | Request log in `/owner` → Logboek. |
| `LOG_RETENTION_DAYS` | `30` | Retention of request/error logs (also adjustable in `/owner`). |
| `SENTRY_DSN` | — | Sentry error tracking. |
| `GITHUB_ISSUE_REPO`, `GITHUB_TOKEN` | — | GitHub issue on an error spike. |
| `ERROR_ALERT_THRESHOLD`, `_WINDOW_MINUTES`, `_COOLDOWN_HOURS` | `10`, `15`, `24` | When to open such an issue. |
| `APP_VERSION` | — | Shown on the status page. |

### Advanced

| Variable | Default | Description |
|---|---|---|
| `TRUST_PROXY` | `1` in production | Trust one reverse proxy's `X-Forwarded-*` headers. |
| `TASK_ALWAYS_EAGER` | on without Redis | Run tasks in-process. |
| `BACKGROUND_SYNC` | = eager | Built-in scheduler thread (when there is no beat). |
| `SCHEDULER_TICK_SECONDS` | `60` | How often the built-in scheduler wakes up. |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_RECYCLE`, `DB_POOL_TIMEOUT`, `DB_CONNECT_TIMEOUT` | `5`, `10`, `280`, `10`, `10` | PostgreSQL pool. |
| `CRM_TIMEOUT_SECONDS` | `10` | Timeout for organisations' CRM connections. |
| `ADDIN_ID` | derived | Fixed GUID for the Outlook add-in. |
| `SEED_DEMO` | `0` | `1` seeds the demo organisation on start (try-outs only). |
| `RUN_MIGRATIONS` | `1` on `web` | Run migrations when the container starts. |

---

## 11. Command reference

Inside the container, use `flask-admin <command>` (it loads the generated secrets);
locally, `flask <command>`.

| Command | What it does |
|---|---|
| `create-owner` | Create an organisation + its owner as administrator (asks for the details). |
| `grant-owner EMAIL` / `revoke-owner EMAIL` | Give or take administrator rights. |
| `set-password EMAIL` | Set a password (and mark the address verified). |
| `verify-user EMAIL` | Mark an address as verified without e-mail. |
| `sync-now EMAIL [--initial]` | Sync a user's mailboxes right away. |
| `learn-style EMAIL` | Re-learn a user's writing style from sent mail. |
| `export-org SLUG [--out file]` | Export all data of an organisation as JSON. |
| `erase-user EMAIL` | Delete a user and their data (GDPR). |
| `seed` / `wipe-demo` | Add / remove the demo organisation. |
| `db upgrade` | Apply database migrations. |
| `db-doctor [--fix]` | Compare the database with the models and repair missing tables/columns. |
| `check-migration-heads` | Check that the migrations form one chain (used in CI). |
| `go-live-check` | Production preflight. |
| `dedupe-mailboxes`, `sync-default-categories SLUG`, `backfill-timestamps` | Maintenance helpers. |

Example: `docker compose exec web flask-admin set-password you@example.com`.

---

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `web` keeps restarting | `docker compose logs web`. Usually a missing `POSTGRES_PASSWORD`, or a placeholder left in `SECRET_KEY` / `TOKEN_ENCRYPTION_KEY`. |
| Caddy gets no certificate | DNS does not point at the server yet, or ports 80/443 are closed. Check with `dig +short otto.example.com`. |
| 502 Bad Gateway | `web` is still starting (migrations). Watch `docker compose logs -f web`. |
| Login works locally but not in production | Production cookies require https; open the site over `https://` with `PUBLIC_BASE_URL` set accordingly. |
| No verification / reset e-mail arrives | Mailer not configured or rejected — see `/owner` → *E-mail & sjablonen* (send log) and section 7. |
| `AADSTS50011` (redirect URI mismatch) | The redirect URI in Entra must be exactly `PUBLIC_BASE_URL` + `/auth/microsoft/callback`. |
| `AADSTS7000215` (invalid client secret) | The Microsoft client secret expired or is wrong; create a new one. |
| "Need admin approval" at Microsoft | An admin-only permission is on the app (e.g. `User.ReadBasic.All`): remove it, or let an admin approve it once via *Integraties*. |
| `redirect_uri_mismatch` at Google | The authorised redirect URI must be exactly `PUBLIC_BASE_URL` + `/auth/google/callback`. |
| `access_denied` at Google | The consent screen is in *Testing* and the address is not a test user. |
| Drafts look like placeholders | No AI model configured — see section 6. |
| Mailbox stopped syncing | `/owner` → *Scheduler* shows the last runs; `/owner` → *Debug & errors* shows failures. Often an expired client secret. |
| Mailboxes must reconnect after a restart | `TOKEN_ENCRYPTION_KEY` changed. Restore the old key from your backup. |
| `column … does not exist` errors | The database is behind the code: `docker compose exec web flask-admin db-doctor --fix`. |
