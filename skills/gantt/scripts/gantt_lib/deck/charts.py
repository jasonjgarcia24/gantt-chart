"""Matplotlib gantt-zoom rendering — returns PNG bytes for Slides embedding.

Lazy import of matplotlib so deck commands that don't render a chart
(strategic templates are all tables/bullets) don't pay the ~1-2s first-import
font-cache cost.
"""
from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO
from typing import Iterable, Optional

from ..model import Task

# Color palette mirrors the workbook _Config seed so the deck visually
# echoes the sheet a viewer might cross-reference.
_CP_COLOR = "#DB4437"           # red — At Risk in workbook
_NON_CP_COLOR = "#4285F4"       # blue — In Progress
_MILESTONE_COLOR = "#0F9D58"    # green — Done


def render_gantt_zoom_png(
    tasks: list[Task],
    today: date,
    *,
    days: int = 30,
    critical_ids: Optional[Iterable[str]] = None,
    title: str = "",
) -> bytes:
    """Render a horizontal-bar gantt chart for `tasks` spanning today..today+days.

    Returns PNG bytes suitable for direct upload to Drive. Caller pre-filters
    tasks to those overlapping the window — this function renders whatever
    it's given.

    Critical-path tasks (id ∈ `critical_ids`) get a distinct color from
    non-CP tasks. Milestones (`t.milestone == True` OR `t.duration == 0`)
    render as diamonds anchored at their end date instead of bars.

    Tasks with missing start/end dates are silently skipped (rendering a bar
    needs both endpoints; partial data shouldn't crash the chart).
    """
    import matplotlib
    matplotlib.use("Agg")  # no GUI; required for headless PNG writes
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    cp_set = set(critical_ids or [])
    horizon = today + timedelta(days=days)

    if not tasks:
        fig, ax = plt.subplots(figsize=(10, 2))
        ax.text(
            0.5, 0.5, "No tasks scheduled in this window",
            ha="center", va="center", fontsize=14, color="#777",
        )
        ax.axis("off")
    else:
        height = max(2.0, 0.5 * len(tasks) + 1.0)
        fig, ax = plt.subplots(figsize=(10, height))

        n = len(tasks)
        for i, t in enumerate(tasks):
            y = n - 1 - i  # invert so first task draws at the top
            is_milestone = t.milestone or t.duration == 0
            on_cp = t.id in cp_set
            color = (
                _MILESTONE_COLOR if is_milestone
                else (_CP_COLOR if on_cp else _NON_CP_COLOR)
            )
            if is_milestone:
                anchor = t.end or t.start
                if anchor is None:
                    continue
                ax.scatter(
                    [anchor], [y], marker="D", s=140,
                    color=color, edgecolor="white", zorder=3,
                )
            else:
                if t.start is None or t.end is None:
                    continue
                width = (t.end - t.start).days + 1
                ax.barh(
                    y=y, width=width, left=t.start,
                    color=color, edgecolor="white",
                    linewidth=0.5, height=0.6,
                )

        ax.set_yticks(range(n))
        ax.set_yticklabels(
            [
                f"{t.id}: {t.name[:30]}{'…' if len(t.name) > 30 else ''}"
                for t in reversed(tasks)
            ],
            fontsize=10,
        )
        ax.set_ylim(-0.5, n - 0.5)

        ax.set_xlim(today - timedelta(days=1), horizon + timedelta(days=1))
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")

        # Today marker
        ax.axvline(today, color="#888", linestyle="--", linewidth=1, zorder=2)
        ax.text(
            today, n - 0.5, " today",
            color="#888", fontsize=9, ha="left", va="bottom",
        )

        ax.grid(True, axis="x", linestyle=":", linewidth=0.5, color="#ccc")
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)

    if title:
        fig.suptitle(title, fontsize=14, fontweight="bold")

    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
