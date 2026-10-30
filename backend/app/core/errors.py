"""Typed domain errors. `app.main` maps any DomainError to a JSON response in one place.

Status codes are part of the API contract exercised by tests/test_api.py:
  DomainError 400 (generic, user-correctable), ValidationFailed 422, ReportGateFailed 422,
  Unauthorized 401, NotFound 404, Conflict 409, MetaApiError 502.
"""
from __future__ import annotations


class DomainError(Exception):
    """Base class. `detail` is safe to show to the client."""

    status_code = 400

    def __init__(self, detail: str = ""):
        super().__init__(detail)
        self.detail = detail


class ValidationFailed(DomainError):
    """Input, image or image pair is unsuitable."""

    status_code = 422


class UnsupportedMeasurement(DomainError):
    """A measurement was requested that the available data cannot support (e.g. m2 without a georeference)."""

    status_code = 422


class ReportGateFailed(DomainError):
    """No report may be produced for this run (failed/unfinished run or missing evidence)."""

    status_code = 422


class Unauthorized(DomainError):
    status_code = 401


class NotFound(DomainError):
    status_code = 404


class Conflict(DomainError):
    status_code = 409


class MetaApiError(DomainError):
    """The Meta Model API failed or returned something we cannot interpret. `status` is the HTTP status, if any."""

    status_code = 502

    def __init__(self, detail: str = "", status: int | None = None):
        super().__init__(detail)
        self.status = status
