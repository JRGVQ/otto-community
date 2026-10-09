"""`flask go-live-check` — read-only preflight that reports what production still misses.

Guards two things: the report covers every section, and it reads the configuration live
(filling in a missing setting makes its blocker disappear).
"""
from __future__ import annotations


def test_reports_every_section_and_exits_on_blockers(app):
    result = app.test_cli_runner().invoke(args=["go-live-check"])
    out = result.output
    for section in ("Secrets & runtime", "AI", "System mail", "Data & migrations"):
        assert section in out, section
    # The test config has the dev secrets and no mailer → blockers → exit code 1.
    assert result.exit_code == 1
    assert "No mailer configured" in out
    assert "blocker" in out.lower()


def test_reads_config_live_so_filling_a_key_clears_its_blocker(app):
    before = app.test_cli_runner().invoke(args=["go-live-check"]).output
    assert "No mailer configured" in before

    app.config["MAIL_CONFIGURED"] = True
    after = app.test_cli_runner().invoke(args=["go-live-check"]).output
    assert "No mailer configured" not in after
    assert "Mailer configured" in after


def test_an_administrator_is_reported(app):
    out = app.test_cli_runner().invoke(args=["go-live-check"]).output
    assert "administrator account(s)" in out  # the seeded demo login is an administrator
