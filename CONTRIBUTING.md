# Contributing to Otto

Thanks for wanting to improve Otto! Bug reports, ideas and pull requests are welcome.

## Before you start

- **Bugs**: open an issue with the steps to reproduce, what you expected and what
  happened. Leave out personal data and e-mail content.
- **Bigger changes**: open an issue first to discuss the idea, so your work fits the
  direction of the project.
- **Security problems**: never in a public issue — see [SECURITY.md](SECURITY.md).

## Development setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
flask db upgrade && flask seed
flask run
```

Before you push:

```bash
ruff check .
pytest
flask check-migration-heads
```

## Guidelines

- **Otto never sends mail on its own.** Don't add send operations to the mailbox
  pipeline or send permissions to the provider apps.
- **Tenant isolation**: every query on organisation data is scoped to the organisation.
  Add a test when you touch data access.
- **Database changes** go through an Alembic migration in `migrations/versions`, with
  exactly one head.
- **Translations**: Dutch is the source language. When you add or change user-facing
  text, extract and update the catalogues (`scripts/i18n.sh extract`, `update`), add the
  French and English translations, and compile (`scripts/i18n.sh compile`).
- Match the style of the surrounding code and keep pull requests focused.

## License of contributions

Otto is published under the [PolyForm Shield License 1.0.0](LICENSE.md), and the
licensor (Digital Genics) also offers commercial licenses. By submitting a contribution
you confirm that you wrote it (or have the right to submit it), and you grant Digital
Genics a perpetual, worldwide, royalty-free license to use, modify and distribute your
contribution under the PolyForm Shield License and under commercial licenses.
