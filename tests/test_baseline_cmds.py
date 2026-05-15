"""Handler tests for gantt_lib.baseline_cmds.

Uses tests.fixtures.fake_workbook to mock the Spreadsheet/Worksheet surface.
Covers refusal + rebaseline + zero-task + missing-program for snapshot,
filter combinations for show, and refusal + per-program scoping for clear.
"""
from __future__ import annotations

import argparse
from datetime import date

import pytest

from gantt_lib import baseline_cmds, schema, sheets as gs_sheets
from gantt_lib.baseline import BASELINE_HEADERS, BaselineRow
from gantt_lib.baseline_io import baseline_row_to_cells
from gantt_lib.model import Task
from tests.fixtures.fake_workbook import FakeSpreadsheet, FakeWorksheet


def _make_workbook(
    programs: dict[str, list[Task]] | None = None,
    baselines: list[BaselineRow] | None = None,
    holidays: list[date] | None = None,
) -> FakeSpreadsheet:
    """Build a FakeSpreadsheet with _Config + program tabs + optional _Baselines."""
    ss = FakeSpreadsheet()

    config = FakeWorksheet("_Config", sheet_id=0)
    if holidays:
        for i, h in enumerate(holidays, start=2):
            config.update(range_name=f"C{i}", values=[[h.isoformat()]])
    ss.add_existing_worksheet(config)

    if programs:
        for name, tasks in programs.items():
            ws = FakeWorksheet(schema.program_tab_name(name), sheet_id=hash(name) & 0xFFFFFF)
            for i, t in enumerate(tasks):
                ws.update(
                    range_name=f"A{schema.FIRST_TASK_ROW + i}",
                    values=[gs_sheets._indented_row(t)],
                )
            ss.add_existing_worksheet(ws)

    if baselines is not None:
        bws = FakeWorksheet("_Baselines", sheet_id=999)
        bws.update(range_name="A1", values=[BASELINE_HEADERS])
        for i, br in enumerate(baselines, start=2):
            bws.update(range_name=f"A{i}", values=[baseline_row_to_cells(br)])
        ss.add_existing_worksheet(bws)

    return ss


def _make_args(**overrides) -> argparse.Namespace:
    """argparse.Namespace with defaults for any baseline subcommand."""
    defaults = dict(
        program=None, all=False, all_programs=False,
        rebaseline=False, label="", actor=None,
        force=False,
        top=10, slipping_only=False, milestones_only=False,
        critical_path_only=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _make_baseline(**overrides) -> BaselineRow:
    defaults = dict(
        program="TPM90", wbs="1", task_name="x",
        snapshot_date=date(2026, 4, 1),
        baseline_start=date(2026, 4, 1), baseline_end=date(2026, 4, 1),
        baseline_duration=1,
    )
    defaults.update(overrides)
    return BaselineRow(**defaults)


def _tpm_rows_in(ss: FakeSpreadsheet, program: str) -> int:
    bws = ss.worksheet("_Baselines")
    return sum(1 for r in bws.rows[1:] if r and r[0] == program)


# ---------- snapshot ----------

def test_snapshot_writes_rows_for_single_program(capsys):
    tasks = [
        Task(id="1", level=1, name="Concept", duration=3,
             start=date(2026, 5, 11), end=date(2026, 5, 13)),
        Task(id="2", level=1, name="Build", duration=5, predecessors="1FS",
             start=date(2026, 5, 14), end=date(2026, 5, 20)),
    ]
    ss = _make_workbook(programs={"TPM90": tasks})

    rc = baseline_cmds.cmd_baseline_snapshot(_make_args(program="TPM90"), ss)

    assert rc == 0
    captured = capsys.readouterr()
    assert "baseline snapshot — TPM90, 2 tasks" in captured.out
    assert "✓" in captured.out
    assert _tpm_rows_in(ss, "TPM90") == 2


def test_snapshot_creates_baselines_tab_when_missing(capsys):
    tasks = [Task(id="1", level=1, name="x", duration=1,
                  start=date(2026, 5, 11), end=date(2026, 5, 11))]
    ss = _make_workbook(programs={"TPM90": tasks})  # no baselines tab
    with pytest.raises(Exception):
        ss.worksheet("_Baselines")  # confirm absent
    baseline_cmds.cmd_baseline_snapshot(_make_args(program="TPM90"), ss)
    bws = ss.worksheet("_Baselines")
    assert bws.rows[0][:3] == ["program", "wbs", "task_name"]


def test_snapshot_refuses_when_active_baseline_exists(capsys):
    """Spec'd refusal: exit 1, multiline ✗ on stderr, no rows added."""
    ss = _make_workbook(
        programs={"TPM90": [Task(id="1", level=1, name="x", duration=1,
                                  start=date(2026, 5, 11), end=date(2026, 5, 11))]},
        baselines=[_make_baseline(snapshot_date=date(2026, 4, 1))],
    )
    rc = baseline_cmds.cmd_baseline_snapshot(_make_args(program="TPM90"), ss)
    assert rc == 1
    err = capsys.readouterr().err
    assert "already exists for TPM90" in err
    assert "Use --rebaseline" in err
    assert "History is always preserved" in err
    assert _tpm_rows_in(ss, "TPM90") == 1  # unchanged


def test_snapshot_rebaseline_appends_fresh_rows(capsys):
    ss = _make_workbook(
        programs={"TPM90": [Task(id="1", level=1, name="x", duration=1,
                                  start=date(2026, 5, 11), end=date(2026, 5, 11))]},
        baselines=[_make_baseline(snapshot_date=date(2026, 4, 1))],
    )
    rc = baseline_cmds.cmd_baseline_snapshot(
        _make_args(program="TPM90", rebaseline=True), ss,
    )
    assert rc == 0
    assert "baseline rebaselined — TPM90" in capsys.readouterr().out
    assert _tpm_rows_in(ss, "TPM90") == 2  # 1 historical + 1 fresh


def test_snapshot_skips_zero_task_program(capsys):
    ss = _make_workbook(programs={"TPM90": []})
    rc = baseline_cmds.cmd_baseline_snapshot(_make_args(program="TPM90"), ss)
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 tasks (skipped)" in out
    assert "⚠" in out
    with pytest.raises(Exception):
        ss.worksheet("_Baselines")  # never created — no work to do


def test_snapshot_dies_on_missing_program(capsys):
    ss = _make_workbook(programs={"TPM90": []})
    with pytest.raises(SystemExit) as exc:
        baseline_cmds.cmd_baseline_snapshot(_make_args(program="NoSuch"), ss)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "NoSuch" in err
    assert "not found" in err


def test_snapshot_actor_defaults_to_user_env(capsys, monkeypatch):
    monkeypatch.setenv("USER", "alice")
    ss = _make_workbook(programs={"TPM90": [Task(id="1", level=1, name="x", duration=1,
                                                  start=date(2026, 5, 11), end=date(2026, 5, 11))]})
    baseline_cmds.cmd_baseline_snapshot(_make_args(program="TPM90"), ss)
    bws = ss.worksheet("_Baselines")
    assert bws.rows[1][9] == "alice"  # actor is col J (index 9)


def test_snapshot_actor_arg_overrides_env(capsys, monkeypatch):
    monkeypatch.setenv("USER", "alice")
    ss = _make_workbook(programs={"TPM90": [Task(id="1", level=1, name="x", duration=1,
                                                  start=date(2026, 5, 11), end=date(2026, 5, 11))]})
    baseline_cmds.cmd_baseline_snapshot(
        _make_args(program="TPM90", actor="bob"), ss,
    )
    bws = ss.worksheet("_Baselines")
    assert bws.rows[1][9] == "bob"


def test_snapshot_label_propagates_to_rows(capsys):
    ss = _make_workbook(programs={"TPM90": [Task(id="1", level=1, name="x", duration=1,
                                                  start=date(2026, 5, 11), end=date(2026, 5, 11))]})
    baseline_cmds.cmd_baseline_snapshot(
        _make_args(program="TPM90", label="Q2 plan freeze"), ss,
    )
    bws = ss.worksheet("_Baselines")
    assert bws.rows[1][8] == "Q2 plan freeze"  # label is col I (index 8)


def test_snapshot_all_iterates_all_programs(capsys):
    tasks_a = [Task(id="1", level=1, name="A", duration=1,
                    start=date(2026, 5, 11), end=date(2026, 5, 11))]
    tasks_b = [Task(id="1", level=1, name="B", duration=1,
                    start=date(2026, 5, 12), end=date(2026, 5, 12))]
    ss = _make_workbook(programs={"TPM90": tasks_a, "Q3Launch": tasks_b})
    rc = baseline_cmds.cmd_baseline_snapshot(_make_args(all=True), ss)
    assert rc == 0
    out = capsys.readouterr().out
    assert "baseline snapshot — TPM90" in out
    assert "baseline snapshot — Q3Launch" in out
    assert _tpm_rows_in(ss, "TPM90") == 1
    assert _tpm_rows_in(ss, "Q3Launch") == 1


# ---------- show ----------

def test_show_summary_block_when_no_baseline(capsys):
    tasks = [Task(id="1", level=1, name="x", duration=1,
                  start=date(2026, 5, 11), end=date(2026, 5, 11))]
    ss = _make_workbook(programs={"TPM90": tasks})
    rc = baseline_cmds.cmd_baseline_show(_make_args(program="TPM90"), ss)
    assert rc == 0
    out = capsys.readouterr().out
    assert "TPM90 — no baseline yet" in out
    assert "1 total" in out
    assert "no slip data" in out


def test_show_all_tracking_when_current_matches_baseline(capsys):
    tasks = [Task(id="1", level=1, name="x", duration=1,
                  start=date(2026, 5, 11), end=date(2026, 5, 11))]
    rows = [_make_baseline(
        wbs="1", baseline_start=date(2026, 5, 11), baseline_end=date(2026, 5, 11),
    )]
    ss = _make_workbook(programs={"TPM90": tasks}, baselines=rows)
    rc = baseline_cmds.cmd_baseline_show(_make_args(program="TPM90"), ss)
    assert rc == 0
    out = capsys.readouterr().out
    assert "All tasks tracking to baseline" in out
    assert "max slip +0d" in out


def test_show_top_n_filters_to_worst_slipper(capsys):
    """Three tasks; cascade pushes all current ends to May 11. Baselines vary,
    producing slips of 0 / +5 / +3. --top=1 → only the +5 task in detail.

    Note: we tune *baselines* (not current ends) because cascade recomputes
    current.end from start + duration. Setting Task(end=...) is ignored.
    """
    tasks = [
        Task(id="1", level=1, name="OnTime", duration=1, start=date(2026, 5, 11)),
        Task(id="2", level=1, name="WorstSlipper", duration=1, start=date(2026, 5, 11)),
        Task(id="3", level=1, name="MidSlipper", duration=1, start=date(2026, 5, 11)),
    ]
    rows = [
        _make_baseline(wbs="1", task_name="OnTime",
                       baseline_start=date(2026, 5, 11), baseline_end=date(2026, 5, 11)),
        _make_baseline(wbs="2", task_name="WorstSlipper",
                       baseline_start=date(2026, 5, 6), baseline_end=date(2026, 5, 6)),
        _make_baseline(wbs="3", task_name="MidSlipper",
                       baseline_start=date(2026, 5, 8), baseline_end=date(2026, 5, 8)),
    ]
    ss = _make_workbook(programs={"TPM90": tasks}, baselines=rows)
    baseline_cmds.cmd_baseline_show(_make_args(program="TPM90", top=1), ss)
    out = capsys.readouterr().out
    assert "WorstSlipper" in out
    assert "MidSlipper" not in out
    assert "max slip +5d" in out


def test_show_slipping_only_excludes_on_baseline(capsys):
    """One slipping (+5), one on-baseline. --slipping-only shows only slipping.

    Same cascade caveat as above — we tune the baseline_end, not current_end.
    """
    tasks = [
        Task(id="1", level=1, name="OnTime", duration=1, start=date(2026, 5, 11)),
        Task(id="2", level=1, name="Slipper", duration=1, start=date(2026, 5, 11)),
    ]
    rows = [
        _make_baseline(wbs="1", task_name="OnTime",
                       baseline_start=date(2026, 5, 11), baseline_end=date(2026, 5, 11)),
        _make_baseline(wbs="2", task_name="Slipper",
                       baseline_start=date(2026, 5, 6), baseline_end=date(2026, 5, 6)),
    ]
    ss = _make_workbook(programs={"TPM90": tasks}, baselines=rows)
    baseline_cmds.cmd_baseline_show(
        _make_args(program="TPM90", slipping_only=True), ss,
    )
    out = capsys.readouterr().out
    assert "Slipper" in out
    # OnTime is not in detail; check that the SLIP column for it doesn't appear.
    # Easier: look for the "OnTime" row marker.
    detail_section = out.split("Critical:")[1] if "Critical:" in out else out
    assert "OnTime" not in detail_section


def test_show_all_returns_every_baselined_task_sorted_by_wbs(capsys):
    tasks = [
        Task(id="2", level=1, name="B", duration=1,
             start=date(2026, 5, 11), end=date(2026, 5, 11)),
        Task(id="1", level=1, name="A", duration=1,
             start=date(2026, 5, 11), end=date(2026, 5, 11)),
    ]
    rows = [
        _make_baseline(wbs="2", task_name="B",
                       baseline_start=date(2026, 5, 11), baseline_end=date(2026, 5, 11)),
        _make_baseline(wbs="1", task_name="A",
                       baseline_start=date(2026, 5, 11), baseline_end=date(2026, 5, 11)),
    ]
    ss = _make_workbook(programs={"TPM90": tasks}, baselines=rows)
    baseline_cmds.cmd_baseline_show(_make_args(program="TPM90", all=True), ss)
    out = capsys.readouterr().out
    # Both tasks present; A before B in output (WBS order)
    a_pos = out.find(" A ")
    b_pos = out.find(" B ")
    assert a_pos > 0 and b_pos > 0
    assert a_pos < b_pos


def test_show_all_programs_summary_only(capsys):
    tasks_a = [Task(id="1", level=1, name="A", duration=1,
                    start=date(2026, 5, 11), end=date(2026, 5, 11))]
    tasks_b = [Task(id="1", level=1, name="B", duration=1,
                    start=date(2026, 5, 12), end=date(2026, 5, 12))]
    ss = _make_workbook(programs={"TPM90": tasks_a, "Q3Launch": tasks_b})
    rc = baseline_cmds.cmd_baseline_show(_make_args(all_programs=True), ss)
    assert rc == 0
    out = capsys.readouterr().out
    assert "TPM90" in out
    assert "Q3Launch" in out
    # Portfolio mode: no detail tables → no "wbs" column header
    assert "wbs " not in out


# ---------- clear ----------

def test_clear_refuses_without_force(capsys):
    rows = [_make_baseline(wbs="1")]
    ss = _make_workbook(programs={"TPM90": []}, baselines=rows)
    rc = baseline_cmds.cmd_baseline_clear(
        _make_args(program="TPM90", force=False), ss,
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "refused" in err
    assert "--force" in err
    assert "1 historical snapshot" in err
    assert _tpm_rows_in(ss, "TPM90") == 1  # untouched


def test_clear_force_removes_target_program_rows_only(capsys):
    rows = [
        _make_baseline(program="TPM90", wbs="1"),
        _make_baseline(program="Q3Launch", wbs="1"),
    ]
    ss = _make_workbook(
        programs={"TPM90": [], "Q3Launch": []},
        baselines=rows,
    )
    rc = baseline_cmds.cmd_baseline_clear(
        _make_args(program="TPM90", force=True), ss,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 historical snapshot removed" in out
    assert _tpm_rows_in(ss, "TPM90") == 0
    assert _tpm_rows_in(ss, "Q3Launch") == 1


def test_clear_no_programs_in_workbook(capsys):
    ss = _make_workbook()  # No programs at all
    rc = baseline_cmds.cmd_baseline_clear(_make_args(all=True, force=True), ss)
    assert rc == 0
    assert "no programs in workbook" in capsys.readouterr().out


def test_clear_force_with_all_programs_clears_everything(capsys):
    rows = [
        _make_baseline(program="TPM90", wbs="1"),
        _make_baseline(program="Q3Launch", wbs="1"),
    ]
    ss = _make_workbook(
        programs={"TPM90": [], "Q3Launch": []},
        baselines=rows,
    )
    baseline_cmds.cmd_baseline_clear(_make_args(all=True, force=True), ss)
    assert _tpm_rows_in(ss, "TPM90") == 0
    assert _tpm_rows_in(ss, "Q3Launch") == 0


def test_clear_uses_single_batch_update_for_large_programs(capsys):
    """Regression for docs/issues/baseline-clear-quota.md.

    Per-row delete_rows() was tripping the Sheets 60/min/user write quota on
    programs with many baseline rows. The fix packs all deletions into one
    batch_update — N row deletes → 1 quota unit. This test pins the structural
    invariant: regardless of how many rows match, exactly one batch_update fires.
    """
    rows = [_make_baseline(program="TPM90", wbs=str(i)) for i in range(1, 101)]
    ss = _make_workbook(programs={"TPM90": []}, baselines=rows)
    pre_batch_count = len(ss.batch_updates)
    rc = baseline_cmds.cmd_baseline_clear(
        _make_args(program="TPM90", force=True), ss,
    )
    assert rc == 0
    assert _tpm_rows_in(ss, "TPM90") == 0  # all 100 deleted
    assert len(ss.batch_updates) - pre_batch_count == 1, (
        f"Expected exactly 1 batch_update for 100 row deletes, "
        f"got {len(ss.batch_updates) - pre_batch_count}"
    )


# ---------- malformed-row tolerance via the integration boundary ----------

def test_show_skips_malformed_baseline_row_with_warning(capsys):
    """A hand-edited bad date in _Baselines → warning to stderr, valid rows still read."""
    tasks = [Task(id="1", level=1, name="x", duration=1,
                  start=date(2026, 5, 11), end=date(2026, 5, 11))]
    ss = _make_workbook(programs={"TPM90": tasks}, baselines=[
        _make_baseline(wbs="1", baseline_start=date(2026, 5, 11),
                       baseline_end=date(2026, 5, 11)),
    ])
    # Inject a malformed row directly into the tab
    bws = ss.worksheet("_Baselines")
    bws.append_rows([["TPM90", "99", "Bad", "not-a-date", "", "", "0"]])

    baseline_cmds.cmd_baseline_show(_make_args(program="TPM90"), ss)
    captured = capsys.readouterr()
    assert "skipping malformed _Baselines row" in captured.err
    # Valid row still produced summary
    assert "max slip +0d" in captured.out
