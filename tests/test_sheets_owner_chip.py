"""Tests for the person-chip email extraction in sheets.parse_owner_chip_emails.

Person chips are a Google Sheets feature: when the user types @name and
picks a Google contact, the cell renders a chip with the contact's display
name but stores the canonical email in `chipRuns[].chip.personProperties.email`.
The lower-level spreadsheets.get API returns that metadata when you ask for it
via a `fields` projection; gspread's high-level get_values strips it.

These tests cover the pure parser — they don't mock the network call.
"""
from __future__ import annotations

import pytest

from gantt_lib.sheets import parse_owner_chip_emails


def _resp_for_rows(rows: list[dict]) -> dict:
    """Build a fake spreadsheets.get response shape from a list of row
    descriptors. Each row dict is the per-row `rowData` entry."""
    return {"sheets": [{"data": [{"rowData": rows}]}]}


def _chip_cell(email: str | None, display_name: str = "") -> dict:
    """A single-cell row with one person chip."""
    person: dict = {}
    if email is not None:
        person["email"] = email
    if display_name:
        person["displayName"] = display_name
    chip = {"chip": {"personProperties": person}} if person else {"chip": {}}
    return {"values": [{"chipRuns": [chip], "formattedValue": display_name}]}


def _plain_cell(text: str) -> dict:
    return {"values": [{"formattedValue": text}]}


def _empty_cell() -> dict:
    return {"values": []}


# --- happy path ---------------------------------------------------------------


def test_parse_chip_email_basic():
    """One row with a person chip → {first_data_row: email}."""
    resp = _resp_for_rows([_chip_cell("alex@example.com", "Alex Lee")])
    assert parse_owner_chip_emails(resp, first_data_row=5) == {5: "alex@example.com"}


def test_parse_chip_email_multiple_rows():
    """Mix of chip, plain text, and empty cells → only chip rows appear."""
    resp = _resp_for_rows([
        _chip_cell("alex@example.com", "Alex Lee"),
        _plain_cell("Jon"),                          # plain text → skip
        _chip_cell("jane@example.com", "Jane Park"),
        _empty_cell(),                               # empty → skip
        _chip_cell("bob@example.com", "Bob Kim"),
    ])
    assert parse_owner_chip_emails(resp, first_data_row=5) == {
        5: "alex@example.com",
        7: "jane@example.com",
        9: "bob@example.com",
    }


def test_parse_chip_email_respects_first_data_row():
    """Row offsets are relative to the first_data_row argument so the
    helper composes with whatever range was requested."""
    resp = _resp_for_rows([_chip_cell("alex@example.com")])
    assert parse_owner_chip_emails(resp, first_data_row=10) == {10: "alex@example.com"}


def test_parse_chip_first_chip_wins_when_multiple():
    """If a cell somehow has multiple chips, take the first (conventional
    'owner' position). Edge case — person chips usually take whole cell."""
    resp = _resp_for_rows([{"values": [{
        "chipRuns": [
            {"chip": {"personProperties": {"email": "first@example.com"}}},
            {"chip": {"personProperties": {"email": "second@example.com"}}},
        ],
    }]}])
    assert parse_owner_chip_emails(resp, first_data_row=5) == {5: "first@example.com"}


# --- skip cases ---------------------------------------------------------------


def test_parse_chip_skips_when_chip_has_no_email():
    """A chip with personProperties but no email field (rare — Google
    contact without an associated email) → skip the row."""
    resp = _resp_for_rows([{"values": [{
        "chipRuns": [{"chip": {"personProperties": {"displayName": "Anon"}}}],
    }]}])
    assert parse_owner_chip_emails(resp, first_data_row=5) == {}


def test_parse_chip_skips_when_email_is_empty_string():
    resp = _resp_for_rows([_chip_cell("")])
    assert parse_owner_chip_emails(resp, first_data_row=5) == {}


def test_parse_chip_skips_non_person_chips():
    """Non-person chip types (date chip, file chip, finance chip, etc.)
    have no personProperties → skip."""
    resp = _resp_for_rows([{"values": [{
        "chipRuns": [{"chip": {"dateTimeProperties": {"value": "2026-05-19"}}}],
    }]}])
    assert parse_owner_chip_emails(resp, first_data_row=5) == {}


def test_parse_chip_strips_whitespace():
    """Email surrounded by whitespace (shouldn't happen from the API,
    but defensive) → stripped."""
    resp = _resp_for_rows([_chip_cell("  alex@example.com  ", "Alex Lee")])
    assert parse_owner_chip_emails(resp, first_data_row=5) == {5: "alex@example.com"}


# --- defensive / structural cases --------------------------------------------


def test_parse_chip_empty_response_returns_empty_dict():
    assert parse_owner_chip_emails({}, first_data_row=5) == {}


def test_parse_chip_missing_data_blocks_returns_empty_dict():
    assert parse_owner_chip_emails({"sheets": [{}]}, first_data_row=5) == {}


def test_parse_chip_missing_row_data_returns_empty_dict():
    assert parse_owner_chip_emails(
        {"sheets": [{"data": [{}]}]}, first_data_row=5
    ) == {}


def test_parse_chip_row_with_empty_values_array_skipped():
    """Empty `values: []` (no cells in row) → skip without crashing."""
    resp = _resp_for_rows([_empty_cell(), _chip_cell("alex@example.com")])
    assert parse_owner_chip_emails(resp, first_data_row=5) == {6: "alex@example.com"}
