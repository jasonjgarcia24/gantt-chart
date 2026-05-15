"""Minimal fakes for the google-api-python-client Slides + Drive services.

Models the chained-call pattern (`service.presentations().create(body=...).execute()`)
just enough to exercise `gantt_lib.deck.slides_io` and `gantt_lib.deck_cmds`.
Every chained call is recorded on the parent service's `.calls` list so tests
can assert on what happened (which methods, with which kwargs).

Default behaviors are useful for the happy path. Tests can override per-method
handlers via `service._handlers[op] = lambda kw: ...` for failure-path testing.
"""
from __future__ import annotations

from typing import Any, Callable


class _Request:
    """Result of a chained method call. Records the call and supports .execute()."""

    def __init__(self, service: "_FakeService", op: str, kwargs: dict):
        self._service = service
        self._op = op
        self._kwargs = kwargs
        service.calls.append((op, kwargs))

    def execute(self) -> Any:
        handler = self._service._handlers.get(self._op)
        if handler is None:
            return {}
        return handler(self._kwargs)


class _FakeService:
    """Shared base — owns the calls list + handlers map."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._handlers: dict[str, Callable[[dict], Any]] = {}


# ---------- Slides ----------

class _PresentationsResource:
    def __init__(self, service: "FakeSlidesService"):
        self._service = service

    def create(self, body=None):
        return _Request(self._service, "presentations.create", {"body": body})

    def get(self, presentationId=None):
        return _Request(
            self._service, "presentations.get",
            {"presentationId": presentationId},
        )

    def batchUpdate(self, presentationId=None, body=None):
        return _Request(
            self._service, "presentations.batchUpdate",
            {"presentationId": presentationId, "body": body},
        )


class FakeSlidesService(_FakeService):
    """Slides v1 service mock supporting create / get / batchUpdate."""

    def __init__(self):
        super().__init__()
        self._next_id = 1

        def _create(kw):
            self._next_id += 1
            return {
                "presentationId": f"pres-{self._next_id}",
                "slides": [{"objectId": f"slide-cover-{self._next_id}"}],
            }

        self._handlers["presentations.create"] = _create
        # Default: get always succeeds with a minimal payload
        self._handlers["presentations.get"] = lambda kw: {
            "presentationId": kw["presentationId"],
        }
        # Default: batchUpdate returns an empty replies array
        self._handlers["presentations.batchUpdate"] = lambda kw: {"replies": []}

    def presentations(self):
        return _PresentationsResource(self)

    def fail_get_with(self, exc: Exception):
        """Make subsequent presentations.get() calls raise this exception."""
        def raiser(kw):
            raise exc
        self._handlers["presentations.get"] = raiser


# ---------- Drive ----------

class _FilesResource:
    def __init__(self, service: "FakeDriveService"):
        self._service = service

    def create(self, body=None, media_body=None, fields=None):
        return _Request(
            self._service, "files.create",
            {"body": body, "media_body": media_body, "fields": fields},
        )

    def delete(self, fileId=None):
        return _Request(self._service, "files.delete", {"fileId": fileId})

    def list(self, q=None, fields=None):
        return _Request(self._service, "files.list", {"q": q, "fields": fields})


class _PermissionsResource:
    def __init__(self, service: "FakeDriveService"):
        self._service = service

    def create(self, fileId=None, body=None):
        return _Request(
            self._service, "permissions.create",
            {"fileId": fileId, "body": body},
        )


class FakeDriveService(_FakeService):
    """Drive v3 service mock supporting files.create/delete/list + permissions.create."""

    def __init__(self):
        super().__init__()
        self._next_id = 1000

        def _files_create(kw):
            self._next_id += 1
            file_id = f"drive-file-{self._next_id}"
            return {
                "id": file_id,
                "webContentLink": f"https://drive.google.com/uc?id={file_id}",
            }

        self._handlers["files.create"] = _files_create
        self._handlers["permissions.create"] = lambda kw: {}
        self._handlers["files.delete"] = lambda kw: {}
        self._handlers["files.list"] = lambda kw: {"files": []}

    def files(self):
        return _FilesResource(self)

    def permissions(self):
        return _PermissionsResource(self)
