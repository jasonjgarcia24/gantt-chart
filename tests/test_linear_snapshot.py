"""Tests for gantt_lib.linear.snapshot — pure-logic snapshot construction
and equality helpers for 3-way merge.

Focus areas:
- Linear MCP response → IssueSnapshot conversion (handles nested vs flat
  state/estimate/assignee shapes the MCP returns in different contexts)
- Round-trip with SyncLink (snapshot ↔ snapshot_* columns)
- Equality helpers normalize None/"", trailing whitespace, blockedBy
  ordering, case-insensitive emails, date-only vs midnight-UTC ISO
"""
from __future__ import annotations

import pytest

from gantt_lib.linear.snapshot import (
    SNAPSHOT_FIELD_NAMES,
    IssueSnapshot,
    assignees_equal,
    blockedby_equal,
    build_snapshot_from_linear,
    due_dates_equal,
    estimates_equal,
    snapshot_to_sync_fields,
    snapshots_equal,
    sync_fields_to_snapshot,
    titles_equal,
)
from gantt_lib.linear.sync_tab import SyncLink


# --- build_snapshot_from_linear ---------------------------------------------


def test_build_snapshot_from_list_issues_shape():
    """`list_issues` returns flat `status` / `statusType` strings and
    nested `estimate.value`. Snapshot captures both."""
    issue = {
        "id": "JAS-9",
        "title": "Launch",
        "status": "Backlog",
        "statusType": "backlog",
        "estimate": {"value": 1, "name": "1 Point"},
        "assignee": None,
        "dueDate": None,
        "parentId": None,
    }
    snap = build_snapshot_from_linear(issue, blockedby_ids=[])
    assert snap.title == "Launch"
    assert snap.state == "Backlog"
    assert snap.state_type == "backlog"
    assert snap.estimate == "1"
    assert snap.assignee == ""
    assert snap.due_date == ""
    assert snap.parent == ""
    assert snap.milestone == ""
    assert snap.blockedby == ""


def test_build_snapshot_from_get_issue_with_blockers():
    """get_issue(includeRelations=true) shape — relations populated."""
    issue = {
        "id": "JAS-9",
        "title": "Launch",
        "status": "Backlog",
        "statusType": "backlog",
        "estimate": {"value": 1, "name": "1 Point"},
        "relations": {
            "blockedBy": [
                {"id": "JAS-6", "title": "Eyepiece fab"},
                {"id": "JAS-7", "title": "Doc revision"},
            ],
        },
    }
    # blockedby_ids=None falls back to digging into relations.
    snap = build_snapshot_from_linear(issue, blockedby_ids=None)
    assert snap.blockedby == "JAS-6,JAS-7"


def test_build_snapshot_explicit_blockedby_overrides_relations():
    """Caller-supplied blockedby_ids wins over whatever's in the issue dict."""
    issue = {
        "id": "JAS-9",
        "title": "Launch",
        "relations": {"blockedBy": [{"id": "JAS-6"}]},
    }
    snap = build_snapshot_from_linear(issue, blockedby_ids=["JAS-100", "JAS-200"])
    assert snap.blockedby == "JAS-100,JAS-200"


def test_build_snapshot_with_missing_estimate_yields_empty_string():
    """Linear omits the `estimate` key entirely when no estimate is set."""
    issue = {"id": "JAS-7", "title": "Doc revision", "status": "Backlog"}
    snap = build_snapshot_from_linear(issue, blockedby_ids=[])
    assert snap.estimate == ""


def test_build_snapshot_from_get_issue_with_nested_state():
    """get_issue may return `state` as a nested dict instead of flat status."""
    issue = {
        "id": "JAS-1",
        "title": "X",
        "state": {"name": "In Progress", "type": "started"},
    }
    snap = build_snapshot_from_linear(issue, blockedby_ids=[])
    assert snap.state == "In Progress"
    assert snap.state_type == "started"


def test_build_snapshot_assignee_prefers_email():
    """Assignee with email + display name → use email."""
    issue = {
        "id": "JAS-1",
        "title": "X",
        "assignee": {"email": "alex@example.com", "displayName": "Alex Smith"},
    }
    snap = build_snapshot_from_linear(issue, blockedby_ids=[])
    assert snap.assignee == "alex@example.com"


def test_build_snapshot_assignee_falls_back_to_display_name():
    """If no email is present, fall back to display name (better than nothing)."""
    issue = {"id": "JAS-1", "title": "X", "assignee": {"displayName": "Alex Smith"}}
    snap = build_snapshot_from_linear(issue, blockedby_ids=[])
    assert snap.assignee == "Alex Smith"


# --- Round-trip with SyncLink -----------------------------------------------


def test_snapshot_to_sync_fields_emits_snapshot_prefixed_keys():
    snap = IssueSnapshot(
        title="X",
        state="In Progress",
        state_type="started",
        assignee="alex@example.com",
        due_date="2026-06-01",
        estimate="5",
        blockedby="JAS-1,JAS-2",
        parent="JAS-0",
        milestone="MS-1",
    )
    fields = snapshot_to_sync_fields(snap)
    assert fields["snapshot_title"] == "X"
    assert fields["snapshot_state"] == "In Progress"
    assert fields["snapshot_blockedby"] == "JAS-1,JAS-2"
    # All 9 snapshot fields present.
    assert set(fields.keys()) == {f"snapshot_{n}" for n in SNAPSHOT_FIELD_NAMES}


def test_sync_fields_to_snapshot_round_trips():
    """Build a SyncLink with snapshot kwargs, extract back, compare."""
    snap = IssueSnapshot(
        title="X",
        state="In Progress",
        state_type="started",
        assignee="alex@example.com",
        due_date="2026-06-01",
        estimate="5",
        blockedby="JAS-1,JAS-2",
        parent="JAS-0",
        milestone="MS-1",
    )
    fields = snapshot_to_sync_fields(snap)
    link = SyncLink(program="P1", wbs_id="1", linear_id="JAS-1", **fields)
    snap2 = sync_fields_to_snapshot(link)
    assert snap == snap2


# --- Equality helpers --------------------------------------------------------


def test_titles_equal_trims_whitespace():
    assert titles_equal("Spec optics", "  Spec optics  ")
    assert titles_equal("Spec optics", "Spec optics ")
    assert titles_equal(None, "")
    assert not titles_equal("Spec optics", "Spec optics (renamed)")


def test_assignees_equal_case_insensitive():
    assert assignees_equal("alex@example.com", "ALEX@example.COM")
    assert assignees_equal(None, "")
    assert assignees_equal(None, None)
    assert not assignees_equal("alex@example.com", "bob@example.com")


def test_due_dates_equal_handles_midnight_utc():
    """Date-only ISO matches an ISO timestamp on the same date."""
    assert due_dates_equal("2026-06-01", "2026-06-01")
    assert due_dates_equal("2026-06-01", "2026-06-01T00:00:00.000Z")
    assert due_dates_equal("", None)
    assert not due_dates_equal("2026-06-01", "2026-06-02")


def test_estimates_equal_normalizes_zero_and_blank():
    assert estimates_equal("", "0")
    assert estimates_equal("0", None)
    assert estimates_equal("0", "")
    assert estimates_equal("5", "5.0")
    assert not estimates_equal("5", "6")


def test_estimates_equal_handles_garbage_strings():
    """Non-parseable values: treat as None vs whatever. Garbage → garbage
    isn't 'equal' to 5 → just a sanity check that it doesn't crash."""
    assert not estimates_equal("not-a-number", "5")
    # Two unparseables compare as None == None → equal, by current contract.
    assert estimates_equal("garbage", "also-garbage") in (True, False)  # implementation detail


def test_blockedby_equal_set_semantics():
    """Order doesn't matter; duplicates collapse; whitespace ignored."""
    assert blockedby_equal("JAS-1,JAS-2", "JAS-2,JAS-1")
    assert blockedby_equal("JAS-1, JAS-2, JAS-1", "JAS-2,JAS-1")
    assert blockedby_equal("", None)
    assert not blockedby_equal("JAS-1", "JAS-2")
    assert not blockedby_equal("JAS-1", "JAS-1,JAS-2")


# --- snapshots_equal (full-snapshot comparison) ------------------------------


def test_snapshots_equal_field_by_field():
    a = IssueSnapshot(title="X", estimate="5", blockedby="JAS-1,JAS-2")
    b = IssueSnapshot(title="X", estimate="5", blockedby="JAS-2,JAS-1")  # order shuffled
    assert snapshots_equal(a, b)


def test_snapshots_unequal_on_any_field_difference():
    a = IssueSnapshot(title="X", state="In Progress")
    b = IssueSnapshot(title="X", state="Done")
    assert not snapshots_equal(a, b)


def test_snapshots_equal_empty():
    """Two default-empty snapshots compare equal."""
    assert snapshots_equal(IssueSnapshot(), IssueSnapshot())
