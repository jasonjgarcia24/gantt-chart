"""Slide template builders → Slides API request dicts.

Each helper returns the request list to create one slide of a specific shape
(table / image / bullets / divider). The two orchestrators —
`tactical_section_requests` and `strategic_section_requests` — compose them
into a full appendable section: 1 divider + 5 content slides per audience.

Object IDs combine a compact prefix (audience tag + scope + date) with a
per-call hex nonce. The prefix keeps the result-line URL anchor
(`#slide=id.div-<T|S>-<scope>-<YYYYMMDD>-<nonce>`) recognizable, and the
nonce guarantees uniqueness across same-day re-runs (the Slides
`batchUpdate` API rejects any objectId that already exists in the file).

Length budget: Slides API caps objectIds at 50 chars. The longest derived
suffix (e.g. `-2-milestones-2-table` on a paginated content slide, or
`-subtitle` on the divider) leaves ~28 chars for the prefix. The chosen
format with a 14-char scope cap and 4-char nonce keeps every derived id
inside that budget — see _id_safe_name.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from .data import (
    CPRow,
    ForwardMilestone,
    MilestoneSlipRow,
    PortfolioRow,
    RiskRow,
)
from ..model import Task

# Slides EMU geometry (English Metric Units): 914400 EMU = 1 inch.
EMU_PER_INCH = 914400
SLIDE_WIDTH = 10 * EMU_PER_INCH
SLIDE_HEIGHT = 5.625 * EMU_PER_INCH

# Audience accent colors for the divider slide (RGB 0-1 floats).
TACTICAL_BG = {"red": 0.95, "green": 0.95, "blue": 0.95}   # neutral grey
STRATEGIC_BG = {"red": 0.92, "green": 0.95, "blue": 1.0}    # light blue


# Max scope (program name) length when used inside a Slides objectId.
# Worst case is the per-program milestone slide ID (issue #6 design):
# `{audience}-{scope}-{ts}-{nonce}-2-ms-{program}-{N}-table` puts the scope
# AND a program name in the same id. With scope ≤ 10 → ≤ 50 chars (the cap).
# All current programs fit under 10 chars (Tahoma=6, TPM90=5, Q3Launch=8,
# OK2DC=5); longer names get truncated in objectIds only — full name still
# appears in slide titles and table cells.
MAX_SCOPE_IN_ID = 10


def _id_safe_name(name: str) -> str:
    """Truncate a program/scope name for use in Slides objectIds.

    Truncation is cosmetic — the full name still appears in slide titles
    and table cells. Slides API caps objectIds at 50 chars; long scope
    names blow that budget once nested suffixes are appended.
    """
    return name[:MAX_SCOPE_IN_ID]


# ---------- atomic helpers ----------

def _truncate(s: Optional[str], n: int = 30) -> str:
    if not s:
        return ""
    return s if len(s) <= n + 1 else s[:n] + "…"


def _date_str(d: Optional[date]) -> str:
    return d.isoformat() if d else "—"


def _slip_str(s: Optional[int]) -> str:
    if s is None:
        return "—"
    if s > 0:
        return f"+{s}d"
    return f"{s}d"


def _shape_props(slide_id: str, x: float, y: float, w: float, h: float) -> dict:
    """Common element-properties payload for createShape / createTable / createImage.

    All measurements in inches; converted to EMU here.
    """
    return {
        "pageObjectId": slide_id,
        "size": {
            "width": {"magnitude": w * EMU_PER_INCH, "unit": "EMU"},
            "height": {"magnitude": h * EMU_PER_INCH, "unit": "EMU"},
        },
        "transform": {
            "scaleX": 1, "scaleY": 1,
            "translateX": x * EMU_PER_INCH,
            "translateY": y * EMU_PER_INCH,
            "unit": "EMU",
        },
    }


def _title_textbox(slide_id: str, title: str) -> list[dict]:
    """Standard slide-title box at top of the slide."""
    title_id = f"{slide_id}-title"
    return [
        {"createShape": {
            "objectId": title_id,
            "shapeType": "TEXT_BOX",
            "elementProperties": _shape_props(slide_id, 0.5, 0.3, 9, 0.6),
        }},
        {"insertText": {"objectId": title_id, "text": title}},
        {"updateTextStyle": {
            "objectId": title_id,
            "style": {"fontSize": {"magnitude": 20, "unit": "PT"}, "bold": True},
            "textRange": {"type": "ALL"},
            "fields": "fontSize,bold",
        }},
    ]


# ---------- slide-template helpers ----------

def _create_divider_slide(
    slide_id: str, audience: str, scope: str, today: date, actor: str,
) -> list[dict]:
    """Section divider — big centered title + date subtitle, audience-colored bg."""
    bg = TACTICAL_BG if audience.lower() == "tactical" else STRATEGIC_BG
    title_text = f"{audience} · {scope} · {today.isoformat()}"
    subtitle_text = f"({actor})"
    title_id = f"{slide_id}-title"
    subtitle_id = f"{slide_id}-subtitle"
    return [
        {"createSlide": {
            "objectId": slide_id,
            "slideLayoutReference": {"predefinedLayout": "BLANK"},
        }},
        {"updatePageProperties": {
            "objectId": slide_id,
            "pageProperties": {
                "pageBackgroundFill": {
                    "solidFill": {"color": {"rgbColor": bg}},
                },
            },
            "fields": "pageBackgroundFill.solidFill.color",
        }},
        {"createShape": {
            "objectId": title_id,
            "shapeType": "TEXT_BOX",
            "elementProperties": _shape_props(slide_id, 1.0, 1.5, 8, 2),
        }},
        {"insertText": {"objectId": title_id, "text": title_text}},
        {"updateTextStyle": {
            "objectId": title_id,
            "style": {"fontSize": {"magnitude": 36, "unit": "PT"}, "bold": True},
            "textRange": {"type": "ALL"},
            "fields": "fontSize,bold",
        }},
        {"createShape": {
            "objectId": subtitle_id,
            "shapeType": "TEXT_BOX",
            "elementProperties": _shape_props(slide_id, 1.0, 4.0, 8, 0.5),
        }},
        {"insertText": {"objectId": subtitle_id, "text": subtitle_text}},
        {"updateTextStyle": {
            "objectId": subtitle_id,
            "style": {
                "fontSize": {"magnitude": 16, "unit": "PT"},
                "italic": True,
                "foregroundColor": {"opaqueColor": {"rgbColor": {
                    "red": 0.4, "green": 0.4, "blue": 0.4,
                }}},
            },
            "textRange": {"type": "ALL"},
            "fields": "fontSize,italic,foregroundColor",
        }},
    ]


# Max data rows per table slide before pagination kicks in. Slides API tables
# auto-grow row height to fit wrapped cell text, so a single 2-line wrap can
# push the table past the slide's 5.625" bottom edge. With a 4" table height
# and 8pt cell font, ~12 data rows + 1 header keeps each row at ~0.31",
# enough headroom for a 2-line wrap before overflow. Pair with TABLE_CELL_PT
# (smaller font = fewer wraps) for a comfortable margin.
MAX_TABLE_ROWS_PER_SLIDE = 12

# Cell font size in table slides. 8pt fits ~22 chars in a 1.8" column before
# wrapping; 9pt was ~16 chars and wrapped most milestone-style names.
TABLE_CELL_PT = 8


def _create_table_slide(
    slide_id: str, title: str, headers: list[str], rows: list[list[str]],
) -> list[dict]:
    """Title at top, table below. Paginates across slides when rows overflow.

    - Empty `rows`: renders one slide with a single "No items" placeholder row.
    - `len(rows) <= MAX_TABLE_ROWS_PER_SLIDE`: one slide, original `slide_id`
      and `title` preserved (singletons retain their deep-link contract).
    - Larger: splits into N slides. Title becomes `"<title> (page/total)"`;
      slide IDs become `"{slide_id}-1"`, `"{slide_id}-2"`, etc.
    """
    if not rows:
        rows = [["No items"] + [""] * (len(headers) - 1)]

    if len(rows) <= MAX_TABLE_ROWS_PER_SLIDE:
        return _build_one_table_slide(slide_id, title, headers, rows)

    chunks = [
        rows[i:i + MAX_TABLE_ROWS_PER_SLIDE]
        for i in range(0, len(rows), MAX_TABLE_ROWS_PER_SLIDE)
    ]
    total = len(chunks)
    requests: list[dict] = []
    for idx, chunk in enumerate(chunks, start=1):
        page_id = f"{slide_id}-{idx}"
        page_title = f"{title} ({idx}/{total})"
        requests.extend(_build_one_table_slide(page_id, page_title, headers, chunk))
    return requests


def _build_one_table_slide(
    slide_id: str, title: str, headers: list[str], rows: list[list[str]],
) -> list[dict]:
    """Build a single table slide; assumes rows already capped/chunked by caller."""
    n_rows = len(rows) + 1  # +1 for header
    n_cols = len(headers)
    table_id = f"{slide_id}-table"

    requests: list[dict] = [
        {"createSlide": {
            "objectId": slide_id,
            "slideLayoutReference": {"predefinedLayout": "BLANK"},
        }},
    ]
    requests.extend(_title_textbox(slide_id, title))
    requests.append({"createTable": {
        "objectId": table_id,
        "elementProperties": _shape_props(slide_id, 0.5, 1.0, 9, 4),
        "rows": n_rows,
        "columns": n_cols,
    }})

    # Header row: text + bold style per cell
    for c, h in enumerate(headers):
        requests.append({"insertText": {
            "objectId": table_id,
            "cellLocation": {"rowIndex": 0, "columnIndex": c},
            "text": str(h),
        }})
        requests.append({"updateTextStyle": {
            "objectId": table_id,
            "cellLocation": {"rowIndex": 0, "columnIndex": c},
            "style": {"bold": True, "fontSize": {"magnitude": 11, "unit": "PT"}},
            "textRange": {"type": "ALL"},
            "fields": "bold,fontSize",
        }})

    # Data rows: text + smaller font per cell
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            if c >= n_cols:
                break
            text = str(val) if val is not None and str(val) != "" else " "
            requests.append({"insertText": {
                "objectId": table_id,
                "cellLocation": {"rowIndex": r + 1, "columnIndex": c},
                "text": text,
            }})
            requests.append({"updateTextStyle": {
                "objectId": table_id,
                "cellLocation": {"rowIndex": r + 1, "columnIndex": c},
                "style": {"fontSize": {"magnitude": TABLE_CELL_PT, "unit": "PT"}},
                "textRange": {"type": "ALL"},
                "fields": "fontSize",
            }})
    return requests


def _create_summary_slide(
    slide_id: str, title: str, summary_text: str,
) -> list[dict]:
    """Title at top, narrative paragraph below — used for LLM-generated
    section summaries that sit between the divider and the content slides.

    Font is 12pt (vs the 8pt table cell font) since this is prose, not data,
    and there's no row-count pressure. Box fills most of the slide.
    """
    body_id = f"{slide_id}-body"
    requests: list[dict] = [
        {"createSlide": {
            "objectId": slide_id,
            "slideLayoutReference": {"predefinedLayout": "BLANK"},
        }},
    ]
    requests.extend(_title_textbox(slide_id, title))
    requests.extend([
        {"createShape": {
            "objectId": body_id,
            "shapeType": "TEXT_BOX",
            "elementProperties": _shape_props(slide_id, 0.5, 1.0, 9, 4.5),
        }},
        {"insertText": {"objectId": body_id, "text": summary_text}},
        {"updateTextStyle": {
            "objectId": body_id,
            "style": {"fontSize": {"magnitude": 12, "unit": "PT"}},
            "textRange": {"type": "ALL"},
            "fields": "fontSize",
        }},
    ])
    return requests


def _create_image_slide(
    slide_id: str, title: str, image_url: str,
) -> list[dict]:
    """Title at top, image filling most of the slide below."""
    image_id = f"{slide_id}-image"
    requests: list[dict] = [
        {"createSlide": {
            "objectId": slide_id,
            "slideLayoutReference": {"predefinedLayout": "BLANK"},
        }},
    ]
    requests.extend(_title_textbox(slide_id, title))
    requests.append({"createImage": {
        "objectId": image_id,
        "url": image_url,
        "elementProperties": _shape_props(slide_id, 0.5, 1.0, 9, 4.5),
    }})
    return requests


def _create_bullets_slide(
    slide_id: str, title: str, bullets: list[str],
) -> list[dict]:
    """Title at top, bulleted list below. Empty `bullets` → 'No items' fallback."""
    if not bullets:
        bullets = ["No items"]
    bullets_id = f"{slide_id}-bullets"
    bullet_text = "\n".join(bullets)

    requests: list[dict] = [
        {"createSlide": {
            "objectId": slide_id,
            "slideLayoutReference": {"predefinedLayout": "BLANK"},
        }},
    ]
    requests.extend(_title_textbox(slide_id, title))
    requests.extend([
        {"createShape": {
            "objectId": bullets_id,
            "shapeType": "TEXT_BOX",
            "elementProperties": _shape_props(slide_id, 0.5, 1.0, 9, 4.5),
        }},
        {"insertText": {"objectId": bullets_id, "text": bullet_text}},
        {"createParagraphBullets": {
            "objectId": bullets_id,
            "textRange": {"type": "ALL"},
            "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
        }},
    ])
    return requests


# ---------- section orchestrators ----------

def tactical_section_requests(
    program_name: str,
    today: date,
    actor: str,
    *,
    this_week_tasks: list[Task],
    blockers_data: list[tuple[Task, list[str]]],
    cp_due_tasks: list[Task],
    recent_tasks: list[Task],
    gantt_image_url: str,
    summary_text: Optional[str] = None,
) -> tuple[list[dict], str]:
    """Compose the full tactical section: divider + 5 content slides.

    Returns (request_list, divider_slide_id). The divider slide_id is used
    by the caller to anchor the result-line URL (`#slide=id.<divider_id>`).

    `gantt_image_url` should be a public Drive URL (set via the upload step
    in slides_io.upload_image_to_drive).
    """
    ts = today.strftime("%Y%m%d")
    nonce = uuid.uuid4().hex[:4]
    scope_id = _id_safe_name(program_name)
    divider_id = f"div-T-{scope_id}-{ts}-{nonce}"
    prefix = f"T-{scope_id}-{ts}-{nonce}"

    requests: list[dict] = []
    requests.extend(_create_divider_slide(
        divider_id, "Tactical", program_name, today, actor,
    ))

    if summary_text:
        requests.extend(_create_summary_slide(
            f"{prefix}-0-summary", "Section Summary", summary_text,
        ))

    requests.extend(_create_table_slide(
        f"{prefix}-1-week",
        "This Week + Next Week",
        ["WBS", "Name", "Owner", "Start", "End", "Status"],
        [
            [t.id, t.name, t.owner,
             _date_str(t.start), _date_str(t.end), t.status]
            for t in this_week_tasks
        ],
    ))

    requests.extend(_create_table_slide(
        f"{prefix}-2-blockers",
        "Blockers",
        ["WBS", "Name", "Owner", "Blocked By", "Status"],
        [
            [t.id, t.name, t.owner,
             ", ".join(open_preds), t.status]
            for t, open_preds in blockers_data
        ],
    ))

    requests.extend(_create_table_slide(
        f"{prefix}-3-cp",
        "Critical Path — Due Soon",
        ["WBS", "Name", "Owner", "End", "Days Until"],
        [
            [t.id, t.name, t.owner, _date_str(t.end),
             str((t.end - today).days) if t.end else "—"]
            for t in cp_due_tasks
        ],
    ))

    requests.extend(_create_table_slide(
        f"{prefix}-4-done",
        "Recently Completed",
        ["WBS", "Name", "Owner", "Completed", "Duration"],
        [
            [t.id, t.name, t.owner,
             _date_str(t.end), str(t.duration)]
            for t in recent_tasks
        ],
    ))

    requests.extend(_create_image_slide(
        f"{prefix}-5-gantt",
        "30-Day Gantt Zoom",
        gantt_image_url,
    ))

    return requests, divider_id


def strategic_section_requests(
    scope: str,
    today: date,
    actor: str,
    *,
    portfolio_rows: list[PortfolioRow],
    milestone_rows: list[MilestoneSlipRow],
    cp_rows: list[CPRow],
    risk_rows: list[RiskRow],
    forward_rows: list[ForwardMilestone],
    summary_text: Optional[str] = None,
) -> tuple[list[dict], str]:
    """Compose the full strategic section: divider + 5 content slides.

    `scope` is the program name when generated for a single program, or
    "Portfolio" for the all-programs default.
    """
    ts = today.strftime("%Y%m%d")
    nonce = uuid.uuid4().hex[:4]
    scope_id = _id_safe_name(scope)
    divider_id = f"div-S-{scope_id}-{ts}-{nonce}"
    prefix = f"S-{scope_id}-{ts}-{nonce}"

    requests: list[dict] = []
    requests.extend(_create_divider_slide(
        divider_id, "Strategic", scope, today, actor,
    ))

    if summary_text:
        requests.extend(_create_summary_slide(
            f"{prefix}-0-summary", "Section Summary", summary_text,
        ))

    # S1: Portfolio Status
    requests.extend(_create_table_slide(
        f"{prefix}-1-portfolio",
        "Portfolio Status",
        ["Program", "Status", "% Complete", "CP Length", "Last Baseline"],
        [
            [r.program, r.status, f"{r.percent_complete}%",
             f"{r.current_cp_days}d" if r.current_cp_days is not None else "—",
             r.last_baseline_date.isoformat() if r.last_baseline_date else "—"]
            for r in portfolio_rows
        ],
    ))

    # S2: Milestone Slip Summary — one slide per program (no Program column).
    # Milestone names render in full; long names wrap inside the cell. The
    # MAX_TABLE_ROWS_PER_SLIDE pagination cap still bounds vertical growth.
    program_names = [r.program for r in portfolio_rows] or [scope]
    by_program: dict[str, list[MilestoneSlipRow]] = {p: [] for p in program_names}
    for r in milestone_rows:
        by_program.setdefault(r.program, []).append(r)
    for p in program_names:
        p_rows = by_program.get(p, [])
        requests.extend(_create_table_slide(
            f"{prefix}-2-ms-{_id_safe_name(p)}",
            f"Milestone Slip Summary — {p}",
            ["Milestone", "Baseline Date", "Current Date", "Slip"],
            [
                [r.name,
                 _date_str(r.baseline_end), _date_str(r.current_end),
                 _slip_str(r.slip)]
                for r in p_rows
            ],
        ))

    # S3: Critical Path by Program
    requests.extend(_create_table_slide(
        f"{prefix}-3-cp",
        "Critical Path by Program",
        ["Program", "Baseline CP", "Current CP", "Δ", "# Tasks on CP"],
        [
            [r.program,
             f"{r.baseline_cp_days}d" if r.baseline_cp_days is not None else "—",
             f"{r.current_cp_days}d" if r.current_cp_days is not None else "—",
             _slip_str(r.delta) + (" ⚠" if r.delta and r.delta > 0 else ""),
             str(r.num_tasks_on_cp)]
            for r in cp_rows
        ],
    ))

    # S4: Top Risks
    requests.extend(_create_bullets_slide(
        f"{prefix}-4-risks",
        "Top Risks / Decisions Needed",
        [
            f"{r.program}/{r.wbs} {r.name} — {r.status} "
            f"(slip {_slip_str(r.slip_days)})"
            for r in risk_rows
        ],
    ))

    # S5: 30-Day Forward Look
    requests.extend(_create_table_slide(
        f"{prefix}-5-forward",
        "30-Day Forward Look",
        ["Program", "Milestone", "Date", "Days From Today"],
        [
            [r.program, _truncate(r.name), r.end.isoformat(),
             str(r.days_from_today)]
            for r in forward_rows
        ],
    ))

    return requests, divider_id
