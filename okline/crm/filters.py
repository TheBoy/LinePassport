"""Customer filters, shared by the customer list and saved segments.

A filter is plain JSON so it can be stored on a segment and sent from the
browser unchanged. ``customer_where`` turns it into a SQL WHERE clause over
``crm_customers c``.
"""

from __future__ import annotations

from typing import Any

CHANNELS = ("line", "telegram")
STATUSES = ("new", "open", "pending", "closed")
SORTS = {
    "recent": "c.last_message_at DESC, c.created_at DESC",
    "name": "LOWER(c.display_name) ASC, c.created_at DESC",
    "created": "c.created_at DESC",
    "waiting": "c.last_inbound_at DESC",
}
UNASSIGNED = "none"
DAY_MS = 86_400_000


def _id_list(value: Any, limit: int = 100) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and len(text) <= 120 and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return 0
    return days if 0 < days <= 3650 else 0


def normalize_filter(raw: Any) -> dict[str, Any]:
    """Keep only known keys with sane values; unknown input becomes "everyone"."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    query = " ".join(str(raw.get("q") or "").split())[:200]
    if query:
        out["q"] = query
    for key in ("tagsAny", "tagsAll", "tagsNone", "accountIds", "assignedTo"):
        values = _id_list(raw.get(key))
        if values:
            out[key] = values
    channels = [item for item in _id_list(raw.get("channels")) if item in CHANNELS]
    if channels:
        out["channels"] = channels
    statuses = [item for item in _id_list(raw.get("statuses")) if item in STATUSES]
    if statuses:
        out["statuses"] = statuses
    last = raw.get("lastContact")
    if isinstance(last, dict):
        within, older = _days(last.get("within")), _days(last.get("olderThan"))
        if within:
            out["lastContact"] = {"within": within}
        elif older:
            out["lastContact"] = {"olderThan": older}
    created = _days(raw.get("createdWithinDays"))
    if created:
        out["createdWithinDays"] = created
    for key in ("hasPhone", "hasOpenTask"):
        if raw.get(key) is True:
            out[key] = True
    return out


def _placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def _like(text: str) -> str:
    escaped = text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def customer_where(
    owner_id: str,
    flt: dict[str, Any],
    scope: list[str] | None,
    now_ms: int,
) -> tuple[str, list[Any]]:
    """WHERE clause for ``crm_customers c``.

    ``scope`` is the list of account ids the viewer may see (``None`` = every
    account of the shop). Customers with no chat at all (added by hand) belong
    to the whole shop and are always visible.
    """
    clauses = ["c.owner_id = ?"]
    params: list[Any] = [owner_id]

    if scope is not None:
        if scope:
            clauses.append(
                "(EXISTS (SELECT 1 FROM crm_conversations sv WHERE sv.customer_id = c.id "
                f"AND sv.account_id IN ({_placeholders(scope)})) OR NOT EXISTS "
                "(SELECT 1 FROM crm_conversations sn WHERE sn.customer_id = c.id))"
            )
            params.extend(scope)
        else:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM crm_conversations sn WHERE sn.customer_id = c.id)"
            )

    if flt.get("q"):
        clauses.append("c.search LIKE ? ESCAPE '\\'")
        params.append(_like(str(flt["q"])))

    for tag_id in flt.get("tagsAll") or []:
        clauses.append(
            "EXISTS (SELECT 1 FROM crm_customer_tags ta WHERE ta.customer_id = c.id "
            "AND ta.tag_id = ?)"
        )
        params.append(tag_id)
    if flt.get("tagsAny"):
        values = list(flt["tagsAny"])
        clauses.append(
            "EXISTS (SELECT 1 FROM crm_customer_tags ty WHERE ty.customer_id = c.id "
            f"AND ty.tag_id IN ({_placeholders(values)}))"
        )
        params.extend(values)
    if flt.get("tagsNone"):
        values = list(flt["tagsNone"])
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM crm_customer_tags tn WHERE tn.customer_id = c.id "
            f"AND tn.tag_id IN ({_placeholders(values)}))"
        )
        params.extend(values)

    if flt.get("channels"):
        values = list(flt["channels"])
        clauses.append(
            "EXISTS (SELECT 1 FROM crm_identities ci WHERE ci.customer_id = c.id "
            f"AND ci.channel IN ({_placeholders(values)}))"
        )
        params.extend(values)

    # Chat-level conditions must all hold for the same chat.
    conv: list[str] = []
    conv_params: list[Any] = []
    if flt.get("accountIds"):
        values = list(flt["accountIds"])
        conv.append(f"cv.account_id IN ({_placeholders(values)})")
        conv_params.extend(values)
    if scope is not None and (flt.get("statuses") or flt.get("assignedTo") or conv):
        conv.append(f"cv.account_id IN ({_placeholders(scope) or 'NULL'})")
        conv_params.extend(scope)
    if flt.get("statuses"):
        values = list(flt["statuses"])
        conv.append(f"cv.status IN ({_placeholders(values)})")
        conv_params.extend(values)
    if flt.get("assignedTo"):
        values = list(flt["assignedTo"])
        people = [item for item in values if item != UNASSIGNED]
        parts = []
        if people:
            parts.append(f"cv.assigned_to IN ({_placeholders(people)})")
            conv_params.extend(people)
        if UNASSIGNED in values:
            parts.append("cv.assigned_to = ''")
        conv.append("(" + " OR ".join(parts) + ")")
    if conv:
        clauses.append(
            "EXISTS (SELECT 1 FROM crm_conversations cv WHERE cv.customer_id = c.id AND "
            + " AND ".join(conv)
            + ")"
        )
        params.extend(conv_params)

    last = flt.get("lastContact") or {}
    if last.get("within"):
        clauses.append("c.last_message_at >= ?")
        params.append(now_ms - int(last["within"]) * DAY_MS)
    elif last.get("olderThan"):
        clauses.append("c.last_message_at > 0 AND c.last_message_at < ?")
        params.append(now_ms - int(last["olderThan"]) * DAY_MS)
    if flt.get("createdWithinDays"):
        clauses.append("c.created_at >= ?")
        params.append(now_ms - int(flt["createdWithinDays"]) * DAY_MS)
    if flt.get("hasPhone"):
        clauses.append("c.phone <> ''")
    if flt.get("hasOpenTask"):
        clauses.append(
            "EXISTS (SELECT 1 FROM crm_tasks tk WHERE tk.customer_id = c.id "
            "AND tk.status = 'open')"
        )
    return " AND ".join(clauses), params
