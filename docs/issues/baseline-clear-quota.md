# Issue: `gantt baseline clear` hits Sheets write quota on large programs

**Status:** Identified 2026-05-14, fixed same day.
**Discovered:** Live test on Tahoma program (~80 baseline rows for one snapshot
× 2 snapshots = ~160 rows). `gantt baseline clear --program=Tahoma --force`
exited 1 partway through with `gspread.exceptions.APIError: APIError: [429]:
Quota exceeded for quota metric 'Write requests' and limit 'Write requests
per minute per user' of service 'sheets.googleapis.com'`.

## Root cause

Phase 1's `gantt_lib/baseline_io.delete_baselines_for_programs(ss, programs)`
implementation deletes rows one-at-a-time:

```python
for r in reversed(rows_to_delete):
    ws.delete_rows(r)
```

Each `ws.delete_rows()` call is one Sheets API write request. The default
free-tier quota is **60 write requests per user per minute**, so deleting
~60+ rows in a tight loop trips it. Combined with the day's other writes
(snapshot inserts, program-tab edits), we burned through the quota.

The Phase 1 spec (`docs/specs/baseline-tracking.md`, R3 + the
delete_baselines_for_programs docstring) called this out explicitly:

> "For a small Phase 1 history (~thousands of rows), per-row delete is
> acceptable; if this becomes slow, switch to a single batched
> deleteDimension request."

It became slow. Switching now.

## Fix

Replace the per-row loop with a single `Spreadsheet.batch_update` call that
packs all deletions into one HTTP request:

```python
requests = [
    {
        "deleteDimension": {
            "range": {
                "sheetId": ws.id,
                "dimension": "ROWS",
                "startIndex": r - 1,
                "endIndex": r,
            },
        },
    }
    for r in sorted(rows_to_delete, reverse=True)
]
ss.batch_update({"requests": requests})
```

Quota cost: 1 write regardless of how many rows are deleted.

**Why descending order matters:** within a single `batchUpdate`, Sheets
processes the `requests` array sequentially. Deleting row 3 first shifts row
5 → row 4, breaking subsequent index references. Sorting descending keeps
indices stable across the batch.

**Why we don't collapse contiguous ranges:** baseline rows for one program
aren't generally contiguous (snapshots interleave across programs over time).
The marginal saving from coalescing isn't worth the complexity vs. the
already 1-call cost.

## Test impact

`FakeSpreadsheet.batch_update` previously just recorded the body; tests didn't
care about side effects there. Now it must honor `deleteDimension` so handler
tests still pass through the production code path. Updated.

Added regression test `test_clear_uses_single_batch_update_for_large_programs`
that asserts `len(ss.batch_updates) == 1` after clearing many rows — this is
the structural invariant that prevents quota regressions.

## Recovery for the affected workbook

The Tahoma `_Baselines` rows are now in a partial-delete state — the
descending-order delete had completed an unknown subset before the 429.
Next steps for the user:

1. Wait ~60s for quota to refill.
2. Re-run `gantt baseline clear --program=Tahoma --force` — the new code
   uses one API call so the partial-state will resolve cleanly.
3. (Optional) Snapshot fresh: `gantt baseline snapshot --program=Tahoma`.

## Quota awareness going forward

The 60/min limit applies to **all** Sheets write operations from the same
OAuth client, not just baseline ops. Other commands that batch-write today:

- `gantt recalc` — already uses batch_update for date/status writes (good)
- `gantt program new` — uses batch_update for tab creation requests (good)
- `gantt baseline snapshot` — uses `worksheet.append_rows()` which is
  one HTTP call regardless of row count (good)

The only outlier was `delete_baselines_for_programs`. Fixed.

## Future-proofing

If Phase 2 (deck generation) adds Drive image uploads that themselves do
many writes, consider:
- Coalescing all of a section's mutations into one batch_update
- Adding a quota-aware retry-with-backoff helper at the gspread call site
- Surfacing the 429 as a friendly "wait 60s and retry" message instead of a
  raw Python traceback (per `/gantt:init`'s output style guide)
