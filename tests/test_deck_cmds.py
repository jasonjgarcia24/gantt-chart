"""Handler tests for gantt_lib.deck_cmds.

Uses FakeSpreadsheet (Phase 1) for workbook reads + FakeSlidesService /
FakeDriveService (T7) for the Google API surface. Verifies the bootstrap
+ append paths, audience defaults, single-batchUpdate-per-section
invariant, and chart-image upload behavior.
"""
from __future__ import annotations

import argparse
from datetime import date

import pytest

from gantt_lib import deck_cmds, schema, sheets as gs_sheets
from gantt_lib.deck import slides_io
from gantt_lib.model import Status, Task
from tests.fixtures.fake_slides import FakeDriveService, FakeSlidesService
from tests.fixtures.fake_workbook import FakeSpreadsheet, FakeWorksheet


def _make_workbook(programs: dict[str, list[Task]] | None = None) -> FakeSpreadsheet:
    """Build a workbook with _Config + program tabs (no _Baselines for these tests)."""
    ss = FakeSpreadsheet()
    ss.add_existing_worksheet(FakeWorksheet("_Config", sheet_id=0))
    if programs:
        for name, tasks in programs.items():
            ws = FakeWorksheet(
                schema.program_tab_name(name), sheet_id=hash(name) & 0xFFFFFF,
            )
            for i, t in enumerate(tasks):
                ws.update(
                    range_name=f"A{schema.FIRST_TASK_ROW + i}",
                    values=[gs_sheets._indented_row(t)],
                )
            ss.add_existing_worksheet(ws)
    return ss


def _make_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        audience="tactical", program=None, all=False, all_programs=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Redirect slides_io.CONFIG_PATH to a tmp file."""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(slides_io, "CONFIG_PATH", cfg_path)
    return cfg_path


@pytest.fixture
def services():
    return FakeSlidesService(), FakeDriveService()


@pytest.fixture
def small_program():
    """A 2-task program with valid dates so cascade succeeds."""
    return [
        Task(id="1", level=1, name="Concept", duration=3,
             start=date(2026, 5, 11)),
        Task(id="2", level=1, name="Build", duration=5,
             predecessors="1FS"),
    ]


# ---------- helpers ----------

def _batch_update_calls(slides: FakeSlidesService) -> list[dict]:
    return [kw for op, kw in slides.calls if op == "presentations.batchUpdate"]


def _create_calls(slides: FakeSlidesService) -> list[dict]:
    return [kw for op, kw in slides.calls if op == "presentations.create"]


# ---------- bootstrap + append ----------

def test_first_run_bootstraps_yearly_file(tmp_config, services, small_program):
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    rc = deck_cmds.cmd_deck(
        _make_args(audience="tactical", program="TPM90"), ss, slides, drive,
    )
    assert rc == 0
    creates = _create_calls(slides)
    assert len(creates) == 1
    assert "Tactical Decks" in creates[0]["body"]["title"]
    rec = slides_io.get_deck_record("tactical", date.today().year)
    assert rec is not None
    assert "Tactical Decks" in rec["title"]


def test_second_run_reuses_existing_file_id(tmp_config, services, small_program):
    """Pre-seed config; verify cmd_deck doesn't call presentations.create."""
    slides_io.set_deck_record("tactical", date.today().year, {
        "file_id": "existing-pres", "url": "https://docs.google.com/presentation/d/existing-pres",
        "created_at": "2026-04-01",
        "title": f"Jason — Tactical Decks — {date.today().year}",
    })
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    deck_cmds.cmd_deck(
        _make_args(audience="tactical", program="TPM90"), ss, slides, drive,
    )
    assert _create_calls(slides) == []  # no fresh file
    batch_calls = _batch_update_calls(slides)
    assert batch_calls[0]["presentationId"] == "existing-pres"


def test_stale_config_file_id_raises_with_cleanup_hint(tmp_config, services, small_program):
    """If config has a file_id but Slides 404s, raise (don't silently re-bootstrap)."""
    slides_io.set_deck_record("tactical", date.today().year, {
        "file_id": "deleted-pres", "url": "https://docs.google.com/presentation/d/deleted-pres",
        "created_at": "2026-04-01",
        "title": f"Jason — Tactical Decks — {date.today().year}",
    })
    slides, drive = services
    slides.fail_get_with(Exception("404 Not Found"))
    ss = _make_workbook(programs={"TPM90": small_program})
    with pytest.raises(RuntimeError, match="not found in Drive"):
        deck_cmds.cmd_deck(
            _make_args(audience="tactical", program="TPM90"), ss, slides, drive,
        )


# ---------- tactical audience ----------

def test_tactical_with_program_writes_one_section(tmp_config, services, small_program):
    """--program=X: one batchUpdate, 6 createSlide requests (1 divider + 5 content)."""
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    deck_cmds.cmd_deck(
        _make_args(audience="tactical", program="TPM90"), ss, slides, drive,
    )
    batch_calls = _batch_update_calls(slides)
    assert len(batch_calls) == 1
    requests = batch_calls[0]["body"]["requests"]
    assert sum(1 for r in requests if "createSlide" in r) == 6


def test_tactical_all_fans_out_one_section_per_program(
    tmp_config, services, small_program,
):
    """--all: one batchUpdate per program (3 programs → 3 batchUpdate calls)."""
    slides, drive = services
    ss = _make_workbook(programs={
        "TPM90": small_program, "Q3Launch": small_program, "OK2DC": small_program,
    })
    deck_cmds.cmd_deck(_make_args(audience="tactical", all=True), ss, slides, drive)
    batch_calls = _batch_update_calls(slides)
    assert len(batch_calls) == 3


def test_tactical_uploads_one_image_per_section(
    tmp_config, services, small_program,
):
    """Each tactical section embeds the gantt-zoom PNG → 1 Drive create + 1 permissions.create."""
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    deck_cmds.cmd_deck(
        _make_args(audience="tactical", program="TPM90"), ss, slides, drive,
    )
    file_creates = [kw for op, kw in drive.calls if op == "files.create"]
    perm_creates = [kw for op, kw in drive.calls if op == "permissions.create"]
    assert len(file_creates) == 1
    assert file_creates[0]["body"]["name"].startswith("gantt-deck-image-")
    assert len(perm_creates) == 1
    assert perm_creates[0]["body"] == {"role": "reader", "type": "anyone"}


def test_tactical_requires_program_or_all(tmp_config, services, small_program):
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    with pytest.raises(SystemExit):
        deck_cmds.cmd_deck(_make_args(audience="tactical"), ss, slides, drive)


def test_tactical_missing_program_dies(tmp_config, services):
    slides, drive = services
    ss = _make_workbook(programs={})
    with pytest.raises(SystemExit):
        deck_cmds.cmd_deck(
            _make_args(audience="tactical", program="NoSuch"), ss, slides, drive,
        )


# ---------- strategic audience ----------

def test_strategic_default_is_portfolio(tmp_config, services, small_program):
    """No flag → portfolio rollup; divider id contains "Portfolio"."""
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program, "Q3Launch": small_program})
    deck_cmds.cmd_deck(_make_args(audience="strategic"), ss, slides, drive)
    batch_calls = _batch_update_calls(slides)
    assert len(batch_calls) == 1
    requests = batch_calls[0]["body"]["requests"]
    divider = next(r["createSlide"] for r in requests if "createSlide" in r)
    assert "Portfolio" in divider["objectId"]


def test_strategic_with_program_uses_program_as_scope(
    tmp_config, services, small_program,
):
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    deck_cmds.cmd_deck(
        _make_args(audience="strategic", program="TPM90"), ss, slides, drive,
    )
    requests = _batch_update_calls(slides)[0]["body"]["requests"]
    divider = next(r["createSlide"] for r in requests if "createSlide" in r)
    assert "TPM90" in divider["objectId"]
    assert "Portfolio" not in divider["objectId"]


def test_strategic_all_programs_explicit(tmp_config, services, small_program):
    """--all-programs explicitly == default."""
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    deck_cmds.cmd_deck(
        _make_args(audience="strategic", all_programs=True), ss, slides, drive,
    )
    requests = _batch_update_calls(slides)[0]["body"]["requests"]
    divider = next(r["createSlide"] for r in requests if "createSlide" in r)
    assert "Portfolio" in divider["objectId"]


def test_strategic_does_not_upload_images(tmp_config, services, small_program):
    """Strategic templates are all tables/bullets — no chart slide → no Drive uploads."""
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    deck_cmds.cmd_deck(_make_args(audience="strategic"), ss, slides, drive)
    file_creates = [kw for op, kw in drive.calls if op == "files.create"]
    assert file_creates == []


def test_strategic_empty_workbook_dies(tmp_config, services):
    slides, drive = services
    ss = _make_workbook(programs={})
    with pytest.raises(SystemExit):
        deck_cmds.cmd_deck(_make_args(audience="strategic"), ss, slides, drive)


# ---------- structural invariants ----------

def test_section_uses_single_batch_update_call(
    tmp_config, services, small_program,
):
    """Pin the atomicity invariant: one section = one batchUpdate API call.

    Same regression-protection pattern we added in baseline-clear after the
    quota incident — if this ever fails, someone's reintroduced N-call writes.
    """
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    deck_cmds.cmd_deck(
        _make_args(audience="tactical", program="TPM90"), ss, slides, drive,
    )
    assert len(_batch_update_calls(slides)) == 1


def test_result_line_url_includes_divider_anchor(
    tmp_config, services, small_program, capsys,
):
    """Result line should be `gantt: deck appended — <url>#slide=id.<divider_id> ✓`."""
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    deck_cmds.cmd_deck(
        _make_args(audience="tactical", program="TPM90"), ss, slides, drive,
    )
    out = capsys.readouterr().out
    assert "deck appended" in out
    assert "#slide=id.divider-tactical-TPM90" in out
    assert "✓" in out
