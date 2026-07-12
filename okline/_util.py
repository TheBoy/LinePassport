"""Small internal utilities shared across modules."""

from __future__ import annotations

import re
import sys
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
