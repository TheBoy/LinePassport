"""Errors raised by the CRM layer.

The CRM package never imports ``okline.webapp``; the web handler turns a
``CrmError`` into the same JSON error shape it uses for its own ``WebError``.
"""

from __future__ import annotations


class CrmError(Exception):
    """A user-facing CRM failure with an HTTP status and a stable error code."""

    def __init__(self, status: int, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.status = int(status)
        self.message = message
        self.code = code or {
            400: "bad_request",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            429: "too_many_requests",
        }.get(self.status, "error")


def bad_request(message: str, code: str = "bad_request") -> CrmError:
    return CrmError(400, message, code)


def forbidden(message: str = "Permission denied.", code: str = "forbidden") -> CrmError:
    return CrmError(403, message, code)


def not_found(message: str, code: str = "not_found") -> CrmError:
    return CrmError(404, message, code)
