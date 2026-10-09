# Security policy

Otto handles e-mail content and OAuth tokens, so we take security reports seriously.

## Reporting a vulnerability

Please **do not open a public GitHub issue** for security problems. Instead, report them
privately via GitHub's *Report a vulnerability* button (Security → Advisories) on this
repository, or by e-mail to **hallo@heyotto.eu** with "Security" in the subject.

Include what you found, how to reproduce it and the impact you expect. We aim to confirm
receipt within a few working days and will keep you informed while we work on a fix.

## Supported versions

Security fixes go into the `main` branch. Keep your installation up to date by pulling
`main` and rebuilding (see "Updates" in [INSTALL.md](INSTALL.md)).

## Hardening checklist for operators

- Serve Otto over HTTPS only, and keep `SECRET_KEY` and `TOKEN_ENCRYPTION_KEY` secret and
  backed up.
- Leave `REGISTRATION_OPEN` off unless you really want anyone to create an organisation.
- Complete the first-run setup (or run `flask create-owner`) right after the first start.
- Register the Microsoft 365 app **without** `Mail.Send`, and restrict any app-only
  mailer to its sender mailbox with an Application Access Policy.
- Run `flask-admin go-live-check` after every configuration change.
