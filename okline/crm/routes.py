"""HTTP routes for the CRM (``/api/crm/*``).

The web handler authenticates the user, enforces rate limits and parses the
query string, then calls :func:`dispatch`. Permission checks live in the service.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .errors import bad_request
from .service import CrmService

NOT_HANDLED = object()
Query = dict[str, list[str]]


def _q(query: Query, key: str, default: str = "") -> str:
    values = query.get(key) or []
    return str(values[-1]) if values else default


def _q_int(query: Query, key: str, default: int = 0) -> int:
    try:
        return int(_q(query, key, str(default)))
    except ValueError:
        return default


def export_params(query: Query) -> dict[str, Any]:
    raw = _q(query, "filter")
    try:
        flt = json.loads(raw) if raw else {}
    except ValueError as exc:
        raise bad_request("The filter is not valid JSON.", "filter_invalid") from exc
    return {"filter": flt if isinstance(flt, dict) else {}, "segmentId": _q(query, "segmentId")}


def dispatch(
    service: CrmService,
    user: dict[str, Any] | None,
    method: str,
    path: str,
    query: Query,
    read_json: Callable[[], dict[str, Any]],
) -> Any:
    if method == "GET":
        if path == "/api/crm/summary":
            return service.summary(user, _q_int(query, "dueBefore"))
        if path == "/api/crm/customer":
            return service.customer_detail(user, _q(query, "id"))
        if path == "/api/crm/timeline":
            return service.timeline(
                user, _q(query, "customerId"), _q_int(query, "before"), _q_int(query, "limit", 60)
            )
        if path == "/api/crm/tags":
            return service.list_tags(user)
        if path == "/api/crm/members":
            return service.members(user)
        if path == "/api/crm/conversations":
            return service.conversations_map(user, _q(query, "accountId"))
        if path == "/api/crm/chat":
            return service.chat_panel(user, _q(query, "accountId"), _q(query, "chatId"))
        if path == "/api/crm/tasks":
            return service.list_tasks(
                user,
                {
                    "scope": _q(query, "scope", "mine"),
                    "status": _q(query, "status", "open"),
                    "customerId": _q(query, "customerId"),
                },
            )
        if path == "/api/crm/segments":
            return service.list_segments(user)
        if path == "/api/crm/settings":
            return service.get_settings(user)
        return NOT_HANDLED
    if method != "POST":
        return NOT_HANDLED
    routes: dict[str, Callable[[dict[str, Any]], Any]] = {
        "/api/crm/customers/search": lambda body: service.list_customers(user, body),
        "/api/crm/customers/create": lambda body: service.create_customer(user, body),
        "/api/crm/customers/update": lambda body: service.update_customer(user, body),
        "/api/crm/customers/delete": lambda body: service.delete_customer(
            user, str(body.get("id") or "")
        ),
        "/api/crm/customers/merge": lambda body: service.merge_customers(user, body),
        "/api/crm/customers/tags": lambda body: service.tag_customers(user, body),
        "/api/crm/tags/create": lambda body: service.create_tag(user, body),
        "/api/crm/tags/update": lambda body: service.update_tag(user, body),
        "/api/crm/tags/delete": lambda body: service.delete_tag(user, str(body.get("id") or "")),
        "/api/crm/notes/create": lambda body: service.add_note(user, body),
        "/api/crm/notes/update": lambda body: service.update_note(user, body),
        "/api/crm/notes/delete": lambda body: service.delete_note(user, str(body.get("id") or "")),
        "/api/crm/chat/assign": lambda body: service.assign(user, body),
        "/api/crm/chat/status": lambda body: service.set_status(user, body),
        "/api/crm/tasks/create": lambda body: service.create_task(user, body),
        "/api/crm/tasks/update": lambda body: service.update_task(user, body),
        "/api/crm/tasks/delete": lambda body: service.delete_task(user, str(body.get("id") or "")),
        "/api/crm/segments/save": lambda body: service.save_segment(user, body),
        "/api/crm/segments/delete": lambda body: service.delete_segment(
            user, str(body.get("id") or "")
        ),
        "/api/crm/segments/count": lambda body: service.count_filter(user, body),
        "/api/crm/settings": lambda body: service.save_settings(user, body),
    }
    handler = routes.get(path)
    if handler is None:
        return NOT_HANDLED
    return handler(read_json())


# Per-route limits merged into the web app's authenticated rate-limit table.
RATE_LIMITS: dict[tuple[str, str], tuple[int, float]] = {
    ("POST", "/api/crm/customers/search"): (120, 60.0),
    ("POST", "/api/crm/customers/create"): (60, 60.0),
    ("POST", "/api/crm/customers/merge"): (30, 60.0),
    ("POST", "/api/crm/customers/delete"): (60, 60.0),
    ("POST", "/api/crm/customers/tags"): (60, 60.0),
    ("GET", "/api/crm/customers/export"): (6, 60.0),
    ("POST", "/api/crm/notes/create"): (60, 60.0),
    ("POST", "/api/crm/tasks/create"): (60, 60.0),
    ("POST", "/api/crm/segments/count"): (120, 60.0),
}
