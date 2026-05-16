"""LLM-generated narrative for deck sections.

Wraps the Anthropic SDK to produce 3-5 sentence executive summaries for the
summary slide that gets inserted after each section divider. Speaker notes
(per-content-slide narrative) are a follow-up (T4).

Design choices:
- Defaults to Haiku 4.5 for cost (~$0.02/section). Sonnet/Opus overrides via
  the `--model` CLI flag map through `MODEL_ALIASES`.
- Lazy SDK import so the rest of the deck pipeline doesn't require anthropic
  installed when `--no-narrative` is set.
- Failure-soft: any exception during a generate call returns None and prints
  a warning to stderr; cmd_deck treats None as "skip the summary slide" and
  the deck still renders.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Optional


# CLI shortname → model id. Adding entries here also expands the --model
# choices automatically (cmd_deck reads MODEL_ALIASES.keys()).
MODEL_ALIASES: dict[str, str] = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-7",
}

DEFAULT_MODEL = "haiku"


@dataclass(frozen=True)
class NarrativeConfig:
    enabled: bool = True
    model: str = DEFAULT_MODEL


class NarrativeClient:
    """Thin wrapper over the Anthropic SDK for deck-narrative generation.

    Caller passes a NarrativeConfig (or builds one from CLI args). The client
    lazy-imports `anthropic` on first generate call so an --no-narrative run
    doesn't require the SDK to be installed.

    `summary()` returns the text on success, or None on failure (missing key,
    network error, etc.) — caller decides whether to drop the slide or fall
    back to a placeholder.
    """

    def __init__(self, config: NarrativeConfig):
        self.config = config
        self._client: Any = None  # lazy-init
        self._call_count = 0

    @property
    def model_id(self) -> str:
        return MODEL_ALIASES.get(self.config.model, self.config.model)

    @property
    def call_count(self) -> int:
        return self._call_count

    def _ensure_client(self) -> Optional[Any]:
        if self._client is not None:
            return self._client
        try:
            from anthropic import Anthropic
        except ImportError:
            print(
                "gantt: narrative — `anthropic` SDK not installed; skipping. "
                "Run `pip install anthropic` or use --no-narrative.",
                file=sys.stderr,
            )
            return None
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "gantt: narrative — ANTHROPIC_API_KEY not set; skipping. "
                "Export the key or use --no-narrative.",
                file=sys.stderr,
            )
            return None
        self._client = Anthropic()  # SDK reads ANTHROPIC_API_KEY itself
        return self._client

    def summary(
        self, audience: str, scope: str, section_data: dict
    ) -> Optional[str]:
        """Generate the 3-5 sentence executive summary for a deck section.

        `audience` is "Tactical" or "Strategic"; `scope` is the program name
        or "Portfolio". `section_data` is a plain-dict snapshot of the
        section's content (lists of tasks/rows by slide) — small enough that
        JSON-dumping it as a single user-turn input is fine.
        """
        client = self._ensure_client()
        if client is None:
            return None

        prompt = _build_summary_prompt(audience, scope, section_data)
        try:
            response = client.messages.create(
                model=self.model_id,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            self._call_count += 1
            # Concatenate any text blocks (Haiku/Sonnet return a single text
            # block for plain prompts; defensive concat handles edge cases).
            parts = [
                b.text for b in response.content
                if getattr(b, "type", None) == "text"
            ]
            text = "".join(parts).strip()
            return text or None
        except Exception as e:
            print(
                f"gantt: narrative — Anthropic API call failed ({e}); "
                f"summary slide will be skipped.",
                file=sys.stderr,
            )
            return None


def _build_summary_prompt(
    audience: str, scope: str, section_data: dict,
) -> str:
    """Build the user-turn prompt for a section summary.

    Kept as a module-level function so it can be unit-tested without
    instantiating the client.
    """
    audience_hint = (
        "engineering ICs needing weekly tactical context"
        if audience.lower() == "tactical"
        else "engineering leadership / TPMs needing strategic portfolio context"
    )
    return f"""You are summarizing one section of a program-status deck.

Audience: {audience} — {audience_hint}
Scope: {scope}

The section contains the following structured data:
{json.dumps(section_data, indent=2, default=str)}

Write a 3-5 sentence executive summary that surfaces the most important
signals from this data. Lead with what changed or what needs attention.
Be specific (cite WBS ids, slip days, dates) — vague generalities are
worse than no summary. If the data is sparse or all on-track, say so
directly rather than padding. Do NOT bullet, do NOT add headers, do NOT
restate the audience or scope — just the prose summary."""
