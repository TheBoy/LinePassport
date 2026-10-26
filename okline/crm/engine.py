"""Background CRM work: task reminders, closing quiet chats, filling in names."""

from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Protocol

from .service import CrmService

NAME_RETRY_SECONDS = 6 * 3600


class CrmEngineHost(Protocol):
    def crm_notify(self, user_id: str, payload: dict[str, Any]) -> None: ...

    def crm_changed(self, account_ids: list[str]) -> None: ...

    def crm_resolve_names(
        self, account_id: str, external_ids: list[str]
    ) -> dict[str, dict[str, str]]: ...

    def crm_engine_tick(self) -> None: ...


class CrmEngine:
    def __init__(
        self,
        service: CrmService,
        host: CrmEngineHost,
        *,
        interval: float = 30.0,
        start: bool = True,
    ) -> None:
        self.service = service
        self.host = host
        self.interval = interval
        self.stop_event = threading.Event()
        self.name_attempts: dict[tuple[str, str], float] = {}
        self.thread = threading.Thread(target=self._run, name="linepassport-crm", daemon=True)
        if start:
            self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            self.tick()

    def tick(self) -> None:
        for step in (self._remind_tasks, self._close_quiet_chats, self._fill_names,
                     self.host.crm_engine_tick):
            if self.stop_event.is_set():
                return
            try:
                step()
            except Exception:
                traceback.print_exc()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)

    def _remind_tasks(self) -> None:
        for task in self.service.due_task_reminders():
            user_id = task.get("assignedTo") or task.get("createdBy")
            if not user_id:
                continue
            customer = str(task.get("customerName") or "")
            body = task["title"] + (f" · {customer}" if customer else "")
            self.host.crm_notify(
                str(user_id),
                {
                    "kind": "task_due",
                    "title": "ถึงเวลางานติดตาม",
                    "body": body,
                    "titleEn": "Follow-up is due",
                    "bodyEn": body,
                    "tag": f"linepassport:task:{task['id']}",
                    "requireInteraction": True,
                },
            )

    def _close_quiet_chats(self) -> None:
        touched = self.service.auto_close()
        if touched:
            self.host.crm_changed(touched)

    def _fill_names(self) -> None:
        now = time.time()
        by_account: dict[str, list[dict[str, Any]]] = {}
        for row in self.service.identities_missing_names():
            key = (str(row["owner_id"]), str(row["external_id"]))
            if now - self.name_attempts.get(key, 0) < NAME_RETRY_SECONDS:
                continue
            self.name_attempts[key] = now
            by_account.setdefault(str(row["account_id"]), []).append(row)
        if len(self.name_attempts) > 20_000:
            self.name_attempts = {
                k: v for k, v in self.name_attempts.items() if now - v < NAME_RETRY_SECONDS
            }
        for account_id, rows in by_account.items():
            ids = [str(row["external_id"]) for row in rows]
            try:
                profiles = self.host.crm_resolve_names(account_id, ids)
            except Exception:
                continue
            for row in rows:
                profile = profiles.get(str(row["external_id"])) or {}
                name = str(profile.get("name") or "")
                if name:
                    self.service.set_identity_profile(
                        str(row["owner_id"]),
                        str(row["channel"]),
                        str(row["external_id"]),
                        name=name,
                        picture=str(profile.get("picture") or ""),
                    )
