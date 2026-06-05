"""Tests for auto-extraction of chat-plugin message.txt attachments."""

import json

from waystone._hooks import submit


def test_extracts_new_attachment(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(submit, "_spawn_extraction", lambda *a, **k: calls.append((a, k)))

    cwd = tmp_path / "proj"
    inbox = cwd / ".claude" / "discord" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "123-456.txt").write_text("A long Discord message with real project content.")
    db_path = tmp_path / "store" / "context.db"
    db_path.parent.mkdir(parents=True)

    submit._extract_new_inbox_attachments(str(cwd), "proj", db_path)

    assert len(calls) == 1
    assert calls[0][1].get("source") == "123-456.txt"

    ledger = json.loads((db_path.parent / "extracted_inbox.json").read_text())
    assert "123-456.txt" in ledger

    # Second run must not re-extract the same file.
    submit._extract_new_inbox_attachments(str(cwd), "proj", db_path)
    assert len(calls) == 1


def test_empty_attachment_is_ledgered_but_not_extracted(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(submit, "_spawn_extraction", lambda *a, **k: calls.append(a))

    inbox = tmp_path / ".claude" / "discord" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "empty.txt").write_text("   ")
    db_path = tmp_path / "store" / "context.db"
    db_path.parent.mkdir(parents=True)

    submit._extract_new_inbox_attachments(str(tmp_path), "proj", db_path)

    assert calls == []
    ledger = json.loads((db_path.parent / "extracted_inbox.json").read_text())
    assert "empty.txt" in ledger  # never retried


def test_no_inbox_is_noop(tmp_path):
    db_path = tmp_path / "store" / "context.db"
    db_path.parent.mkdir(parents=True)
    # Must not raise when there's no inbox directory.
    submit._extract_new_inbox_attachments(str(tmp_path), "proj", db_path)
    assert not (db_path.parent / "extracted_inbox.json").exists()


def test_telegram_inbox_also_scanned(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(submit, "_spawn_extraction", lambda *a, **k: calls.append((a, k)))

    inbox = tmp_path / ".claude" / "telegram" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "tg-1.txt").write_text("A long Telegram message worth remembering.")
    db_path = tmp_path / "store" / "context.db"
    db_path.parent.mkdir(parents=True)

    submit._extract_new_inbox_attachments(str(tmp_path), "proj", db_path)
    assert len(calls) == 1
    assert calls[0][1].get("source") == "tg-1.txt"
