"""Lightweight .env loader for the gantt CLI.

Reads `KEY=value` lines from `~/.config/gantt/.env` (or `$GANTT_ENV_FILE` if
set) and populates `os.environ`. Does NOT overwrite values already in the
environment — shell-set vars take precedence, so a one-shot `KEY=... gantt …`
still wins over the file.

Deliberately minimal:
- No quoting/escaping rules — value is the literal text after `=`, stripped.
- No `export` prefix support (silently ignored if present).
- No variable interpolation.
- Comments (`#` at line start) and blank lines are skipped.
- File missing is fine (no-op).
- Parse errors warn to stderr and continue with the next line.

Called once from the gantt CLI's `main()` before subcommand dispatch.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


DEFAULT_ENV_PATH = Path.home() / ".config" / "gantt" / ".env"


def load_env_file(path: Optional[Path] = None) -> int:
    """Load `KEY=value` lines from `path` into `os.environ`.

    Returns the number of keys actually set (skipping any already present in
    the environment). If `path` is None, uses `$GANTT_ENV_FILE` then the
    DEFAULT_ENV_PATH. Returns 0 silently if the file doesn't exist.
    """
    if path is None:
        override = os.environ.get("GANTT_ENV_FILE")
        path = Path(override) if override else DEFAULT_ENV_PATH

    if not path.is_file():
        return 0

    count = 0
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Permit `export KEY=value` for shell-script compatibility.
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            print(
                f"gantt: {path}:{lineno}: skipping malformed line "
                f"(no `=` found)",
                file=sys.stderr,
            )
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Don't clobber a value the user already set in their shell.
        if key and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count
