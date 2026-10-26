"""HTTP routes for Telegram (``/api/telegram/*``)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from .errors import TelegramError
from .notify import StaffNotifier

NOT_HANDLED = object()


class RouteHost(Protocol):
    def crm_tenant_id(self, user: dict[str, Any] | None) -> str: ...

    def telegram_can_manage(self, user: dict[str, Any] | None) -> bool: ...


def _user_id(user: dict[str, Any] | None) -> str:
    return str((user or {}).get("id") or "")


def dispatch_notify(
    notifier: StaffNotifier,
    host: RouteHost,
    user: dict[str, Any] | None,
    method: str,
    path: str,
    read_json: Callable[[], dict[str, Any]],
) -> Any:
    owner_id = host.crm_tenant_id(user)
    if not owner_id:
        raise TelegramError(403, "Permission denied.", "forbidden")
    can_manage = host.telegram_can_manage(user)

    def settings() -> dict[str, Any]:
        return notifier.settings(owner_id, _user_id(user), can_manage=can_manage)

    def require_manager() -> None:
        if not can_manage:
            raise TelegramError(403, "Only the shop owner can change the Telegram bot.", "forbidden")

    if method == "GET" and path == "/api/telegram/notify":
        return settings()
    if method != "POST":
        return NOT_HANDLED
    if path == "/api/telegram/notify/settings":
        require_manager()
        notifier.configure(owner_id, str(read_json().get("token") or ""))
        return settings()
    if path == "/api/telegram/notify/remove":
        require_manager()
        notifier.remove(owner_id)
        return settings()
    if path == "/api/telegram/notify/link":
        return notifier.create_link(owner_id, _user_id(user))
    if path == "/api/telegram/notify/unlink":
        notifier.unlink(_user_id(user))
        return settings()
    if path == "/api/telegram/notify/mode":
        notifier.set_mode(_user_id(user), str(read_json().get("mode") or ""))
        return settings()
    if path == "/api/telegram/notify/test":
        notifier.send_test(owner_id, _user_id(user))
        return {"ok": True}
    return NOT_HANDLED


RATE_LIMITS: dict[tuple[str, str], tuple[int, float]] = {
    ("POST", "/api/telegram/notify/settings"): (6, 60.0),
    ("POST", "/api/telegram/notify/link"): (10, 60.0),
    ("POST", "/api/telegram/notify/test"): (6, 60.0),
    ("POST", "/api/telegram/accounts/create"): (6, 60.0),
    ("POST", "/api/telegram/accounts/token"): (6, 60.0),
    ("POST", "/api/telegram/user/start"): (5, 300.0),
    ("POST", "/api/telegram/user/code"): (10, 300.0),
    ("POST", "/api/telegram/user/password"): (10, 300.0),
}
