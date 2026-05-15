"""Deck generation package — content selection, chart rendering, Slides I/O,
and slide-template builders for the gantt deck command.

The cmd_deck handler in gantt_lib/deck_cmds.py composes:
  data       → which tasks belong on which slide
  charts     → matplotlib gantt-zoom PNG bytes
  templates  → Slides API request dicts for each slide template
  slides_io  → Slides + Drive API wrappers (bootstrap, image upload, batchUpdate)

All slide selection rules are pure-logic (no Sheets I/O); all Slides/Drive
calls are routed through slides_io for testability.
"""
