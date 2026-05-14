---
name: gantt
description: Trigger the gantt skill to manage Jason's program plans in the portfolio workbook (recalc dates, shift tasks, view critical path, add/update tasks, etc.).
---

Use the `gantt` skill to handle the user's request. The user is asking about
their program plans in the "Jason — Program Portfolio" workbook — task
mutations, date cascade, critical path, status, or schema operations.

Invoke the CLI at `~/.local/bin/gantt` directly via your Bash tool. Surface
each verified result line (starting with `gantt:`, ending with ` ✓` or ` ✗`)
as-is at the top of your response. For mutations, two result lines will be
emitted (the mutation + the auto-recalc summary) — surface both.

If the user describes a task by name and the WBS id is ambiguous, ask once.
Never guess. Skill details live in `SKILL.md` at the project root.
