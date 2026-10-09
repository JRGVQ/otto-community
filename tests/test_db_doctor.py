"""`flask db-doctor` repairs schema drift (missing tables/columns the models expect).

Reproduces the production incident where `alembic_version` sat ahead of the real schema,
so `flask db upgrade` was a no-op and e.g. `tasks.notes` never got created — every request
touching it 500'd with "column tasks.notes does not exist".
"""
from __future__ import annotations

from sqlalchemy import inspect as sa_inspect, text

from app.extensions import db


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa_inspect(db.engine).get_columns(table)}


def test_db_doctor_dry_run_reports_but_does_not_fix(app):
    db.session.execute(text('ALTER TABLE tasks DROP COLUMN notes'))
    db.session.commit()
    assert "notes" not in _columns("tasks")

    result = app.test_cli_runner().invoke(args=["db-doctor"])
    assert result.exit_code == 0
    assert "tasks.notes" in result.output
    # Dry-run: still missing.
    assert "notes" not in _columns("tasks")


def test_db_doctor_fix_readds_missing_column(app):
    db.session.execute(text('ALTER TABLE tasks DROP COLUMN notes'))
    db.session.commit()
    assert "notes" not in _columns("tasks")

    result = app.test_cli_runner().invoke(args=["db-doctor", "--fix"])
    assert result.exit_code == 0, result.output
    assert "notes" in _columns("tasks")


def test_db_doctor_fix_recreates_missing_table(app):
    db.session.execute(text("DROP TABLE task_steps"))
    db.session.commit()
    assert not sa_inspect(db.engine).has_table("task_steps")

    result = app.test_cli_runner().invoke(args=["db-doctor", "--fix"])
    assert result.exit_code == 0, result.output
    assert sa_inspect(db.engine).has_table("task_steps")


def test_db_doctor_clean_schema_is_a_noop(app):
    result = app.test_cli_runner().invoke(args=["db-doctor", "--fix"])
    assert result.exit_code == 0
    assert "nothing to repair" in result.output.lower()
