"""The diagnostic bundle must NEVER leak a secret. These tests plant known secrets
in every channel (config, env, logs) and assert none survive into the report."""

from __future__ import annotations

from waystone._diagnostics import build_report, redact_mapping, redact_text


def test_redact_text_scrubs_known_secret_shapes():
    samples = [
        "key=sk_live_ABCD1234efgh5678",
        "whsec_TESTwebhooksecret123456",
        "Authorization: Bearer waystone_memberkey_abcdef123456",
        "token waystone_AbC123_def-456xyz",
        "google AIzaSyA1234567890abcdefghijklmnopqrstu",
        "jwt eyJhbGciOiJFZERTQSJ9.eyJzZWF0cyI6NX0.c2lnbmF0dXJlYmxvYg",
        "postgresql://waystone:supersecretpw@db:5432/waystone",
    ]
    secrets = ["sk_live_ABCD1234efgh5678", "whsec_TESTwebhooksecret123456",
               "waystone_memberkey_abcdef123456", "waystone_AbC123_def-456xyz",
               "AIzaSyA1234567890abcdefghijklmnopqrstu",
               "eyJhbGciOiJFZERTQSJ9.eyJzZWF0cyI6NX0.c2lnbmF0dXJlYmxvYg",
               "supersecretpw"]
    out = redact_text("\n".join(samples))
    for s in secrets:
        assert s not in out, f"leaked: {s}"
    # The DSN scheme/user is kept (useful for diagnosis), only the password redacted.
    assert "postgresql://waystone:" in out and "@db:5432" in out


def test_redact_mapping_redacts_secret_named_keys():
    cfg = {
        "llm": {"model": "gemini", "api_key": "sk_secret_value_123456"},
        "license": "eyJhbGciOiJ.payload.sig",
        "store": {"database_url": "postgresql://u:pw_secret_99@h/db"},
        "harmless": "keep me",
    }
    red = redact_mapping(cfg)
    flat = repr(red)
    assert "sk_secret_value_123456" not in flat
    assert "pw_secret_99" not in flat
    assert red["llm"]["api_key"] == "***REDACTED***"   # secret-named key → value gone
    assert red["llm"]["model"] == "gemini"             # non-secret kept
    assert red["harmless"] == "keep me"


def test_build_report_does_not_leak_planted_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows Path.home()
    # A unique sentinel planted in config, env, and a log file.
    sentinel_key = "waystone_PLANTED_SENTINEL_KEY_999"
    sentinel_stripe = "sk_live_PLANTEDSTRIPE999"

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "llm:\n  model: test\n  api_key: " + sentinel_key + "\n"
        "store:\n  database_url: postgresql://u:" + sentinel_stripe + "@h/db\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", sentinel_stripe)
    logs = tmp_path / ".waystone" / "logs"
    logs.mkdir(parents=True)
    (logs / "hooks.log").write_text(
        "2026-01-01 ERROR auth Bearer " + sentinel_key + " failed\n", encoding="utf-8")

    report = build_report(str(cfg), log_lines=50)

    assert sentinel_key not in report, "planted API key leaked into the bundle"
    assert sentinel_stripe not in report, "planted Stripe secret leaked into the bundle"
    # But the bundle is still useful — structure + non-secret context present.
    assert "Waystone diagnostics" in report
    assert "Log: hooks.log" in report
    assert "STRIPE_WEBHOOK_SECRET" in report  # env NAME shown (value never)


def test_diagnostics_cli_writes_redacted_file(tmp_path):
    """Drive the real `waystone diagnostics` command end to end."""
    from click.testing import CliRunner

    from waystone.cli import cli

    cfg = tmp_path / "config.yaml"
    cfg.write_text("llm:\n  model: test\n  api_key: sk_live_CLILEAK12345\n", encoding="utf-8")
    out = tmp_path / "diag.txt"
    r = CliRunner().invoke(cli, ["--config", str(cfg), "diagnostics", "-o", str(out)])
    assert r.exit_code == 0, r.output
    text = out.read_text(encoding="utf-8")
    assert "sk_live_CLILEAK12345" not in text   # the planted key never reaches the file
    assert "Waystone diagnostics" in text
