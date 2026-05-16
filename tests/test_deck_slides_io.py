"""Pure-filesystem tests for gantt_lib.deck.slides_io config helpers.

Slides + Drive API integration tests live in tests/test_deck_cmds.py via
FakeSlidesService / FakeDriveService — easier to mock at the handler level
than to mock individual service-method chains here.
"""
from __future__ import annotations

import json

import pytest

from gantt_lib.deck import slides_io


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Redirect CONFIG_PATH to a tmp file for the test's duration."""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(slides_io, "CONFIG_PATH", cfg_path)
    return cfg_path


def test_get_deck_record_returns_none_when_config_missing(tmp_config):
    assert slides_io.get_deck_record("tactical", 2026) is None


def test_get_deck_record_returns_none_when_audience_year_absent(tmp_config):
    tmp_config.write_text(json.dumps({"sheet_id": "S1"}))
    assert slides_io.get_deck_record("tactical", 2026) is None


def test_set_deck_record_creates_decks_block(tmp_config):
    rec = {
        "file_id": "abc", "url": "https://docs.google.com/presentation/d/abc",
        "created_at": "2026-05-14",
        "title": "Tactical Decks — 2026",
    }
    slides_io.set_deck_record("tactical", 2026, rec)
    cfg = json.loads(tmp_config.read_text())
    assert cfg["decks"]["tactical_2026"] == rec


def test_set_then_get_round_trips(tmp_config):
    rec = {
        "file_id": "xyz", "url": "https://docs.google.com/presentation/d/xyz",
        "created_at": "2026-05-14",
        "title": "Strategic Decks — 2026",
    }
    slides_io.set_deck_record("strategic", 2026, rec)
    assert slides_io.get_deck_record("strategic", 2026) == rec


def test_set_deck_record_preserves_unrelated_config_keys(tmp_config):
    """Mutating the decks block must not clobber sheet_id / other top-level keys."""
    tmp_config.write_text(json.dumps({
        "sheet_id": "S1",
        "sheet_url": "https://docs.google.com/spreadsheets/d/S1",
        "title": "Program Portfolio",
    }))
    slides_io.set_deck_record("tactical", 2026, {
        "file_id": "abc", "url": "u", "created_at": "d", "title": "t",
    })
    cfg = json.loads(tmp_config.read_text())
    assert cfg["sheet_id"] == "S1"
    assert cfg["sheet_url"] == "https://docs.google.com/spreadsheets/d/S1"
    assert cfg["title"] == "Program Portfolio"
    assert "tactical_2026" in cfg["decks"]


def test_set_deck_record_writes_chmod_600(tmp_config):
    """Match the existing config.json permission pattern."""
    slides_io.set_deck_record("tactical", 2026, {
        "file_id": "abc", "url": "u", "created_at": "d", "title": "t",
    })
    mode = tmp_config.stat().st_mode & 0o777
    assert mode == 0o600


def test_set_deck_record_distinct_audience_year_keys_coexist(tmp_config):
    """tactical_2026 and strategic_2026 are independent slots."""
    slides_io.set_deck_record("tactical", 2026, {
        "file_id": "T", "url": "u1", "created_at": "d", "title": "t1",
    })
    slides_io.set_deck_record("strategic", 2026, {
        "file_id": "S", "url": "u2", "created_at": "d", "title": "t2",
    })
    assert slides_io.get_deck_record("tactical", 2026)["file_id"] == "T"
    assert slides_io.get_deck_record("strategic", 2026)["file_id"] == "S"
