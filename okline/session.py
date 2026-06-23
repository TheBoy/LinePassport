"""Session persistence — save / load credentials so you log in once.

>>> from okline import OkLine
>>> api = OkLine()
>>> api.auth.qr_login(on_qr=print)        # first time: scan the QR   # doctest: +SKIP
>>> api.save_tokens("session.json")        # remember the tokens       # doctest: +SKIP

>>> api = OkLine.from_tokens_file("session.json")   # next time: instant  # doctest: +SKIP
>>> api.get_profile()                                                     # doctest: +SKIP

When loaded from a file, OkLine **auto-saves** the file whenever the access
token is refreshed, so the session stays valid across runs.

> ⚠️ The session file contains live credentials — keep it private (it is matched
> by the project ``.gitignore``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class Session:
    """Persisted credentials."""

    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    certificate: Optional[str] = None
    mid: Optional[str] = None
    region_code: Optional[str] = None
    #: Exported E2EE keychain (``E2EEManager.export_keys()``) so Letter Sealing
    #: works across sessions without a fresh QR login.  Private-key material —
    #: keep the file secret.
    e2ee: Optional[Dict[str, Any]] = None

    # JSON uses the camelCase keys the rest of the ecosystem expects.
    def to_dict(self) -> dict:
        d = {
            "accessToken": self.access_token,
            "refreshToken": self.refresh_token,
            "certificate": self.certificate,
            "mid": self.mid,
            "regionCode": self.region_code,
        }
        if self.e2ee:
            d["e2ee"] = self.e2ee
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        d = d or {}
        return cls(
            access_token=d.get("accessToken") or d.get("access_token"),
            refresh_token=d.get("refreshToken") or d.get("refresh_token"),
            certificate=d.get("certificate"),
            mid=d.get("mid"),
            region_code=d.get("regionCode") or d.get("region_code"),
            e2ee=d.get("e2ee"),
        )

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "Session":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    @classmethod
    def from_tokens(cls, tokens) -> "Session":
        return cls(access_token=tokens.access_token,
                   refresh_token=tokens.refresh_token,
                   certificate=tokens.certificate, mid=tokens.mid)
