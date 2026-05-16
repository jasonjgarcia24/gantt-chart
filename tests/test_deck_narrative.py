"""Tests for gantt_lib.deck.narrative.

Don't actually hit Anthropic — verify model-alias mapping, prompt shape, and
the graceful-skip behavior when the API key / SDK is missing.
"""
from __future__ import annotations

from gantt_lib.deck.narrative import (
    DEFAULT_MODEL,
    MODEL_ALIASES,
    NarrativeClient,
    NarrativeConfig,
    _build_summary_prompt,
)


def test_default_model_is_haiku():
    assert DEFAULT_MODEL == "haiku"
    assert MODEL_ALIASES["haiku"] == "claude-haiku-4-5"


def test_model_aliases_cover_three_tiers():
    assert "haiku" in MODEL_ALIASES
    assert "sonnet" in MODEL_ALIASES
    assert "opus" in MODEL_ALIASES


def test_model_id_resolves_alias():
    c = NarrativeClient(NarrativeConfig(model="haiku"))
    assert c.model_id == "claude-haiku-4-5"


def test_model_id_passthrough_for_unknown_alias():
    """If user passes an exact model id, use it as-is — don't force alias lookup."""
    c = NarrativeClient(NarrativeConfig(model="claude-opus-4-6"))
    assert c.model_id == "claude-opus-4-6"


def test_summary_returns_none_when_api_key_missing(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = NarrativeClient(NarrativeConfig())
    result = c.summary("Tactical", "TPM90", {})
    assert result is None
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY not set" in err
    assert c.call_count == 0  # no API call was attempted


def test_summary_prompt_includes_audience_and_scope():
    prompt = _build_summary_prompt("Tactical", "TPM90", {"tasks": []})
    assert "Tactical" in prompt
    assert "TPM90" in prompt
    assert "engineering ICs" in prompt  # tactical audience hint


def test_summary_prompt_strategic_uses_different_audience_hint():
    prompt = _build_summary_prompt("Strategic", "Portfolio", {})
    assert "engineering leadership" in prompt
