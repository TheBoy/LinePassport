"""CRM business logic: customers, chats, tags, notes, follow-up tasks and segments.

The service owns the rules (who may do what, how chat status moves) and talks
to the rest of LinePassport only through ``CrmContext``, which ``WebState``
implements. That keeps this package free of any import from ``okline.webapp``.
"""

from __future__ import annotations

import csv
import io
import json
import time
import uuid
from typing import Any, Protocol

from .db import CrmDatabase
from .errors import CrmError, bad_request, forbidden, not_found
from .filters import DAY_MS, SORTS, STATUSES, customer_where, normalize_filter

DEFAULT_SETTINGS: dict[str, Any] = {"autoCloseDays": 3, "autoAssignOnReply": True}
TASK_STATUSES = ("open", "done", "cancelled")
SENDER_KINDS = ("customer", "staff", "ai", "schedule", "api", "broadcast", "app", "system")
# Senders whose message counts as the shop handling the chat.
HANDLING_SENDERS = frozenset({"staff", "app"})
PREVIEW_TOKENS = {
    1: "[image]",
    2: "[video]",
    3: "[audio]",
    7: "[sticker]",
    13: "[contact]",
    14: "[file]",
    15: "[location]",
}
MAX_PAGE_SIZE = 200
MAX_SEGMENTS = 100
MAX_TAGS = 300
MAX_MERGE = 20
MAX_BULK = 500
MAX_FIELDS = 30
MAX_BROADCAST_RECIPIENTS = 1000


class CrmContext(Protocol):
    """What the CRM needs from the host application."""

    def crm_tenant_id(self, user: dict[str, Any] | None) -> str: ...

    def crm_scope(self, user: dict[str, Any] | None) -> list[str] | None: ...

    def crm_accounts(self, owner_id: str) -> list[dict[str, Any]]: ...

    def crm_members(self, owner_id: str) -> list[dict[str, Any]]: ...

    def crm_has_permission(self, user: dict[str, Any] | None, permission: str) -> bool: ...

    def crm_notify(self, user_id: str, payload: dict[str, Any]) -> None: ...

    def crm_changed(self, account_ids: list[str]) -> None: ...


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return uuid.uuid4().hex


def channel_family(channel: str) -> str:
    return "telegram" if str(channel or "").startswith("telegram") else "line"


def chat_external_id(channel: str, chat_id: str) -> str:
    """The person behind a one-to-one chat id, or "" for groups and unknown ids."""
    chat_id = str(chat_id or "")
    if channel_family(channel) == "telegram":
        return chat_id[3:] if chat_id.startswith("tgu") and chat_id[3:].isdigit() else ""
    return chat_id if chat_id[:1].lower() == "u" and len(chat_id) >= 8 else ""


def preview_text(text: str, content_type: int) -> str:
    text = " ".join(str(text or "").split())
    if text and content_type in (0, None):
        return text[:160]
    return PREVIEW_TOKENS.get(int(content_type or 0), text[:160] or "[media]")


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _loads(text: Any) -> dict[str, Any]:
    try:
        data = json.loads(str(text or "{}"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _placeholders(values: list[Any]) -> str:
    return ",".join("?" for _ in values)


def _user_id(user: dict[str, Any] | None) -> str:
    return str((user or {}).get("id") or "")


class CrmService:
    def __init__(self, db: CrmDatabase, ctx: CrmContext) -> None:
        self.db = db
        self.ctx = ctx
        self.db.migrate()

    def close(self) -> None:
        self.db.close()

    # -- access --------------------------------------------------------------
    def _require(
        self, user: dict[str, Any] | None, permission: str
    ) -> tuple[str, list[str] | None]:
        if not self.ctx.crm_has_permission(user, permission):
            raise forbidden()
        owner_id = self.ctx.crm_tenant_id(user)
        if not owner_id:
            raise forbidden()
        return owner_id, self.ctx.crm_scope(user)

    def _can(self, user: dict[str, Any] | None, permission: str) -> bool:
        return bool(self.ctx.crm_has_permission(user, permission))

    def _account(self, owner_id: str, scope: list[str] | None, account_id: str) -> dict[str, Any]:
        account_id = str(account_id or "")
        for account in self.ctx.crm_accounts(owner_id):
            if account.get("id") == account_id and (scope is None or account_id in scope):
                return account
        raise CrmError(403, "Permission denied for this account.", "account_forbidden")

    def _customer_row(
        self, owner_id: str, scope: list[str] | None, customer_id: str
    ) -> dict[str, Any]:
        where, params = customer_where(owner_id, {}, scope, now_ms())
        row = self.db.query_one(
            f"SELECT c.* FROM crm_customers c WHERE {where} AND c.id = ?",
            [*params, str(customer_id or "")],
        )
        if not row:
            raise not_found("Customer not found.", "customer_not_found")
        return row

    def _conversation_row(
        self, owner_id: str, scope: list[str] | None, conversation_id: str
    ) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM crm_conversations WHERE id = ? AND owner_id = ?",
            (str(conversation_id or ""), owner_id),
        )
        if not row or (scope is not None and row["account_id"] not in scope):
            raise not_found("Chat not found.", "conversation_not_found")
        return row

    def _member_map(self, owner_id: str) -> dict[str, dict[str, Any]]:
        return {str(m.get("id")): m for m in self.ctx.crm_members(owner_id) if m.get("id")}

    def settings_for(self, owner_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT data FROM crm_settings WHERE owner_id = ?", (owner_id,))
        settings = dict(DEFAULT_SETTINGS)
        stored = _loads(row["data"]) if row else {}
        settings.update({key: stored[key] for key in DEFAULT_SETTINGS if key in stored})
        return settings

    def _event(
        self,
        owner_id: str,
        kind: str,
        *,
        customer_id: str = "",
        conversation_id: str = "",
        actor_id: str = "",
        data: dict[str, Any] | None = None,
        at: int | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO crm_events (id, owner_id, customer_id, conversation_id, kind, "
            "actor_id, data, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                owner_id,
                customer_id,
                conversation_id,
                kind,
                actor_id,
                _dumps(data or {}),
                at or now_ms(),
            ),
        )

    # -- ingest (called by the channels, not by users) -----------------------
    def ingest(
        self,
        *,
        owner_id: str,
        account_id: str,
        channel: str,
        chat_id: str,
        contact: dict[str, Any],
        messages: list[dict[str, Any]],
        track_unread: bool = False,
    ) -> dict[str, Any]:
        """Record messages of one chat; creates the customer and chat on first sight.

        Each message: ``externalId, direction ("in"/"out"), senderKind, senderId,
        text, contentType, meta, at`` (ms). Duplicates are ignored, so callers may
        resend overlapping history.
        """
        family = channel_family(channel)
        external = _clean(contact.get("externalId"), 120) or chat_external_id(channel, chat_id)
        if not owner_id or not account_id or not chat_id or not external:
            return {}
        items = sorted(
            (m for m in messages if isinstance(m, dict) and _int(m.get("at")) > 0),
            key=lambda m: _int(m.get("at")),
        )
        now = now_ms()
        settings = self.settings_for(owner_id)
        with self.db.transaction():
            conv = self.db.query_one(
                "SELECT * FROM crm_conversations WHERE account_id = ? AND chat_id = ?",
                (account_id, chat_id),
            )
            if conv is not None and conv["owner_id"] != owner_id:
                # The account moved to another shop: the old shop keeps no live chat.
                self.db.execute("DELETE FROM crm_messages WHERE conversation_id = ?", (conv["id"],))
                self.db.execute("DELETE FROM crm_conversations WHERE id = ?", (conv["id"],))
                conv = None
            created = conv is None
            if conv is None:
                customer_id = self._customer_for_identity(owner_id, family, external, contact, now)
                conv = self._new_conversation(
                    owner_id, customer_id, channel, account_id, chat_id, items, settings, now
                )
            else:
                self._update_identity(owner_id, family, external, contact, now)
            previous_status = conv["status"]
            previous_last = _int(conv["last_message_at"])
            inserted = [m for m in items if self._insert_message(owner_id, conv["id"], m)]
            status = conv["status"]
            assigned = conv["assigned_to"]
            reopened = False
            auto_assigned = ""
            new_inbound = 0
            for message in inserted:
                at = _int(message.get("at"))
                # Equal times are new too: Telegram stamps whole seconds.
                if at < previous_last:
                    continue
                if message.get("direction") == "in":
                    new_inbound += 1
                if created:
                    continue  # a new chat already starts with the right status
                if message.get("direction") == "in":
                    if status == "closed":
                        status = "open" if assigned else "new"
                        reopened = True
                    elif status == "pending":
                        status = "open"
                    continue
                kind = str(message.get("senderKind") or "")
                if kind in HANDLING_SENDERS:
                    if status == "new":
                        status = "open"
                    sender = str(message.get("senderId") or "")
                    if (
                        kind == "staff"
                        and sender
                        and not assigned
                        and settings.get("autoAssignOnReply")
                    ):
                        assigned = auto_assigned = sender
            updates: dict[str, Any] = {}
            if inserted:
                newest = max(inserted, key=lambda m: _int(m.get("at")))
                last_in = max(
                    [_int(m.get("at")) for m in inserted if m.get("direction") == "in"],
                    default=0,
                )
                last_out = max(
                    [_int(m.get("at")) for m in inserted if m.get("direction") != "in"],
                    default=0,
                )
                if _int(newest.get("at")) >= previous_last:
                    updates["last_message_at"] = _int(newest.get("at"))
                    updates["last_preview"] = preview_text(
                        str(newest.get("text") or ""), _int(newest.get("contentType"))
                    )
                if last_in > _int(conv["last_inbound_at"]):
                    updates["last_inbound_at"] = last_in
                if last_out > _int(conv["last_outbound_at"]):
                    updates["last_outbound_at"] = last_out
                if track_unread:
                    staff_replied = any(
                        m.get("direction") != "in" and m.get("senderKind") in HANDLING_SENDERS
                        for m in inserted
                    )
                    updates["unread"] = 0 if staff_replied else _int(conv["unread"]) + new_inbound
            if status != previous_status:
                updates["status"] = status
                updates["closed_at"] = now if status == "closed" else 0
            if auto_assigned:
                updates["assigned_to"] = auto_assigned
                updates["assigned_at"] = now
            if updates:
                updates["updated_at"] = now
                self._update_row("crm_conversations", conv["id"], updates)
                self._touch_customer(conv["customer_id"], updates)
            if reopened:
                self._event(
                    owner_id,
                    "status",
                    customer_id=conv["customer_id"],
                    conversation_id=conv["id"],
                    actor_id="system",
                    data={"from": previous_status, "to": status, "reason": "customer_message"},
                )
            if auto_assigned:
                self._event(
                    owner_id,
                    "assigned",
                    customer_id=conv["customer_id"],
                    conversation_id=conv["id"],
                    actor_id=auto_assigned,
                    data={"from": "", "to": auto_assigned, "reason": "replied"},
                )
        return {
            "conversationId": conv["id"],
            "customerId": conv["customer_id"],
            "created": created,
            "status": status,
            "previousStatus": previous_status,
            "assignedTo": assigned,
            "newInbound": new_inbound,
            "inserted": len(inserted),
            "reopened": reopened,
        }

    def _customer_for_identity(
        self,
        owner_id: str,
        family: str,
        external: str,
        contact: dict[str, Any],
        now: int,
    ) -> str:
        identity = self.db.query_one(
            "SELECT * FROM crm_identities WHERE owner_id = ? AND channel = ? AND external_id = ?",
            (owner_id, family, external),
        )
        name = _one_line(contact.get("name"), 120)
        if identity:
            exists = self.db.scalar(
                "SELECT id FROM crm_customers WHERE id = ?", (identity["customer_id"],)
            )
            if exists:
                self._update_identity(owner_id, family, external, contact, now)
                return str(identity["customer_id"])
            customer_id = self._insert_customer(owner_id, name, now, created_by="system")
            self.db.execute(
                "UPDATE crm_identities SET customer_id = ?, updated_at = ? WHERE id = ?",
                (customer_id, now, identity["id"]),
            )
            self._update_identity(owner_id, family, external, contact, now)
            return customer_id
        customer_id = self._insert_customer(owner_id, name, now, created_by="system")
        self.db.execute(
            "INSERT INTO crm_identities (id, owner_id, customer_id, channel, external_id, name, "
            "username, picture, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                owner_id,
                customer_id,
                family,
                external,
                name,
                _one_line(contact.get("username"), 80),
                _clean(contact.get("picture"), 300),
                now,
                now,
            ),
        )
        self._event(owner_id, "created", customer_id=customer_id, actor_id="system",
                    data={"channel": family}, at=now)
        self._refresh_search(customer_id)
        return customer_id

    def _update_identity(
        self, owner_id: str, family: str, external: str, contact: dict[str, Any], now: int
    ) -> None:
        name = _one_line(contact.get("name"), 120)
        username = _one_line(contact.get("username"), 80)
        picture = _clean(contact.get("picture"), 300)
        if not (name or username or picture):
            return
        identity = self.db.query_one(
            "SELECT * FROM crm_identities WHERE owner_id = ? AND channel = ? AND external_id = ?",
            (owner_id, family, external),
        )
        if not identity:
            return
        updates: dict[str, Any] = {}
        if name and name != identity["name"]:
            updates["name"] = name
        if username and username != identity["username"]:
            updates["username"] = username
        if picture and picture != identity["picture"]:
            updates["picture"] = picture
        if not updates:
            return
        updates["updated_at"] = now
        self._update_row("crm_identities", identity["id"], updates)
        customer = self.db.query_one(
            "SELECT id, display_name, picture FROM crm_customers WHERE id = ?",
            (identity["customer_id"],),
        )
        if customer:
            fill: dict[str, Any] = {}
            if name and not customer["display_name"]:
                fill["display_name"] = name
            if picture and not customer["picture"]:
                fill["picture"] = picture
            if fill:
                fill["updated_at"] = now
                self._update_row("crm_customers", customer["id"], fill)
            self._refresh_search(customer["id"])

    def _insert_customer(
        self,
        owner_id: str,
        name: str,
        now: int,
        *,
        created_by: str,
        phone: str = "",
        email: str = "",
        company: str = "",
        fields: dict[str, str] | None = None,
    ) -> str:
        customer_id = new_id()
        self.db.execute(
            "INSERT INTO crm_customers (id, owner_id, display_name, phone, email, company, "
            "fields, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                customer_id,
                owner_id,
                name,
                phone,
                email,
                company,
                _dumps(fields or {}),
                created_by,
                now,
                now,
            ),
        )
        self._refresh_search(customer_id)
        return customer_id

    def _new_conversation(
        self,
        owner_id: str,
        customer_id: str,
        channel: str,
        account_id: str,
        chat_id: str,
        items: list[dict[str, Any]],
        settings: dict[str, Any],
        now: int,
    ) -> dict[str, Any]:
        last = items[-1] if items else None
        close_days = _int(settings.get("autoCloseDays"))
        if last is None:
            status = "open"
        elif close_days and now - _int(last.get("at")) > close_days * DAY_MS:
            status = "closed"
        else:
            status = "new" if last.get("direction") == "in" else "open"
        row = {
            "id": new_id(),
            "owner_id": owner_id,
            "customer_id": customer_id,
            "channel": channel,
            "account_id": account_id,
            "chat_id": chat_id,
            "status": status,
            "assigned_to": "",
            "assigned_at": 0,
            "unread": 0,
            "last_message_at": 0,
            "last_inbound_at": 0,
            "last_outbound_at": 0,
            "last_preview": "",
            "created_at": now,
            "updated_at": now,
            "closed_at": now if status == "closed" else 0,
        }
        columns = list(row)
        self.db.execute(
            f"INSERT INTO crm_conversations ({', '.join(columns)}) "
            f"VALUES ({_placeholders(columns)})",
            [row[key] for key in columns],
        )
        return row

    def _insert_message(self, owner_id: str, conversation_id: str, message: dict[str, Any]) -> bool:
        external = _clean(message.get("externalId"), 120)
        if not external:
            return False
        direction = "in" if message.get("direction") == "in" else "out"
        kind = str(message.get("senderKind") or ("customer" if direction == "in" else "app"))
        if kind not in SENDER_KINDS:
            kind = "app"
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        return (
            self.db.execute(
                "INSERT INTO crm_messages (id, owner_id, conversation_id, external_id, direction, "
                "sender_kind, sender_id, text, content_type, meta, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (conversation_id, external_id) DO NOTHING",
                (
                    new_id(),
                    owner_id,
                    conversation_id,
                    external,
                    direction,
                    kind,
                    _clean(message.get("senderId"), 120),
                    _clean(message.get("text"), 10_000),
                    _int(message.get("contentType")),
                    _dumps(meta),
                    _int(message.get("at")),
                ),
            )
            > 0
        )

    def _touch_customer(self, customer_id: str, conv_updates: dict[str, Any]) -> None:
        customer = self.db.query_one(
            "SELECT last_message_at, last_inbound_at, last_outbound_at FROM crm_customers "
            "WHERE id = ?",
            (customer_id,),
        )
        if not customer:
            return
        updates: dict[str, Any] = {}
        for key in ("last_inbound_at", "last_outbound_at"):
            if _int(conv_updates.get(key)) > _int(customer[key]):
                updates[key] = _int(conv_updates[key])
        if _int(conv_updates.get("last_message_at")) >= _int(customer["last_message_at"]) and (
            "last_message_at" in conv_updates
        ):
            updates["last_message_at"] = _int(conv_updates["last_message_at"])
            updates["last_preview"] = conv_updates.get("last_preview", "")
        if updates:
            self._update_row("crm_customers", customer_id, updates)

    def _update_row(self, table: str, row_id: str, updates: dict[str, Any]) -> None:
        columns = list(updates)
        self.db.execute(
            f"UPDATE {table} SET {', '.join(f'{c} = ?' for c in columns)} WHERE id = ?",
            [*[updates[c] for c in columns], row_id],
        )

    def _refresh_search(self, customer_id: str) -> None:
        customer = self.db.query_one("SELECT * FROM crm_customers WHERE id = ?", (customer_id,))
        if not customer:
            return
        identities = self.db.query(
            "SELECT name, username, external_id FROM crm_identities WHERE customer_id = ?",
            (customer_id,),
        )
        parts = [
            customer["display_name"],
            customer["phone"],
            "".join(ch for ch in customer["phone"] if ch.isdigit()),
            customer["email"],
            customer["company"],
        ]
        for identity in identities:
            parts.extend([identity["name"], identity["username"]])
            if identity["username"]:
                parts.append("@" + identity["username"])
        search = " ".join(p for p in parts if p).lower()[:2000]
        if search != customer["search"]:
            self.db.execute(
                "UPDATE crm_customers SET search = ? WHERE id = ?", (search, customer_id)
            )

    def mark_read(self, account_id: str, chat_id: str) -> None:
        self.db.execute(
            "UPDATE crm_conversations SET unread = 0 WHERE account_id = ? AND chat_id = ?",
            (account_id, chat_id),
        )

    def conversation_for_chat(self, account_id: str, chat_id: str) -> dict[str, Any] | None:
        """System lookup used by channels and notifications (no access check)."""
        row = self.db.query_one(
            "SELECT v.*, c.display_name FROM crm_conversations v "
            "JOIN crm_customers c ON c.id = v.customer_id "
            "WHERE v.account_id = ? AND v.chat_id = ?",
            (account_id, chat_id),
        )
        return row

    def assignees_for_chats(self, account_id: str, chat_ids: list[str]) -> dict[str, str]:
        if not chat_ids:
            return {}
        rows = self.db.query(
            "SELECT chat_id, assigned_to FROM crm_conversations WHERE account_id = ? "
            f"AND chat_id IN ({_placeholders(chat_ids)})",
            [account_id, *chat_ids],
        )
        return {row["chat_id"]: row["assigned_to"] for row in rows if row["assigned_to"]}

    def account_chats(self, account_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        """Chats of one Telegram account, newest first (its inbox lives here)."""
        return self.db.query(
            "SELECT v.*, c.display_name, c.picture AS customer_picture, i.name AS identity_name, "
            "i.username AS identity_username, i.picture AS identity_picture "
            "FROM crm_conversations v JOIN crm_customers c ON c.id = v.customer_id "
            "LEFT JOIN crm_identities i ON i.customer_id = v.customer_id "
            "AND i.owner_id = v.owner_id AND i.channel = 'telegram' "
            "WHERE v.account_id = ? ORDER BY v.last_message_at DESC LIMIT ?",
            (account_id, limit),
        )

    def chat_messages(
        self, account_id: str, chat_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Recent messages of one chat, oldest first (Telegram inbox)."""
        rows = self.db.query(
            "SELECT m.* FROM crm_messages m JOIN crm_conversations v ON v.id = m.conversation_id "
            "WHERE v.account_id = ? AND v.chat_id = ? ORDER BY m.at DESC LIMIT ?",
            (account_id, chat_id, limit),
        )
        rows.reverse()
        return rows

    def find_message(self, account_id: str, chat_id: str, external_id: str) -> dict[str, Any] | None:
        return self.db.query_one(
            "SELECT m.* FROM crm_messages m JOIN crm_conversations v ON v.id = m.conversation_id "
            "WHERE v.account_id = ? AND v.chat_id = ? AND m.external_id = ?",
            (account_id, chat_id, external_id),
        )

    def purge_account(self, account_id: str) -> None:
        with self.db.transaction():
            ids = [
                row["id"]
                for row in self.db.query(
                    "SELECT id FROM crm_conversations WHERE account_id = ?", (account_id,)
                )
            ]
            for chunk_start in range(0, len(ids), 200):
                chunk = ids[chunk_start : chunk_start + 200]
                self.db.execute(
                    f"DELETE FROM crm_messages WHERE conversation_id IN ({_placeholders(chunk)})",
                    chunk,
                )
            self.db.execute("DELETE FROM crm_conversations WHERE account_id = ?", (account_id,))
            self.db.execute(
                "UPDATE crm_broadcasts SET status = 'cancelled', error = 'account_removed' "
                "WHERE account_id = ? AND status IN ('queued', 'running', 'paused')",
                (account_id,),
            )

    # -- customers -----------------------------------------------------------
    def _hydrate(
        self, scope: list[str] | None, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not rows:
            return []
        ids = [row["id"] for row in rows]
        tags: dict[str, list[dict[str, Any]]] = {}
        for row in self.db.query(
            "SELECT ct.customer_id, t.id, t.name, t.color FROM crm_customer_tags ct "
            "JOIN crm_tags t ON t.id = ct.tag_id "
            f"WHERE ct.customer_id IN ({_placeholders(ids)}) ORDER BY t.name_key",
            ids,
        ):
            tags.setdefault(row["customer_id"], []).append(
                {"id": row["id"], "name": row["name"], "color": row["color"]}
            )
        identities: dict[str, list[dict[str, Any]]] = {}
        for row in self.db.query(
            "SELECT customer_id, channel, external_id, name, username, picture "
            f"FROM crm_identities WHERE customer_id IN ({_placeholders(ids)})",
            ids,
        ):
            identities.setdefault(row["customer_id"], []).append(
                {
                    "channel": row["channel"],
                    "externalId": row["external_id"],
                    "name": row["name"],
                    "username": row["username"],
                    "picture": row["picture"],
                }
            )
        conversations: dict[str, list[dict[str, Any]]] = {}
        for row in self.db.query(
            "SELECT id, customer_id, account_id, chat_id, channel, status, assigned_to, "
            "last_message_at, unread FROM crm_conversations "
            f"WHERE customer_id IN ({_placeholders(ids)}) ORDER BY last_message_at DESC",
            ids,
        ):
            if scope is not None and row["account_id"] not in scope:
                continue
            conversations.setdefault(row["customer_id"], []).append(_public_conversation(row))
        tasks: dict[str, tuple[int, int]] = {}
        for row in self.db.query(
            "SELECT customer_id, COUNT(*) AS n, MIN(due_at) AS due FROM crm_tasks "
            f"WHERE status = 'open' AND customer_id IN ({_placeholders(ids)}) "
            "GROUP BY customer_id",
            ids,
        ):
            tasks[row["customer_id"]] = (_int(row["n"]), _int(row["due"]))
        out = []
        for row in rows:
            count, due = tasks.get(row["id"], (0, 0))
            out.append(
                {
                    **_public_customer(row),
                    "tags": tags.get(row["id"], []),
                    "identities": identities.get(row["id"], []),
                    "conversations": conversations.get(row["id"], []),
                    "openTasks": count,
                    "nextDueAt": due,
                }
            )
        return out

    def _filter_from(self, owner_id: str, params: dict[str, Any]) -> dict[str, Any]:
        raw = params.get("filter") if isinstance(params.get("filter"), dict) else params
        flt = normalize_filter(raw)
        segment_id = str(params.get("segmentId") or "")
        if segment_id:
            segment = self.db.query_one(
                "SELECT filter_json FROM crm_segments WHERE id = ? AND owner_id = ?",
                (segment_id, owner_id),
            )
            if not segment:
                raise not_found("Segment not found.", "segment_not_found")
            base = normalize_filter(_loads(segment["filter_json"]))
            if flt.get("q"):
                base["q"] = flt["q"]
            flt = base
        return flt

    def list_customers(self, user: dict[str, Any] | None, params: dict[str, Any]) -> dict[str, Any]:
        owner_id, scope = self._require(user, "read")
        flt = self._filter_from(owner_id, params)
        sort = str(params.get("sort") or "recent")
        if sort not in SORTS:
            sort = "recent"
        page_size = min(MAX_PAGE_SIZE, max(1, _int(params.get("pageSize"), 50)))
        page = max(1, _int(params.get("page"), 1))
        where, args = customer_where(owner_id, flt, scope, now_ms())
        total = _int(self.db.scalar(f"SELECT COUNT(*) FROM crm_customers c WHERE {where}", args))
        rows = self.db.query(
            f"SELECT c.* FROM crm_customers c WHERE {where} ORDER BY {SORTS[sort]} "
            "LIMIT ? OFFSET ?",
            [*args, page_size, (page - 1) * page_size],
        )
        return {
            "ok": True,
            "customers": self._hydrate(scope, rows),
            "total": total,
            "page": page,
            "pageSize": page_size,
            "filter": flt,
            "sort": sort,
        }

    def customer_detail(self, user: dict[str, Any] | None, customer_id: str) -> dict[str, Any]:
        owner_id, scope = self._require(user, "read")
        row = self._customer_row(owner_id, scope, customer_id)
        customer = self._hydrate(scope, [row])[0]
        notes = [
            _public_note(note)
            for note in self.db.query(
                "SELECT * FROM crm_notes WHERE customer_id = ? AND owner_id = ? "
                "ORDER BY pinned DESC, created_at DESC LIMIT 200",
                (row["id"], owner_id),
            )
        ]
        tasks = [
            _public_task(task)
            for task in self.db.query(
                "SELECT * FROM crm_tasks WHERE customer_id = ? AND owner_id = ? "
                "ORDER BY CASE WHEN status = 'open' THEN 0 ELSE 1 END, due_at, created_at DESC "
                "LIMIT 200",
                (row["id"], owner_id),
            )
        ]
        return {
            "ok": True,
            "customer": customer,
            "notes": notes,
            "tasks": tasks,
            "timeline": self._timeline(owner_id, scope, row["id"], before=0, limit=60),
            "members": self._public_members(owner_id),
            "canEdit": self._can(user, "crm"),
            "canManage": self._can(user, "manage_crm"),
        }

    def timeline(self, user: dict[str, Any] | None, customer_id: str, before: int, limit: int) -> dict[str, Any]:
        owner_id, scope = self._require(user, "read")
        row = self._customer_row(owner_id, scope, customer_id)
        items = self._timeline(owner_id, scope, row["id"], before=before, limit=limit)
        return {"ok": True, "timeline": items, "nextBefore": items[-1]["at"] if items else 0}

    def _timeline(
        self, owner_id: str, scope: list[str] | None, customer_id: str, *, before: int, limit: int
    ) -> list[dict[str, Any]]:
        limit = min(200, max(1, limit))
        before = before or 2**62
        scope_sql = ""
        params: list[Any] = [customer_id, owner_id, before]
        if scope is not None:
            scope_sql = f" AND v.account_id IN ({_placeholders(scope) or 'NULL'})"
            params.extend(scope)
        messages = self.db.query(
            "SELECT m.direction, m.sender_kind, m.sender_id, m.text, m.content_type, m.at, "
            "v.account_id, v.channel, v.chat_id FROM crm_messages m "
            "JOIN crm_conversations v ON v.id = m.conversation_id "
            f"WHERE v.customer_id = ? AND v.owner_id = ? AND m.at < ?{scope_sql} "
            "ORDER BY m.at DESC LIMIT ?",
            [*params, limit],
        )
        events = self.db.query(
            "SELECT kind, actor_id, data, at, conversation_id FROM crm_events "
            "WHERE customer_id = ? AND owner_id = ? AND at < ? ORDER BY at DESC LIMIT ?",
            (customer_id, owner_id, before, limit),
        )
        items: list[dict[str, Any]] = [
            {
                "type": "message",
                "at": _int(m["at"]),
                "direction": m["direction"],
                "senderKind": m["sender_kind"],
                "senderId": m["sender_id"],
                "text": m["text"],
                "contentType": _int(m["content_type"]),
                "accountId": m["account_id"],
                "channel": m["channel"],
                "chatId": m["chat_id"],
            }
            for m in messages
        ]
        items.extend(
            {
                "type": "event",
                "at": _int(e["at"]),
                "kind": e["kind"],
                "actorId": e["actor_id"],
                "data": _loads(e["data"]),
            }
            for e in events
        )
        items.sort(key=lambda item: item["at"], reverse=True)
        return items[:limit]

    def create_customer(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, _scope = self._require(user, "crm")
        values = _customer_values(body, require_name=True)
        now = now_ms()
        with self.db.transaction():
            customer_id = self._insert_customer(
                owner_id,
                values["display_name"],
                now,
                created_by=_user_id(user),
                phone=values.get("phone", ""),
                email=values.get("email", ""),
                company=values.get("company", ""),
                fields=values.get("fields"),
            )
            self._event(owner_id, "created", customer_id=customer_id, actor_id=_user_id(user))
        return self.customer_detail(user, customer_id)

    def update_customer(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, scope = self._require(user, "crm")
        row = self._customer_row(owner_id, scope, str(body.get("id") or ""))
        changed: dict[str, Any] = {}
        for key, value in _customer_values(body, require_name=False).items():
            stored_value = _dumps(value) if key == "fields" else value
            if stored_value != row[key]:
                changed[key] = stored_value
        if changed:
            with self.db.transaction():
                self._update_row("crm_customers", row["id"], {**changed, "updated_at": now_ms()})
                self._refresh_search(row["id"])
                self._event(
                    owner_id,
                    "updated",
                    customer_id=row["id"],
                    actor_id=_user_id(user),
                    data={"fields": sorted(changed)},
                )
        return self.customer_detail(user, row["id"])

    def delete_customer(self, user: dict[str, Any] | None, customer_id: str) -> dict[str, Any]:
        owner_id, scope = self._require(user, "manage_crm")
        row = self._customer_row(owner_id, scope, customer_id)
        with self.db.transaction():
            conv_ids = [
                r["id"]
                for r in self.db.query(
                    "SELECT id FROM crm_conversations WHERE customer_id = ?", (row["id"],)
                )
            ]
            if conv_ids:
                self.db.execute(
                    f"DELETE FROM crm_messages WHERE conversation_id IN ({_placeholders(conv_ids)})",
                    conv_ids,
                )
            for table in ("crm_conversations", "crm_identities", "crm_customer_tags",
                          "crm_notes", "crm_tasks", "crm_events"):
                self.db.execute(f"DELETE FROM {table} WHERE customer_id = ?", (row["id"],))
            self.db.execute("DELETE FROM crm_customers WHERE id = ?", (row["id"],))
        return {"ok": True, "id": row["id"]}

    def merge_customers(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, scope = self._require(user, "manage_crm")
        target = self._customer_row(owner_id, scope, str(body.get("targetId") or ""))
        source_ids = [
            item for item in dict.fromkeys(str(x) for x in (body.get("sourceIds") or []))
            if item and item != target["id"]
        ]
        if not source_ids:
            raise bad_request("Choose at least one customer to merge.", "nothing_to_merge")
        if len(source_ids) > MAX_MERGE:
            raise bad_request(f"Merge at most {MAX_MERGE} customers at a time.", "too_many")
        sources = [self._customer_row(owner_id, scope, item) for item in source_ids]
        now = now_ms()
        with self.db.transaction():
            fill: dict[str, Any] = {}
            fields = _loads(target["fields"])
            for source in sources:
                for key in ("display_name", "phone", "email", "company", "picture"):
                    if not (fill.get(key) or target[key]) and source[key]:
                        fill[key] = source[key]
                for key, value in _loads(source["fields"]).items():
                    fields.setdefault(key, value)
                for key in ("last_inbound_at", "last_outbound_at"):
                    if _int(source[key]) > _int(fill.get(key, target[key])):
                        fill[key] = _int(source[key])
                if _int(source["last_message_at"]) > _int(
                    fill.get("last_message_at", target["last_message_at"])
                ):
                    fill["last_message_at"] = _int(source["last_message_at"])
                    fill["last_preview"] = source["last_preview"]
                for table in ("crm_identities", "crm_conversations", "crm_notes",
                              "crm_tasks", "crm_events"):
                    self.db.execute(
                        f"UPDATE {table} SET customer_id = ? WHERE customer_id = ?",
                        (target["id"], source["id"]),
                    )
                for tag in self.db.query(
                    "SELECT tag_id FROM crm_customer_tags WHERE customer_id = ?", (source["id"],)
                ):
                    self.db.execute(
                        "INSERT INTO crm_customer_tags (customer_id, tag_id, owner_id, created_at) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT (customer_id, tag_id) DO NOTHING",
                        (target["id"], tag["tag_id"], owner_id, now),
                    )
                self.db.execute(
                    "DELETE FROM crm_customer_tags WHERE customer_id = ?", (source["id"],)
                )
                self.db.execute(
                    "UPDATE crm_broadcast_recipients SET customer_id = ? WHERE customer_id = ? "
                    "AND NOT EXISTS (SELECT 1 FROM crm_broadcast_recipients r2 WHERE "
                    "r2.broadcast_id = crm_broadcast_recipients.broadcast_id "
                    "AND r2.customer_id = ?)",
                    (target["id"], source["id"], target["id"]),
                )
                self.db.execute(
                    "DELETE FROM crm_broadcast_recipients WHERE customer_id = ?", (source["id"],)
                )
                self.db.execute("DELETE FROM crm_customers WHERE id = ?", (source["id"],))
            fill["fields"] = _dumps(fields)
            fill["updated_at"] = now
            self._update_row("crm_customers", target["id"], fill)
            self._refresh_search(target["id"])
            self._event(
                owner_id,
                "merged",
                customer_id=target["id"],
                actor_id=_user_id(user),
                data={"names": [s["display_name"] for s in sources], "count": len(sources)},
            )
        self.ctx.crm_changed([a["id"] for a in self.ctx.crm_accounts(owner_id)])
        return self.customer_detail(user, target["id"])

    def export_csv(self, user: dict[str, Any] | None, params: dict[str, Any]) -> bytes:
        owner_id, scope = self._require(user, "manage_crm")
        flt = self._filter_from(owner_id, params)
        where, args = customer_where(owner_id, flt, scope, now_ms())
        rows = self.db.query(
            f"SELECT c.* FROM crm_customers c WHERE {where} ORDER BY {SORTS['name']} LIMIT 20000",
            args,
        )
        members = self._member_map(owner_id)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["id", "name", "phone", "email", "company", "tags", "channels", "status",
             "assigned_to", "last_contact", "created", "open_tasks"]
        )
        for start in range(0, len(rows), MAX_PAGE_SIZE):
            for customer in self._hydrate(scope, rows[start : start + MAX_PAGE_SIZE]):
                conv = customer["conversations"][0] if customer["conversations"] else {}
                assignee = members.get(str(conv.get("assignedTo") or ""), {})
                writer.writerow(
                    [
                        customer["id"],
                        _csv_safe(customer["displayName"]),
                        _csv_safe(customer["phone"]),
                        _csv_safe(customer["email"]),
                        _csv_safe(customer["company"]),
                        _csv_safe(", ".join(t["name"] for t in customer["tags"])),
                        ", ".join(sorted({i["channel"] for i in customer["identities"]})),
                        conv.get("status", ""),
                        _csv_safe(str(assignee.get("name") or "")),
                        _iso(customer["lastMessageAt"]),
                        _iso(customer["createdAt"]),
                        customer["openTasks"],
                    ]
                )
        return ("﻿" + buffer.getvalue()).encode("utf-8")

    # -- tags ----------------------------------------------------------------
    def list_tags(self, user: dict[str, Any] | None) -> dict[str, Any]:
        owner_id, _scope = self._require(user, "read")
        rows = self.db.query(
            "SELECT t.*, (SELECT COUNT(*) FROM crm_customer_tags ct WHERE ct.tag_id = t.id) "
            "AS customers FROM crm_tags t WHERE t.owner_id = ? ORDER BY t.name_key",
            (owner_id,),
        )
        return {
            "ok": True,
            "tags": [
                {"id": r["id"], "name": r["name"], "color": r["color"],
                 "customers": _int(r["customers"])}
                for r in rows
            ],
        }

    def _tag_for_name(self, owner_id: str, name: str, color: str = "") -> str:
        name = _one_line(name, 40)
        if not name:
            raise bad_request("Tag name is required.", "tag_name_required")
        key = name.casefold()
        existing = self.db.scalar(
            "SELECT id FROM crm_tags WHERE owner_id = ? AND name_key = ?", (owner_id, key)
        )
        if existing:
            return str(existing)
        count = _int(self.db.scalar("SELECT COUNT(*) FROM crm_tags WHERE owner_id = ?", (owner_id,)))
        if count >= MAX_TAGS:
            raise bad_request(f"A shop can have at most {MAX_TAGS} tags.", "too_many_tags")
        tag_id = new_id()
        self.db.execute(
            "INSERT INTO crm_tags (id, owner_id, name, name_key, color, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tag_id, owner_id, name, key, _color(color), now_ms()),
        )
        return tag_id

    def create_tag(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, _scope = self._require(user, "crm")
        with self.db.transaction():
            tag_id = self._tag_for_name(owner_id, str(body.get("name") or ""), str(body.get("color") or ""))
        return {"ok": True, "id": tag_id, **self.list_tags(user)}

    def update_tag(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, _scope = self._require(user, "manage_crm")
        tag = self.db.query_one(
            "SELECT * FROM crm_tags WHERE id = ? AND owner_id = ?", (str(body.get("id") or ""), owner_id)
        )
        if not tag:
            raise not_found("Tag not found.", "tag_not_found")
        updates: dict[str, Any] = {}
        if "name" in body:
            name = _one_line(body.get("name"), 40)
            if not name:
                raise bad_request("Tag name is required.", "tag_name_required")
            clash = self.db.scalar(
                "SELECT id FROM crm_tags WHERE owner_id = ? AND name_key = ? AND id <> ?",
                (owner_id, name.casefold(), tag["id"]),
            )
            if clash:
                raise CrmError(409, "Another tag already has this name.", "tag_exists")
            updates.update({"name": name, "name_key": name.casefold()})
        if "color" in body:
            updates["color"] = _color(str(body.get("color") or ""))
        if updates:
            self._update_row("crm_tags", tag["id"], updates)
        return self.list_tags(user)

    def delete_tag(self, user: dict[str, Any] | None, tag_id: str) -> dict[str, Any]:
        owner_id, _scope = self._require(user, "manage_crm")
        tag = self.db.query_one(
            "SELECT id FROM crm_tags WHERE id = ? AND owner_id = ?", (str(tag_id or ""), owner_id)
        )
        if not tag:
            raise not_found("Tag not found.", "tag_not_found")
        with self.db.transaction():
            self.db.execute("DELETE FROM crm_customer_tags WHERE tag_id = ?", (tag["id"],))
            self.db.execute("DELETE FROM crm_tags WHERE id = ?", (tag["id"],))
            for segment in self.db.query(
                "SELECT id, filter_json FROM crm_segments WHERE owner_id = ?", (owner_id,)
            ):
                flt = _loads(segment["filter_json"])
                changed = False
                for key in ("tagsAny", "tagsAll", "tagsNone"):
                    if isinstance(flt.get(key), list) and tag["id"] in flt[key]:
                        flt[key] = [item for item in flt[key] if item != tag["id"]]
                        changed = True
                if changed:
                    self.db.execute(
                        "UPDATE crm_segments SET filter_json = ? WHERE id = ?",
                        (_dumps(normalize_filter(flt)), segment["id"]),
                    )
        return self.list_tags(user)

    def tag_customers(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, scope = self._require(user, "crm")
        ids = [str(x) for x in dict.fromkeys(body.get("customerIds") or []) if x]
        if not ids:
            raise bad_request("Choose at least one customer.", "no_customers")
        if len(ids) > MAX_BULK:
            raise bad_request(f"Tag at most {MAX_BULK} customers at a time.", "too_many")
        customers = [self._customer_row(owner_id, scope, item) for item in ids]
        now = now_ms()
        with self.db.transaction():
            add_ids = []
            for item in body.get("add") or []:
                if isinstance(item, dict):
                    add_ids.append(self._tag_for_name(owner_id, str(item.get("name") or ""),
                                                      str(item.get("color") or "")))
                    continue
                text = str(item or "")
                known = self.db.scalar(
                    "SELECT id FROM crm_tags WHERE id = ? AND owner_id = ?", (text, owner_id)
                )
                add_ids.append(str(known) if known else self._tag_for_name(owner_id, text))
            remove_ids = [str(x) for x in body.get("remove") or [] if x]
            names = {
                r["id"]: r["name"]
                for r in self.db.query(
                    "SELECT id, name FROM crm_tags WHERE owner_id = ?", (owner_id,)
                )
            }
            for customer in customers:
                added, removed = [], []
                for tag_id in add_ids:
                    if self.db.execute(
                        "INSERT INTO crm_customer_tags (customer_id, tag_id, owner_id, created_at) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT (customer_id, tag_id) DO NOTHING",
                        (customer["id"], tag_id, owner_id, now),
                    ):
                        added.append(names.get(tag_id, ""))
                for tag_id in remove_ids:
                    if self.db.execute(
                        "DELETE FROM crm_customer_tags WHERE customer_id = ? AND tag_id = ?",
                        (customer["id"], tag_id),
                    ):
                        removed.append(names.get(tag_id, ""))
                if added or removed:
                    self._event(
                        owner_id,
                        "tag",
                        customer_id=customer["id"],
                        actor_id=_user_id(user),
                        data={"added": added, "removed": removed},
                        at=now,
                    )
        self.ctx.crm_changed(self._accounts_of(customers))
        return {"ok": True, "customers": self._hydrate(scope, [
            self._customer_row(owner_id, scope, c["id"]) for c in customers[:MAX_PAGE_SIZE]
        ])}

    def _accounts_of(self, customers: list[dict[str, Any]]) -> list[str]:
        ids = [c["id"] for c in customers]
        if not ids:
            return []
        return sorted(
            {
                r["account_id"]
                for r in self.db.query(
                    "SELECT DISTINCT account_id FROM crm_conversations "
                    f"WHERE customer_id IN ({_placeholders(ids)})",
                    ids,
                )
            }
        )

    # -- notes ---------------------------------------------------------------
    def add_note(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, scope = self._require(user, "crm")
        customer = self._customer_row(owner_id, scope, str(body.get("customerId") or ""))
        text = _clean(body.get("body"), 5000)
        if not text:
            raise bad_request("Write the note first.", "note_empty")
        now = now_ms()
        with self.db.transaction():
            note_id = new_id()
            self.db.execute(
                "INSERT INTO crm_notes (id, owner_id, customer_id, author_id, body, pinned, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (note_id, owner_id, customer["id"], _user_id(user), text,
                 1 if body.get("pinned") else 0, now, now),
            )
            self._event(owner_id, "note", customer_id=customer["id"], actor_id=_user_id(user),
                        data={"preview": _one_line(text, 120)}, at=now)
        return {"ok": True, "note": _public_note(self.db.query_one(
            "SELECT * FROM crm_notes WHERE id = ?", (note_id,)) or {})}

    def _note_for_edit(self, user: dict[str, Any] | None, note_id: str) -> tuple[str, dict[str, Any]]:
        owner_id, scope = self._require(user, "crm")
        note = self.db.query_one(
            "SELECT * FROM crm_notes WHERE id = ? AND owner_id = ?", (str(note_id or ""), owner_id)
        )
        if not note:
            raise not_found("Note not found.", "note_not_found")
        self._customer_row(owner_id, scope, note["customer_id"])
        if note["author_id"] != _user_id(user) and not self._can(user, "manage_crm"):
            raise forbidden("Only the author can change this note.", "note_not_yours")
        return owner_id, note

    def update_note(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        _owner_id, note = self._note_for_edit(user, str(body.get("id") or ""))
        updates: dict[str, Any] = {"updated_at": now_ms()}
        if "body" in body:
            text = _clean(body.get("body"), 5000)
            if not text:
                raise bad_request("Write the note first.", "note_empty")
            updates["body"] = text
        if "pinned" in body:
            updates["pinned"] = 1 if body.get("pinned") else 0
        self._update_row("crm_notes", note["id"], updates)
        return {"ok": True, "note": _public_note(self.db.query_one(
            "SELECT * FROM crm_notes WHERE id = ?", (note["id"],)) or {})}

    def delete_note(self, user: dict[str, Any] | None, note_id: str) -> dict[str, Any]:
        _owner_id, note = self._note_for_edit(user, note_id)
        self.db.execute("DELETE FROM crm_notes WHERE id = ?", (note["id"],))
        return {"ok": True, "id": note["id"]}

    # -- chats: assignment and status ----------------------------------------
    def _public_members(self, owner_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": str(m.get("id")),
                "name": str(m.get("name") or ""),
                "role": str(m.get("role") or ""),
                "accountIds": list(m.get("accountIds") or []),
                "active": bool(m.get("active", True)),
            }
            for m in self.ctx.crm_members(owner_id)
        ]

    def members(self, user: dict[str, Any] | None) -> dict[str, Any]:
        owner_id, _scope = self._require(user, "read")
        return {"ok": True, "members": self._public_members(owner_id), "me": _user_id(user)}

    def conversations_map(self, user: dict[str, Any] | None, account_id: str) -> dict[str, Any]:
        owner_id, scope = self._require(user, "read")
        account = self._account(owner_id, scope, account_id)
        rows = self.db.query(
            "SELECT v.id, v.chat_id, v.customer_id, v.status, v.assigned_to, v.unread, "
            "c.display_name FROM crm_conversations v JOIN crm_customers c ON c.id = v.customer_id "
            "WHERE v.account_id = ? AND v.owner_id = ?",
            (account["id"], owner_id),
        )
        tag_rows = self.db.query(
            "SELECT ct.customer_id, t.id, t.name, t.color FROM crm_customer_tags ct "
            "JOIN crm_tags t ON t.id = ct.tag_id JOIN crm_conversations v "
            "ON v.customer_id = ct.customer_id WHERE v.account_id = ? AND v.owner_id = ?",
            (account["id"], owner_id),
        )
        tags: dict[str, list[dict[str, Any]]] = {}
        for row in tag_rows:
            tags.setdefault(row["customer_id"], []).append(
                {"id": row["id"], "name": row["name"], "color": row["color"]}
            )
        return {
            "ok": True,
            "accountId": account["id"],
            "conversations": {
                row["chat_id"]: {
                    "conversationId": row["id"],
                    "customerId": row["customer_id"],
                    "customerName": row["display_name"],
                    "status": row["status"],
                    "assignedTo": row["assigned_to"],
                    "tags": tags.get(row["customer_id"], []),
                }
                for row in rows
            },
            "members": self._public_members(owner_id),
            "me": _user_id(user),
        }

    def _chat_conversation(
        self,
        owner_id: str,
        scope: list[str] | None,
        body: dict[str, Any],
        *,
        ensure: bool,
    ) -> dict[str, Any] | None:
        conversation_id = str(body.get("conversationId") or "")
        if conversation_id:
            return self._conversation_row(owner_id, scope, conversation_id)
        account = self._account(owner_id, scope, str(body.get("accountId") or ""))
        chat_id = str(body.get("chatId") or "")
        row = self.db.query_one(
            "SELECT * FROM crm_conversations WHERE account_id = ? AND chat_id = ? AND owner_id = ?",
            (account["id"], chat_id, owner_id),
        )
        if row or not ensure:
            return row
        channel = str(account.get("channel") or "line")
        external = chat_external_id(channel, chat_id)
        if not external:
            raise bad_request(
                "Customer records are kept for one-to-one chats only.", "chat_not_supported"
            )
        now = now_ms()
        with self.db.transaction():
            customer_id = self._customer_for_identity(
                owner_id,
                channel_family(channel),
                external,
                {"name": _one_line(body.get("name"), 120)},
                now,
            )
            self._new_conversation(
                owner_id, customer_id, channel, account["id"], chat_id, [], self.settings_for(owner_id), now
            )
        return self.db.query_one(
            "SELECT * FROM crm_conversations WHERE account_id = ? AND chat_id = ?",
            (account["id"], chat_id),
        )

    def chat_panel(self, user: dict[str, Any] | None, account_id: str, chat_id: str) -> dict[str, Any]:
        owner_id, scope = self._require(user, "read")
        conv = self._chat_conversation(
            owner_id, scope, {"accountId": account_id, "chatId": chat_id}, ensure=False
        )
        base = {
            "ok": True,
            "members": self._public_members(owner_id),
            "me": _user_id(user),
            "canEdit": self._can(user, "crm"),
            "canManage": self._can(user, "manage_crm"),
            "supported": bool(chat_external_id(
                str(self._account(owner_id, scope, account_id).get("channel") or "line"), chat_id
            )),
        }
        if not conv:
            return {**base, "conversation": None, "customer": None, "notes": [], "tasks": []}
        customer_row = self.db.query_one(
            "SELECT * FROM crm_customers WHERE id = ?", (conv["customer_id"],)
        )
        customer = self._hydrate(scope, [customer_row])[0] if customer_row else None
        notes = [
            _public_note(n)
            for n in self.db.query(
                "SELECT * FROM crm_notes WHERE customer_id = ? ORDER BY pinned DESC, "
                "created_at DESC LIMIT 5",
                (conv["customer_id"],),
            )
        ]
        tasks = [
            _public_task(t)
            for t in self.db.query(
                "SELECT * FROM crm_tasks WHERE customer_id = ? AND status = 'open' "
                "ORDER BY due_at LIMIT 20",
                (conv["customer_id"],),
            )
        ]
        return {
            **base,
            "conversation": _public_conversation(conv),
            "customer": customer,
            "notes": notes,
            "tasks": tasks,
        }

    def assign(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, scope = self._require(user, "crm")
        conv = self._chat_conversation(owner_id, scope, body, ensure=True)
        assert conv is not None
        me = _user_id(user)
        target = str(body.get("assignee") if body.get("assignee") is not None else "")
        if target == "me":
            target = me
        current = conv["assigned_to"]
        manage = self._can(user, "manage_crm")
        if target != me and not manage:
            if target or current != me:
                raise forbidden("Only a manager can hand chats to someone else.", "assign_forbidden")
        if target:
            member = self._member_map(owner_id).get(target)
            account_ids = set((member or {}).get("accountIds") or [])
            if not member or not member.get("active", True) or conv["account_id"] not in account_ids:
                raise bad_request("That person cannot reply from this account.", "assignee_invalid")
        if target == current:
            return self.chat_panel(user, conv["account_id"], conv["chat_id"])
        now = now_ms()
        updates: dict[str, Any] = {"assigned_to": target, "assigned_at": now if target else 0,
                                   "updated_at": now}
        if target and conv["status"] == "new":
            updates["status"] = "open"
        with self.db.transaction():
            self._update_row("crm_conversations", conv["id"], updates)
            self._event(owner_id, "assigned", customer_id=conv["customer_id"],
                        conversation_id=conv["id"], actor_id=me,
                        data={"from": current, "to": target}, at=now)
        if target and target != me:
            customer = self.db.query_one(
                "SELECT display_name FROM crm_customers WHERE id = ?", (conv["customer_id"],)
            )
            name = str((customer or {}).get("display_name") or "")
            self.ctx.crm_notify(
                target,
                {
                    "kind": "assigned",
                    "title": "มีแชทมอบหมายให้คุณ",
                    "body": name or "ลูกค้า",
                    "titleEn": "A chat was assigned to you",
                    "bodyEn": name or "Customer",
                    "tag": f"linepassport:assigned:{conv['id']}",
                    "accountId": conv["account_id"],
                    "mid": conv["chat_id"],
                },
            )
        self.ctx.crm_changed([conv["account_id"]])
        return self.chat_panel(user, conv["account_id"], conv["chat_id"])

    def set_status(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, scope = self._require(user, "crm")
        status = str(body.get("status") or "")
        if status not in STATUSES:
            raise bad_request("Unknown chat status.", "status_invalid")
        conv = self._chat_conversation(owner_id, scope, body, ensure=True)
        assert conv is not None
        if conv["status"] != status:
            now = now_ms()
            with self.db.transaction():
                self._update_row(
                    "crm_conversations",
                    conv["id"],
                    {"status": status, "closed_at": now if status == "closed" else 0,
                     "updated_at": now},
                )
                self._event(owner_id, "status", customer_id=conv["customer_id"],
                            conversation_id=conv["id"], actor_id=_user_id(user),
                            data={"from": conv["status"], "to": status}, at=now)
            self.ctx.crm_changed([conv["account_id"]])
        return self.chat_panel(user, conv["account_id"], conv["chat_id"])

    def system_assign(self, owner_id: str, conversation_id: str, user_id: str) -> bool:
        """Claim from outside the web UI (a Telegram button); caller checked access."""
        conv = self.db.query_one(
            "SELECT * FROM crm_conversations WHERE id = ? AND owner_id = ?",
            (conversation_id, owner_id),
        )
        if not conv or conv["assigned_to"] == user_id:
            return bool(conv)
        now = now_ms()
        updates: dict[str, Any] = {"assigned_to": user_id, "assigned_at": now, "updated_at": now}
        if conv["status"] in ("new", "closed"):
            updates["status"] = "open"
        with self.db.transaction():
            self._update_row("crm_conversations", conv["id"], updates)
            self._event(owner_id, "assigned", customer_id=conv["customer_id"],
                        conversation_id=conv["id"], actor_id=user_id,
                        data={"from": conv["assigned_to"], "to": user_id, "via": "telegram"},
                        at=now)
        self.ctx.crm_changed([conv["account_id"]])
        return True

    def system_set_status(self, owner_id: str, conversation_id: str, status: str, actor_id: str) -> bool:
        conv = self.db.query_one(
            "SELECT * FROM crm_conversations WHERE id = ? AND owner_id = ?",
            (conversation_id, owner_id),
        )
        if not conv or status not in STATUSES:
            return False
        if conv["status"] != status:
            now = now_ms()
            with self.db.transaction():
                self._update_row(
                    "crm_conversations",
                    conv["id"],
                    {"status": status, "closed_at": now if status == "closed" else 0,
                     "updated_at": now},
                )
                self._event(owner_id, "status", customer_id=conv["customer_id"],
                            conversation_id=conv["id"], actor_id=actor_id,
                            data={"from": conv["status"], "to": status, "via": "telegram"}, at=now)
            self.ctx.crm_changed([conv["account_id"]])
        return True

    # -- follow-up tasks -----------------------------------------------------
    def list_tasks(self, user: dict[str, Any] | None, params: dict[str, Any]) -> dict[str, Any]:
        owner_id, scope = self._require(user, "read")
        where, args = customer_where(owner_id, {}, scope, now_ms())
        clauses = ["t.owner_id = ?", f"(t.customer_id = '' OR EXISTS (SELECT 1 FROM crm_customers c "
                   f"WHERE c.id = t.customer_id AND {where}))"]
        params_sql: list[Any] = [owner_id, *args]
        if str(params.get("scope") or "mine") == "mine":
            clauses.append("t.assigned_to = ?")
            params_sql.append(_user_id(user))
        status = str(params.get("status") or "open")
        if status in TASK_STATUSES:
            clauses.append("t.status = ?")
            params_sql.append(status)
        customer_id = str(params.get("customerId") or "")
        if customer_id:
            clauses.append("t.customer_id = ?")
            params_sql.append(customer_id)
        order = "t.done_at DESC" if status == "done" else "CASE WHEN t.due_at = 0 THEN 1 ELSE 0 END, t.due_at, t.created_at"
        rows = self.db.query(
            "SELECT t.*, c.display_name AS customer_name FROM crm_tasks t "
            "LEFT JOIN crm_customers c ON c.id = t.customer_id "
            f"WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT 500",
            params_sql,
        )
        return {"ok": True, "tasks": [_public_task(r) for r in rows],
                "members": self._public_members(owner_id), "me": _user_id(user)}

    def create_task(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, scope = self._require(user, "crm")
        title = _one_line(body.get("title"), 200)
        if not title:
            raise bad_request("Give the task a title.", "task_title_required")
        customer_id = str(body.get("customerId") or "")
        conversation_id = ""
        if body.get("accountId") and body.get("chatId") and not customer_id:
            conv = self._chat_conversation(owner_id, scope, body, ensure=True)
            assert conv is not None
            customer_id, conversation_id = conv["customer_id"], conv["id"]
        if customer_id:
            self._customer_row(owner_id, scope, customer_id)
        assignee = self._task_assignee(owner_id, user, body.get("assignedTo"))
        now = now_ms()
        task_id = new_id()
        due = max(0, _int(body.get("dueAt")))
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO crm_tasks (id, owner_id, customer_id, conversation_id, title, body, "
                "due_at, assigned_to, status, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
                (task_id, owner_id, customer_id, conversation_id, title,
                 _clean(body.get("body"), 2000), due, assignee, _user_id(user), now, now),
            )
            if customer_id:
                self._event(owner_id, "task", customer_id=customer_id, actor_id=_user_id(user),
                            data={"title": title, "dueAt": due, "assignedTo": assignee}, at=now)
        if assignee and assignee != _user_id(user):
            self.ctx.crm_notify(assignee, {
                "kind": "task_assigned",
                "title": "มีงานติดตามใหม่",
                "body": title,
                "titleEn": "New follow-up task",
                "bodyEn": title,
                "tag": f"linepassport:task:{task_id}",
            })
        return {"ok": True, "task": self._task(owner_id, task_id)}

    def _task_assignee(self, owner_id: str, user: dict[str, Any] | None, value: Any) -> str:
        target = str(value if value is not None else "me")
        if target == "me":
            return _user_id(user)
        if not target:
            return ""
        member = self._member_map(owner_id).get(target)
        if not member or not member.get("active", True):
            raise bad_request("Choose someone from your team.", "assignee_invalid")
        return target

    def _task(self, owner_id: str, task_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT t.*, c.display_name AS customer_name FROM crm_tasks t "
            "LEFT JOIN crm_customers c ON c.id = t.customer_id WHERE t.id = ? AND t.owner_id = ?",
            (task_id, owner_id),
        )
        if not row:
            raise not_found("Task not found.", "task_not_found")
        return _public_task(row)

    def _task_for_edit(self, user: dict[str, Any] | None, task_id: str) -> tuple[str, dict[str, Any]]:
        owner_id, scope = self._require(user, "crm")
        task = self.db.query_one(
            "SELECT * FROM crm_tasks WHERE id = ? AND owner_id = ?", (str(task_id or ""), owner_id)
        )
        if not task:
            raise not_found("Task not found.", "task_not_found")
        if task["customer_id"]:
            self._customer_row(owner_id, scope, task["customer_id"])
        return owner_id, task

    def update_task(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, task = self._task_for_edit(user, str(body.get("id") or ""))
        now = now_ms()
        updates: dict[str, Any] = {"updated_at": now}
        if "title" in body:
            title = _one_line(body.get("title"), 200)
            if not title:
                raise bad_request("Give the task a title.", "task_title_required")
            updates["title"] = title
        if "body" in body:
            updates["body"] = _clean(body.get("body"), 2000)
        if "dueAt" in body:
            updates["due_at"] = max(0, _int(body.get("dueAt")))
            updates["reminded_at"] = 0
        if "assignedTo" in body:
            updates["assigned_to"] = self._task_assignee(owner_id, user, body.get("assignedTo"))
        if "status" in body:
            status = str(body.get("status") or "")
            if status not in TASK_STATUSES:
                raise bad_request("Unknown task status.", "status_invalid")
            updates["status"] = status
            updates["done_at"] = now if status == "done" else 0
        with self.db.transaction():
            self._update_row("crm_tasks", task["id"], updates)
            if updates.get("status") == "done" and task["status"] != "done" and task["customer_id"]:
                self._event(owner_id, "task_done", customer_id=task["customer_id"],
                            actor_id=_user_id(user), data={"title": task["title"]}, at=now)
        new_assignee = updates.get("assigned_to")
        if new_assignee and new_assignee != task["assigned_to"] and new_assignee != _user_id(user):
            self.ctx.crm_notify(new_assignee, {
                "kind": "task_assigned",
                "title": "มีงานติดตามใหม่",
                "body": str(updates.get("title") or task["title"]),
                "titleEn": "New follow-up task",
                "bodyEn": str(updates.get("title") or task["title"]),
                "tag": f"linepassport:task:{task['id']}",
            })
        return {"ok": True, "task": self._task(owner_id, task["id"])}

    def delete_task(self, user: dict[str, Any] | None, task_id: str) -> dict[str, Any]:
        _owner_id, task = self._task_for_edit(user, task_id)
        me = _user_id(user)
        if me not in {task["created_by"], task["assigned_to"]} and not self._can(user, "manage_crm"):
            raise forbidden("Only the creator, the assignee or a manager can delete this task.",
                            "task_not_yours")
        self.db.execute("DELETE FROM crm_tasks WHERE id = ?", (task["id"],))
        return {"ok": True, "id": task["id"]}

    def due_task_reminders(self, now: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Open tasks that just came due; marks them reminded so each fires once."""
        now = now or now_ms()
        rows = self.db.query(
            "SELECT t.*, c.display_name AS customer_name FROM crm_tasks t "
            "LEFT JOIN crm_customers c ON c.id = t.customer_id "
            "WHERE t.status = 'open' AND t.due_at > 0 AND t.due_at <= ? AND t.reminded_at = 0 "
            "ORDER BY t.due_at LIMIT ?",
            (now, limit),
        )
        for row in rows:
            self.db.execute("UPDATE crm_tasks SET reminded_at = ? WHERE id = ?", (now, row["id"]))
        return [{**_public_task(row), "ownerId": row["owner_id"]} for row in rows]

    # -- segments ------------------------------------------------------------
    def _count(self, owner_id: str, scope: list[str] | None, flt: dict[str, Any]) -> int:
        where, args = customer_where(owner_id, flt, scope, now_ms())
        return _int(self.db.scalar(f"SELECT COUNT(*) FROM crm_customers c WHERE {where}", args))

    def list_segments(self, user: dict[str, Any] | None) -> dict[str, Any]:
        owner_id, scope = self._require(user, "read")
        rows = self.db.query(
            "SELECT * FROM crm_segments WHERE owner_id = ? ORDER BY LOWER(name)", (owner_id,)
        )
        segments = []
        for row in rows:
            flt = normalize_filter(_loads(row["filter_json"]))
            segments.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "filter": flt,
                    "count": self._count(owner_id, scope, flt),
                    "createdBy": row["created_by"],
                    "updatedAt": _int(row["updated_at"]),
                }
            )
        return {"ok": True, "segments": segments, "canManage": self._can(user, "manage_crm")}

    def save_segment(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, _scope = self._require(user, "manage_crm")
        name = _one_line(body.get("name"), 80)
        if not name:
            raise bad_request("Give the segment a name.", "segment_name_required")
        flt = normalize_filter(body.get("filter"))
        now = now_ms()
        segment_id = str(body.get("id") or "")
        if segment_id:
            updated = self.db.execute(
                "UPDATE crm_segments SET name = ?, filter_json = ?, updated_at = ? "
                "WHERE id = ? AND owner_id = ?",
                (name, _dumps(flt), now, segment_id, owner_id),
            )
            if not updated:
                raise not_found("Segment not found.", "segment_not_found")
        else:
            count = _int(self.db.scalar(
                "SELECT COUNT(*) FROM crm_segments WHERE owner_id = ?", (owner_id,)
            ))
            if count >= MAX_SEGMENTS:
                raise bad_request(f"A shop can save at most {MAX_SEGMENTS} segments.",
                                  "too_many_segments")
            segment_id = new_id()
            self.db.execute(
                "INSERT INTO crm_segments (id, owner_id, name, filter_json, created_by, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (segment_id, owner_id, name, _dumps(flt), _user_id(user), now, now),
            )
        return {"ok": True, "id": segment_id, **self.list_segments(user)}

    def delete_segment(self, user: dict[str, Any] | None, segment_id: str) -> dict[str, Any]:
        owner_id, _scope = self._require(user, "manage_crm")
        if not self.db.execute(
            "DELETE FROM crm_segments WHERE id = ? AND owner_id = ?", (str(segment_id or ""), owner_id)
        ):
            raise not_found("Segment not found.", "segment_not_found")
        return self.list_segments(user)

    def count_filter(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, scope = self._require(user, "read")
        flt = self._filter_from(owner_id, body)
        return {"ok": True, "count": self._count(owner_id, scope, flt), "filter": flt}

    # -- settings and summary ------------------------------------------------
    def get_settings(self, user: dict[str, Any] | None) -> dict[str, Any]:
        owner_id, _scope = self._require(user, "read")
        return {"ok": True, "settings": self.settings_for(owner_id),
                "canEdit": self._can(user, "manage_crm")}

    def save_settings(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, _scope = self._require(user, "manage_crm")
        nested = body.get("settings")
        raw: dict[str, Any] = nested if isinstance(nested, dict) else body
        settings = self.settings_for(owner_id)
        if "autoCloseDays" in raw:
            days = _int(raw.get("autoCloseDays"), -1)
            if not 0 <= days <= 60:
                raise bad_request("Auto-close must be between 0 and 60 days.", "settings_invalid")
            settings["autoCloseDays"] = days
        if "autoAssignOnReply" in raw:
            settings["autoAssignOnReply"] = bool(raw.get("autoAssignOnReply"))
        self.db.execute(
            "INSERT INTO crm_settings (owner_id, data, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (owner_id) DO UPDATE SET data = excluded.data, "
            "updated_at = excluded.updated_at",
            (owner_id, _dumps(settings), now_ms()),
        )
        return self.get_settings(user)

    def summary(self, user: dict[str, Any] | None, due_before: int = 0) -> dict[str, Any]:
        owner_id, scope = self._require(user, "read")
        me = _user_id(user)
        scope_sql = ""
        scope_args: list[Any] = []
        if scope is not None:
            scope_sql = f" AND account_id IN ({_placeholders(scope) or 'NULL'})"
            scope_args = list(scope)
        mine = _int(self.db.scalar(
            "SELECT COUNT(*) FROM crm_conversations WHERE owner_id = ? AND assigned_to = ? "
            f"AND status IN ('new', 'open'){scope_sql}",
            [owner_id, me, *scope_args],
        ))
        unassigned = _int(self.db.scalar(
            "SELECT COUNT(*) FROM crm_conversations WHERE owner_id = ? AND assigned_to = '' "
            f"AND status = 'new'{scope_sql}",
            [owner_id, *scope_args],
        ))
        due = _int(self.db.scalar(
            "SELECT COUNT(*) FROM crm_tasks WHERE owner_id = ? AND assigned_to = ? "
            "AND status = 'open' AND due_at > 0 AND due_at <= ?",
            (owner_id, me, due_before or now_ms()),
        ))
        return {"ok": True, "myOpen": mine, "unassigned": unassigned, "tasksDue": due}

    # -- messages to a segment (broadcasts) ----------------------------------
    def _broadcast_targets(
        self, owner_id: str, scope: list[str] | None, flt: dict[str, Any], account_id: str
    ) -> list[dict[str, Any]]:
        """Customers in the filter who already chat with this account, newest first."""
        where, args = customer_where(owner_id, flt, scope, now_ms())
        return self.db.query(
            "SELECT c.id AS customer_id, c.display_name, v.id AS conversation_id, v.chat_id "
            "FROM crm_customers c JOIN crm_conversations v "
            "ON v.customer_id = c.id AND v.account_id = ? "
            f"WHERE {where} ORDER BY c.last_message_at DESC LIMIT ?",
            [account_id, *args, MAX_BROADCAST_RECIPIENTS + 1],
        )

    def preview_broadcast(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, scope = self._require(user, "manage_crm")
        account = self._account(owner_id, scope, str(body.get("accountId") or ""))
        flt = self._filter_from(owner_id, body)
        targets = self._broadcast_targets(owner_id, scope, flt, account["id"])
        return {
            "ok": True,
            "accountId": account["id"],
            "channel": account.get("channel") or "line",
            "inSegment": self._count(owner_id, scope, flt),
            "reachable": min(len(targets), MAX_BROADCAST_RECIPIENTS),
            "tooMany": len(targets) > MAX_BROADCAST_RECIPIENTS,
            "sampleName": str(targets[0]["display_name"]) if targets else "",
            "limit": MAX_BROADCAST_RECIPIENTS,
        }

    def create_broadcast(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, scope = self._require(user, "manage_crm")
        account = self._account(owner_id, scope, str(body.get("accountId") or ""))
        text = _clean(body.get("text"), 2000)
        if not text:
            raise bad_request("Write the message first.", "broadcast_text_required")
        flt = self._filter_from(owner_id, body)
        targets = self._broadcast_targets(owner_id, scope, flt, account["id"])
        if not targets:
            raise bad_request(
                "Nobody in this segment chats with that account yet.", "broadcast_nobody"
            )
        if len(targets) > MAX_BROADCAST_RECIPIENTS:
            raise bad_request(
                f"Send to at most {MAX_BROADCAST_RECIPIENTS} people at a time.",
                "broadcast_too_many",
            )
        segment_name = ""
        if body.get("segmentId"):
            segment_name = str(
                self.db.scalar(
                    "SELECT name FROM crm_segments WHERE id = ? AND owner_id = ?",
                    (str(body.get("segmentId")), owner_id),
                )
                or ""
            )
        now = now_ms()
        broadcast_id = new_id()
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO crm_broadcasts (id, owner_id, segment_id, name, account_id, text, "
                "status, created_by, created_at, updated_at, started_at, next_at, total) "
                "VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)",
                (
                    broadcast_id,
                    owner_id,
                    str(body.get("segmentId") or ""),
                    _one_line(body.get("name"), 80) or segment_name,
                    account["id"],
                    text,
                    _user_id(user),
                    now,
                    now,
                    now,
                    now,
                    len(targets),
                ),
            )
            for position, target in enumerate(targets):
                self.db.execute(
                    "INSERT INTO crm_broadcast_recipients (broadcast_id, customer_id, "
                    "conversation_id, chat_id, name, position) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        broadcast_id,
                        target["customer_id"],
                        target["conversation_id"],
                        target["chat_id"],
                        target["display_name"],
                        position,
                    ),
                )
        return {"ok": True, "broadcast": self._broadcast(owner_id, broadcast_id)}

    def _broadcast(self, owner_id: str, broadcast_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM crm_broadcasts WHERE id = ? AND owner_id = ?", (broadcast_id, owner_id)
        )
        if not row:
            raise not_found("Message not found.", "broadcast_not_found")
        return _public_broadcast(row)

    def list_broadcasts(self, user: dict[str, Any] | None) -> dict[str, Any]:
        owner_id, scope = self._require(user, "read")
        rows = self.db.query(
            "SELECT * FROM crm_broadcasts WHERE owner_id = ? ORDER BY created_at DESC LIMIT 50",
            (owner_id,),
        )
        visible = [r for r in rows if scope is None or r["account_id"] in scope]
        return {
            "ok": True,
            "broadcasts": [_public_broadcast(r) for r in visible],
            "canManage": self._can(user, "manage_crm"),
        }

    def control_broadcast(self, user: dict[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        owner_id, _scope = self._require(user, "manage_crm")
        broadcast = self._broadcast(owner_id, str(body.get("id") or ""))
        action = str(body.get("action") or "")
        moves = {
            "pause": ({"running"}, "paused"),
            "resume": ({"paused"}, "running"),
            "cancel": ({"running", "paused"}, "cancelled"),
        }
        if action not in moves:
            raise bad_request("Unknown action.", "broadcast_action_invalid")
        allowed, target = moves[action]
        if broadcast["status"] not in allowed:
            raise CrmError(409, "This message cannot do that now.", "broadcast_state")
        now = now_ms()
        updates: dict[str, Any] = {"status": target, "updated_at": now, "error": ""}
        if target == "running":
            updates["next_at"] = now
        if target == "cancelled":
            updates["finished_at"] = now
        self._update_row("crm_broadcasts", broadcast["id"], updates)
        return {"ok": True, "broadcast": self._broadcast(owner_id, broadcast["id"])}

    def due_broadcasts(self, now: int | None = None) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM crm_broadcasts WHERE status = 'running' AND next_at <= ? "
            "ORDER BY next_at LIMIT 20",
            (now or now_ms(),),
        )

    def next_broadcast_recipient(self, broadcast_id: str) -> dict[str, Any] | None:
        return self.db.query_one(
            "SELECT * FROM crm_broadcast_recipients WHERE broadcast_id = ? AND status = 'pending' "
            "ORDER BY position LIMIT 1",
            (broadcast_id,),
        )

    def finish_broadcast_recipient(
        self, broadcast_id: str, customer_id: str, status: str, error: str = ""
    ) -> None:
        counter = {"sent": "sent", "failed": "failed", "skipped": "skipped"}[status]
        now = now_ms()
        with self.db.transaction():
            self.db.execute(
                "UPDATE crm_broadcast_recipients SET status = ?, sent_at = ?, error = ? "
                "WHERE broadcast_id = ? AND customer_id = ?",
                (status, now, error[:300], broadcast_id, customer_id),
            )
            self.db.execute(
                f"UPDATE crm_broadcasts SET {counter} = {counter} + 1, updated_at = ? WHERE id = ?",
                (now, broadcast_id),
            )
            if not self.next_broadcast_recipient(broadcast_id):
                self.db.execute(
                    "UPDATE crm_broadcasts SET status = 'done', finished_at = ?, next_at = 0 "
                    "WHERE id = ? AND status = 'running'",
                    (now, broadcast_id),
                )

    def delay_broadcast(self, broadcast_id: str, next_at: int, note: str = "") -> None:
        self.db.execute(
            "UPDATE crm_broadcasts SET next_at = ?, error = ?, updated_at = ? WHERE id = ?",
            (next_at, note[:300], now_ms(), broadcast_id),
        )

    def stop_broadcast(self, broadcast_id: str, reason: str) -> None:
        """Pause a running message because sending cannot continue (e.g. signed out)."""
        self.db.execute(
            "UPDATE crm_broadcasts SET status = 'paused', error = ?, updated_at = ? "
            "WHERE id = ? AND status = 'running'",
            (reason[:300], now_ms(), broadcast_id),
        )

    # -- housekeeping (engine) -----------------------------------------------
    def auto_close(self, now: int | None = None, limit: int = 500) -> list[str]:
        """Close chats that have been quiet for the shop's auto-close period."""
        now = now or now_ms()
        touched: set[str] = set()
        owners = [
            r["owner_id"]
            for r in self.db.query(
                "SELECT DISTINCT owner_id FROM crm_conversations WHERE status <> 'closed'"
            )
        ]
        for owner_id in owners:
            days = _int(self.settings_for(owner_id).get("autoCloseDays"))
            if not days:
                continue
            rows = self.db.query(
                "SELECT id, customer_id, account_id, status FROM crm_conversations "
                "WHERE owner_id = ? AND status <> 'closed' AND last_message_at > 0 "
                "AND last_message_at < ? LIMIT ?",
                (owner_id, now - days * DAY_MS, limit),
            )
            if not rows:
                continue
            with self.db.transaction():
                for row in rows:
                    self._update_row("crm_conversations", row["id"],
                                     {"status": "closed", "closed_at": now, "updated_at": now})
                    self._event(owner_id, "status", customer_id=row["customer_id"],
                                conversation_id=row["id"], actor_id="system",
                                data={"from": row["status"], "to": "closed", "reason": "quiet"},
                                at=now)
                    touched.add(row["account_id"])
        return sorted(touched)

    def identities_missing_names(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT i.owner_id, i.channel, i.external_id, MIN(v.account_id) AS account_id "
            "FROM crm_identities i JOIN crm_conversations v ON v.customer_id = i.customer_id "
            "AND v.channel = 'line' WHERE i.channel = 'line' AND i.name = '' "
            "GROUP BY i.owner_id, i.channel, i.external_id LIMIT ?",
            (limit,),
        )

    def set_identity_profile(
        self, owner_id: str, channel: str, external_id: str, *, name: str, picture: str = ""
    ) -> None:
        with self.db.transaction():
            self._update_identity(
                owner_id, channel_family(channel), external_id,
                {"name": name, "picture": picture}, now_ms(),
            )


def _customer_values(body: dict[str, Any], *, require_name: bool) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "displayName" in body or require_name:
        name = _one_line(body.get("displayName"), 120)
        if require_name and not name:
            raise bad_request("Customer name is required.", "customer_name_required")
        values["display_name"] = name
    if "phone" in body:
        values["phone"] = _one_line(body.get("phone"), 40)
    if "email" in body:
        email = _one_line(body.get("email"), 160)
        if email and ("@" not in email or " " in email):
            raise bad_request("That email address does not look right.", "email_invalid")
        values["email"] = email
    if "company" in body:
        values["company"] = _one_line(body.get("company"), 160)
    if "fields" in body:
        raw = body.get("fields")
        if not isinstance(raw, dict):
            raise bad_request("Extra fields must be name/value pairs.", "fields_invalid")
        fields: dict[str, str] = {}
        for key, value in raw.items():
            name = _one_line(key, 40)
            if name:
                fields[name] = _clean(value, 500)
            if len(fields) >= MAX_FIELDS:
                break
        values["fields"] = fields
    return values


def _public_customer(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "displayName": row["display_name"],
        "phone": row["phone"],
        "email": row["email"],
        "company": row["company"],
        "fields": _loads(row["fields"]),
        "picture": row["picture"],
        "createdAt": _int(row["created_at"]),
        "updatedAt": _int(row["updated_at"]),
        "lastInboundAt": _int(row["last_inbound_at"]),
        "lastOutboundAt": _int(row["last_outbound_at"]),
        "lastMessageAt": _int(row["last_message_at"]),
        "lastPreview": row["last_preview"],
    }


def _public_conversation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "accountId": row["account_id"],
        "chatId": row["chat_id"],
        "channel": row["channel"],
        "status": row["status"],
        "assignedTo": row["assigned_to"],
        "lastMessageAt": _int(row["last_message_at"]),
        "unread": _int(row["unread"]),
    }


def _public_note(row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": row["id"],
        "customerId": row["customer_id"],
        "authorId": row["author_id"],
        "body": row["body"],
        "pinned": bool(row["pinned"]),
        "createdAt": _int(row["created_at"]),
        "updatedAt": _int(row["updated_at"]),
    }


def _public_task(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "customerId": row["customer_id"],
        "customerName": row.get("customer_name") or "",
        "conversationId": row["conversation_id"],
        "title": row["title"],
        "body": row["body"],
        "dueAt": _int(row["due_at"]),
        "assignedTo": row["assigned_to"],
        "status": row["status"],
        "createdBy": row["created_by"],
        "createdAt": _int(row["created_at"]),
        "doneAt": _int(row["done_at"]),
    }


def _public_broadcast(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "segmentId": row["segment_id"],
        "accountId": row["account_id"],
        "text": row["text"],
        "status": row["status"],
        "total": _int(row["total"]),
        "sent": _int(row["sent"]),
        "failed": _int(row["failed"]),
        "skipped": _int(row["skipped"]),
        "createdBy": row["created_by"],
        "createdAt": _int(row["created_at"]),
        "finishedAt": _int(row["finished_at"]),
        "nextAt": _int(row["next_at"]),
        "note": row["error"],
    }


def _color(value: str) -> str:
    value = str(value or "").strip()
    if len(value) == 7 and value.startswith("#") and all(
        ch in "0123456789abcdefABCDEF" for ch in value[1:]
    ):
        return value.lower()
    return ""


def _csv_safe(value: str) -> str:
    # Spreadsheet apps run cells that start with these as formulas.
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def _iso(ms: int) -> str:
    if not ms:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000))
