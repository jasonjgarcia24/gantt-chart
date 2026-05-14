"""Tests for gantt_lib.refs — predecessor reference scanning."""
from __future__ import annotations

from gantt_lib.model import Task
from gantt_lib.refs import referencing_tasks


def _t(id, predecessors=""):
    return Task(id=id, level=1, name=f"t{id}", predecessors=predecessors)


def test_no_references_returns_empty():
    tasks = [_t("1"), _t("2"), _t("3")]
    assert referencing_tasks(tasks, "1") == []


def test_single_reference_found():
    tasks = [_t("1"), _t("2", "1FS")]
    out = referencing_tasks(tasks, "1")
    assert len(out) == 1 and out[0].id == "2"


def test_multiple_references_found():
    tasks = [_t("1"), _t("2", "1FS"), _t("3", "1SS+2"), _t("4", "2FS")]
    out = referencing_tasks(tasks, "1")
    ids = sorted(t.id for t in out)
    assert ids == ["2", "3"]


def test_does_not_match_substring_ids():
    # "1" should not match a task referencing "1.1" or "11".
    tasks = [_t("1"), _t("1.1"), _t("11"), _t("2", "1.1FS"), _t("3", "11FS")]
    out = referencing_tasks(tasks, "1")
    assert out == []


def test_malformed_predecessors_do_not_crash():
    tasks = [_t("1"), _t("2", "garbage-not-valid"), _t("3", "1FS")]
    out = referencing_tasks(tasks, "1")
    assert [t.id for t in out] == ["3"]
