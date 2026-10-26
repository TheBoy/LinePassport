"""User-facing Telegram errors; the web handler turns them into JSON errors."""

from __future__ import annotations


class TelegramError(Exception):
    def __init__(self, status: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.message = message
        self.code = code
