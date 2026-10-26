"""Customer records (CRM) for LinePassport: customers, chats, tags, notes, tasks, segments."""

from __future__ import annotations

from .db import CrmDatabase
from .errors import CrmError
from .service import CrmContext, CrmService, channel_family, chat_external_id, preview_text

__all__ = [
    "CrmContext",
    "CrmDatabase",
    "CrmError",
    "CrmService",
    "channel_family",
    "chat_external_id",
    "preview_text",
]
