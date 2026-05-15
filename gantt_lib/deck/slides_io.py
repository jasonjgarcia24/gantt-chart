"""Slides + Drive API wrappers — bootstrap yearly files, upload images, append sections.

All Slides + Drive calls funnel through the four public functions here so
handler tests can mock at the service-builder boundary (see
tests/fixtures/fake_slides.py). Config persistence (the `decks:` block in
~/.config/gantt/config.json) is also concentrated here — pure-file helpers
that tests can redirect to a tmp_path.
"""
from __future__ import annotations

import json
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Optional

# Mirror the script's CONFIG_PATH constant so both producers/consumers point at
# the same file. If this drifts, deck and non-deck commands would split-brain.
CONFIG_PATH = Path.home() / ".config/gantt/config.json"


# ---------- service factories ----------

def slides_service(creds):
    """Build a Slides v1 service object from authorized creds."""
    from googleapiclient.discovery import build
    return build("slides", "v1", credentials=creds, cache_discovery=False)


def drive_service(creds):
    """Build a Drive v3 service object from authorized creds."""
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ---------- config helpers ----------

def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())


def _write_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    CONFIG_PATH.chmod(0o600)


def get_deck_record(audience: str, year: int) -> Optional[dict]:
    """Return `{file_id, url, created_at, title}` for the audience-year, or None."""
    return _read_config().get("decks", {}).get(f"{audience}_{year}")


def set_deck_record(audience: str, year: int, record: dict) -> None:
    """Persist a deck record under config.decks.<audience>_<year>.

    Preserves all other top-level config keys (sheet_id, sheet_url, etc.) —
    only mutates the targeted decks entry.
    """
    cfg = _read_config()
    cfg.setdefault("decks", {})[f"{audience}_{year}"] = record
    _write_config(cfg)


# ---------- bootstrap / append ----------

def find_or_bootstrap_yearly_file(
    slides_svc, audience: str, year: int,
) -> tuple[str, str]:
    """Return (file_id, url) for the audience-year deck file, creating if missing.

    On bootstrap: Slides `presentations.create` mints a fresh file with one
    blank cover slide; the (file_id, url, title, created_at) are persisted to
    config.decks.<audience>_<year>. The cover-slide title text is the caller's
    responsibility to set in the first batchUpdate (typically as part of the
    section-append request list).

    Drive search for an existing file with the same name is intentionally
    skipped: the workbook OAuth uses `drive.file` scope, which only sees
    files this app created. A user-manually-created file with the same name
    is invisible to our queries; we'd create a duplicate. Acceptable —
    Drive uses file IDs, not names, for de-duplication.

    Raises RuntimeError if config has a file_id but the file no longer exists
    in Drive (likely user-deleted) — points at the config key for cleanup.
    """
    record = get_deck_record(audience, year)
    if record:
        try:
            slides_svc.presentations().get(
                presentationId=record["file_id"],
            ).execute()
            return record["file_id"], record["url"]
        except Exception:
            raise RuntimeError(
                f"deck file {record['file_id']!r} for {audience}_{year} "
                f"not found in Drive (file may have been deleted). Edit "
                f"~/.config/gantt/config.json to remove the "
                f"decks.{audience}_{year} key, then re-run to create a new file."
            )

    title = f"Jason — {audience.capitalize()} Decks — {year}"
    pres = slides_svc.presentations().create(body={"title": title}).execute()
    file_id = pres["presentationId"]
    url = f"https://docs.google.com/presentation/d/{file_id}"
    set_deck_record(audience, year, {
        "file_id": file_id,
        "url": url,
        "created_at": date.today().isoformat(),
        "title": title,
    })
    return file_id, url


def upload_image_to_drive(
    drive_svc, png_bytes: bytes, name: str,
) -> tuple[str, str]:
    """Upload PNG to Drive root, set public-link permissions, return (file_id, url).

    Slides `createImage` requests need a publicly-accessible URL. Setting
    permissions to `{role: reader, type: anyone}` grants link-based read
    access to anyone with the (random, unguessable) Drive URL — same privacy
    posture as a Sheets file shared "anyone with link, view-only". Document
    in the README so the user knows.

    No auto-cleanup. Files persist in Drive forever; the `gantt-deck-image-`
    prefix in `name` makes manual search-and-cleanup easy via the Drive UI.
    """
    from googleapiclient.http import MediaIoBaseUpload

    media = MediaIoBaseUpload(BytesIO(png_bytes), mimetype="image/png")
    file = drive_svc.files().create(
        body={"name": name},
        media_body=media,
        fields="id, webContentLink",
    ).execute()
    file_id = file["id"]

    drive_svc.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
    ).execute()

    return file_id, file.get("webContentLink", "")


def execute_section_append(
    slides_svc, file_id: str, requests: list[dict],
) -> dict:
    """Execute one Slides batchUpdate with the given requests. Returns the response.

    Single batchUpdate per section is the atomicity guarantee — either all
    slides land or none do (matches the same per-section atomicity rule we
    rely on for baseline snapshots in Phase 1).
    """
    return slides_svc.presentations().batchUpdate(
        presentationId=file_id,
        body={"requests": requests},
    ).execute()
