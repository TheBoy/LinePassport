"""Small internal utilities shared across modules."""

from __future__ import annotations

import re
import sys
import traceback
from typing import Any

# A LINE mid is a one-letter kind prefix (u=user, c=chat/group, r=room)
# followed by an id body.  Depending on account type the body is either 32 hex
# characters (classic, e.g. ``u0123456789abcdef0123456789abcdef``) or a longer
# base64url token (e.g. ``UKquB15TbE4F0LQMn9nKQJZaTsGBkH_YaXvpBrQKsE7c``).
# Both forms are matched here.
_MID_RE = re.compile(r"^[ucr][A-Za-z0-9_-]{19,}$", re.IGNORECASE)


def is_mid(value: str | None) -> bool:
    """True if ``value`` looks like a raw LINE mid: a u/c/r kind prefix plus a
    20+ character base64url id body.

    Shared by every UI surface so a plausible display name is never mistaken
    for a mid (and vice versa): real display names contain spaces or other
    characters outside ``[A-Za-z0-9_-]`` and therefore never match.
    """
    return value is not None and _MID_RE.match(value) is not None


def reconfigure_stdout_utf8(stream: Any = None) -> None:
    """Best-effort switch a text stream to UTF-8 so box glyphs / Thai / emoji
    print on any OS code page.  A no-op if the stream cannot be reconfigured."""
    stream = sys.stdout if stream is None else stream
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8")
    except Exception:
        pass


def print_exc_quietly() -> None:
    """traceback.print_exc() for a fallback that must not fail itself: writing to
    stderr fails too when it is a file on a full disk, or a pipe whose reader has
    gone. (A pipe that nobody drains does not fail: a write to it waits, as any write
    to stderr then does.)"""
    try:
        traceback.print_exc()
    except Exception:
        pass


def print_quietly(message: str, *, stdout: bool = False) -> None:
    """A line for the server log (stderr, or stdout) that must not fail (see
    print_exc_quietly)."""
    try:
        print(message, file=sys.stdout if stdout else sys.stderr, flush=True)
    except Exception:
        pass


# PostgreSQL error classes (SQLSTATE) that say the database cannot take writes now:
# connection (08), a read-only database (25006), no rights to write (42501, as when a
# provider takes them away over a quota), out of resources such as a full disk (53),
# shut down or restarting (57) and system errors (58).
_UNAVAILABLE_SQLSTATES = ("08", "25006", "42501", "53", "57", "58")


def storage_unavailable(exc: BaseException) -> bool:
    """Whether ``exc`` says the place data is kept in cannot be used now (a file that
    cannot be written, a database that is down, refused the connection or takes no
    writes), so the same write can work later, rather than that this data itself was
    refused (say, a NUL byte PostgreSQL does not store)."""
    if isinstance(exc, OSError):
        return True
    if any(
        cls.__name__ in {"OperationalError", "InterfaceError"} for cls in type(exc).__mro__
    ):
        return True  # sqlite3 and psycopg: a database that is down, locked or read-only
    sqlstate = str(getattr(exc, "sqlstate", "") or "")
    return bool(sqlstate) and sqlstate.startswith(_UNAVAILABLE_SQLSTATES)
