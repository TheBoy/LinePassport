"""Builders for the request structs that callers construct most often.

The gateway accepts plain JSON objects with the Thrift field *names*, so these
helpers simply return ``dict`` payloads.  The :class:`Message` builders mirror
the message-construction helpers in ``static/js/main.js``::

    base = {from, to, toType, id, createdTime, sessionId:0}
    text = {...base, text, contentType: NONE, contentMetadata, hasContent:false}
    sticker = {...base, contentType: STICKER,
               contentMetadata: {STKID, STKPKGID, STKVER}}
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional

from .enums import ContentType, MIDType


def mid_to_type(mid: str) -> int:
    """Infer ``toType`` from a mid prefix (u=user, r=room, c=group/chat)."""
    if not mid:
        return int(MIDType.USER)
    head = mid[0]
    if head == "c":
        return int(MIDType.GROUP)
    if head == "r":
        return int(MIDType.ROOM)
    if head == "s":
        return int(MIDType.SQUARE_CHAT)
    return int(MIDType.USER)


def now_ms() -> int:
    return int(time.time() * 1000)


class Message:
    """Factory methods returning the ``Message`` dict for ``sendMessage``.

    Only ``to``, ``contentType`` (and ``text`` / ``contentMetadata``) are
    required on the wire; the rest are accepted for parity with the client and
    are harmless if present.
    """

    @staticmethod
    def _base(to: str, **extra: Any) -> dict:
        msg = {
            "to": to,
            "toType": mid_to_type(to),
            "contentMetadata": {},
            "sessionId": 0,
        }
        msg.update(extra)
        return msg

    @classmethod
    def text(cls, to: str, text: str, *,
             content_metadata: Optional[Mapping[str, Any]] = None,
             related_message_id: Optional[str] = None,
             message_relation_type: Optional[int] = None,
             **extra: Any) -> dict:
        msg = cls._base(to, text=text, contentType=int(ContentType.NONE), **extra)
        if content_metadata:
            msg["contentMetadata"] = dict(content_metadata)
        if related_message_id is not None:
            msg["relatedMessageId"] = related_message_id
            msg["messageRelationType"] = message_relation_type
            msg["relatedMessageServiceCode"] = 1
        return msg

    @classmethod
    def sticker(cls, to: str, package_id: str, sticker_id: str,
                version: int = 1, *, sticker_text: str = "", **extra: Any) -> dict:
        meta = {
            "STKID": str(sticker_id),
            "STKPKGID": str(package_id),
            "STKVER": str(version),
        }
        if sticker_text:
            meta["STKTXT"] = sticker_text
        return cls._base(to, text="", contentType=int(ContentType.STICKER),
                         contentMetadata=meta, **extra)

    @classmethod
    def location(cls, to: str, latitude: float, longitude: float, *,
                 title: str = "", address: str = "", **extra: Any) -> dict:
        msg = cls._base(to, text="", contentType=int(ContentType.LOCATION), **extra)
        msg["location"] = {
            "title": title,
            "address": address,
            "latitude": latitude,
            "longitude": longitude,
            "phone": extra.get("phone"),
        }
        return msg

    @classmethod
    def contact(cls, to: str, contact_mid: str, display_name: str = "",
                **extra: Any) -> dict:
        meta = {"mid": contact_mid, "displayName": display_name}
        return cls._base(to, text="", contentType=int(ContentType.CONTACT),
                         contentMetadata=meta, **extra)

    @classmethod
    def flex(cls, to: str, alt_text: str, contents: Mapping[str, Any],
             **extra: Any) -> dict:
        import json
        meta = {
            "FLEX_JSON": json.dumps(contents, ensure_ascii=False),
            "ALT_TEXT": alt_text,
        }
        return cls._base(to, text="", contentType=int(ContentType.FLEX),
                         contentMetadata=meta, **extra)

    @classmethod
    def media_ref(cls, to: str, content_type: int, object_id: str, *,
                  service: str = "talk", content_metadata: Optional[dict] = None,
                  **extra: Any) -> dict:
        """A media message that references an already-uploaded OBS object."""
        meta = dict(content_metadata or {})
        return cls._base(to, text="", contentType=int(content_type),
                         contentMetadata=meta, **extra)
