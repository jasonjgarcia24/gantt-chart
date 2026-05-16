"""Top-level pytest conftest.

The `gantt_lib` package lives under `skills/gantt/scripts/` so the skill is
fully self-contained. Tests at the repo root still `import gantt_lib.*`
directly — this conftest prepends the package's parent directory to
`sys.path` so those imports resolve without making the tests skill-aware.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent / "skills" / "gantt" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
