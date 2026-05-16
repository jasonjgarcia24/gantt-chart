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
        no_narrative=True, model="haiku",  # default-off in tests: keep them deterministic
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
        "title": f"Tactical Decks — {date.today().year}",
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
        "title": f"Tactical Decks — {date.today().year}",
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
    assert "#slide=id.div-T-TPM90-" in out
    assert "✓" in out


def test_warns_when_targeted_program_has_no_baseline(
    tmp_config, services, small_program, capsys,
):
    """Issue #4: deck should warn (not refuse) when a targeted program has no baseline."""
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})  # no _Baselines tab
    deck_cmds.cmd_deck(
        _make_args(audience="strategic", program="TPM90"), ss, slides, drive,
    )
    err = capsys.readouterr().err
    assert "no active baseline for TPM90" in err
    assert "gantt baseline snapshot" in err


class _FakeNarrativeClient:
    """Stand-in for NarrativeClient — returns a canned summary, no API calls."""

    def __init__(self, model="haiku", text="canned section summary."):
        from gantt_lib.deck.narrative import NarrativeConfig
        self.config = NarrativeConfig(enabled=True, model=model)
        self._text = text
        self.call_count = 0

    def summary(self, audience, scope, section_data):
        self.call_count += 1
        return self._text


def test_narrative_enabled_inserts_summary_slide_after_divider(
    tmp_config, services, small_program, monkeypatch,
):
    """With narrative on, summary slide sits between divider and content slides."""
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    fake = _FakeNarrativeClient(text="here is the summary.")
    monkeypatch.setattr(
        deck_cmds, "_build_narrative_client", lambda args: fake,
    )
    deck_cmds.cmd_deck(
        _make_args(audience="tactical", program="TPM90", no_narrative=False),
        ss, slides, drive,
    )
    requests = _batch_update_calls(slides)[0]["body"]["requests"]
    create_slide_ids = [
        r["createSlide"]["objectId"] for r in requests if "createSlide" in r
    ]
    # Order: divider → summary → 5 content slides
    assert create_slide_ids[0].startswith("div-T-TPM90-")
    assert "-0-summary" in create_slide_ids[1]
    assert fake.call_count == 1


def test_no_narrative_flag_skips_llm_call(
    tmp_config, services, small_program, monkeypatch,
):
    """--no-narrative path: orchestrator gets no summary slide, no LLM call."""
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    fake = _FakeNarrativeClient()
    monkeypatch.setattr(
        deck_cmds, "_build_narrative_client", lambda args: None,
    )
    deck_cmds.cmd_deck(
        _make_args(audience="tactical", program="TPM90", no_narrative=True),
        ss, slides, drive,
    )
    requests = _batch_update_calls(slides)[0]["body"]["requests"]
    summary_slides = [
        r for r in requests
        if "createSlide" in r and "-0-summary" in r["createSlide"]["objectId"]
    ]
    assert summary_slides == []
    assert fake.call_count == 0


def test_narrative_failure_skips_summary_slide(
    tmp_config, services, small_program, monkeypatch, capsys,
):
    """When the LLM client returns None (failure), no summary slide is added."""
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})

    class _FailingClient(_FakeNarrativeClient):
        def summary(self, *a, **kw):
            self.call_count += 1
            return None

    fake = _FailingClient()
    monkeypatch.setattr(
        deck_cmds, "_build_narrative_client", lambda args: fake,
    )
    deck_cmds.cmd_deck(
        _make_args(audience="tactical", program="TPM90", no_narrative=False),
        ss, slides, drive,
    )
    requests = _batch_update_calls(slides)[0]["body"]["requests"]
    summary_slides = [
        r for r in requests
        if "createSlide" in r and "-0-summary" in r["createSlide"]["objectId"]
    ]
    assert summary_slides == []  # graceful degrade


def test_result_line_annotates_narrative_call_count(
    tmp_config, services, small_program, monkeypatch, capsys,
):
    """When narrative ran, the result line includes the call count and model."""
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    fake = _FakeNarrativeClient(model="haiku")
    monkeypatch.setattr(
        deck_cmds, "_build_narrative_client", lambda args: fake,
    )
    deck_cmds.cmd_deck(
        _make_args(audience="tactical", program="TPM90", no_narrative=False),
        ss, slides, drive,
    )
    out = capsys.readouterr().out
    assert "narrative: 1 call(s), haiku" in out


def test_no_warning_when_baseline_exists(
    tmp_config, services, small_program, capsys,
):
    """No warning when targeted program is baselined."""
    from gantt_lib.baseline import BASELINE_HEADERS, BaselineRow
    from gantt_lib.baseline_io import baseline_row_to_cells
    slides, drive = services
    ss = _make_workbook(programs={"TPM90": small_program})
    bws = FakeWorksheet("_Baselines", sheet_id=999)
    bws.update(range_name="A1", values=[BASELINE_HEADERS])
    bws.update(range_name="A2", values=[baseline_row_to_cells(BaselineRow(
        program="TPM90", wbs="1", task_name="Concept",
        snapshot_date=date(2026, 5, 11),
        baseline_start=date(2026, 5, 11), baseline_end=date(2026, 5, 13),
        baseline_duration=3,
    ))])
    ss.add_existing_worksheet(bws)
    deck_cmds.cmd_deck(
        _make_args(audience="strategic", program="TPM90"), ss, slides, drive,
    )
    err = capsys.readouterr().err
    assert "no active baseline" not in err
