"""A tiny event-driven bot framework on top of OkLine.

Register handlers with decorators and call :meth:`Bot.run` — it streams the
operation feed, dispatches each event, and gives message handlers a convenient
``reply()``::

    from okline import OkLine
    from okline.bot import Bot

    api = OkLine.from_tokens_file("session.json")
    bot = Bot(api)

    @bot.on_message
    def echo(ctx):
        if ctx.text:
            ctx.reply(f"you said: {ctx.text}")

    @bot.command("ping")
    def ping(ctx):
        ctx.reply("pong")

    bot.run()                 # blocks; Ctrl-C to stop

Handlers receive a :class:`MessageContext` (for messages) or an
:class:`EventContext` (for other operations). Exceptions in a handler are caught
and logged so one bad handler never kills the loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .enums import OpType
from .operations import Operation

log = logging.getLogger("okline.bot")


def _is_group_mid(mid: Optional[str]) -> bool:
    """True for a group/room/square mid (C/R/S prefix, any case)."""
    return (mid or "")[:1].lower() in ("c", "r", "s")


def _reply_target(message: dict) -> Optional[str]:
    """Where a reply should go: the group/room if any, else the sender."""
    to = message.get("to") or ""
    if _is_group_mid(to):
        return to
    return message.get("from") or to or None


@dataclass
class EventContext:
    api: Any
    bot: "Bot"
    op: Operation

    @property
    def type(self) -> Optional[int]:
        return self.op.type


@dataclass
class MessageContext(EventContext):
    message: Dict[str, Any] = None  # type: ignore[assignment]

    @property
    def text(self) -> Optional[str]:
        return self.message.get("text") if self.message else None

    @property
    def sender(self) -> Optional[str]:
        return self.message.get("from") if self.message else None

    @property
    def to(self) -> Optional[str]:
        return self.message.get("to") if self.message else None

    @property
    def content_type(self) -> int:
        return int(self.message.get("contentType", 0)) if self.message else 0

    @property
    def is_group(self) -> bool:
        return _is_group_mid(self.to)

    @property
    def reply_target(self) -> Optional[str]:
        return _reply_target(self.message or {})

    def reply(self, text: str, **kw: Any) -> Any:
        target = self.reply_target
        if not target:
            raise ValueError("cannot determine a reply target for this message")
        return self.api.send_text(target, text, **kw)

    def reply_sticker(self, package_id: str, sticker_id: str, **kw: Any) -> Any:
        return self.api.send_sticker(self.reply_target, package_id, sticker_id, **kw)

    def mark_read(self) -> Any:
        if self.to and self.message.get("id"):
            return self.api.send_chat_checked(self.to, self.message["id"])
        return None


class Bot:
    """Dispatches operations to registered handlers."""

    def __init__(self, api: Any, *, ignore_self: bool = True,
                 auto_mark_read: bool = False) -> None:
        self.api = api
        self.ignore_self = ignore_self
        self.auto_mark_read = auto_mark_read
        self._message_handlers: List[Callable[[MessageContext], Any]] = []
        self._event_handlers: Dict[int, List[Callable[[EventContext], Any]]] = {}
        self._commands: Dict[str, Callable[[MessageContext], Any]] = {}
        self.command_prefix = "/"
        self._self_mid: Optional[str] = getattr(api.tokens, "mid", None)

    # -- registration --------------------------------------------------------
    def on_message(self, fn: Callable[[MessageContext], Any]) -> Callable:
        """Register a handler for incoming (received) messages."""
        self._message_handlers.append(fn)
        return fn

    def on(self, *op_types: int) -> Callable:
        """Decorator: register a handler for one or more :class:`OpType` values."""
        def deco(fn: Callable[[EventContext], Any]) -> Callable:
            for t in op_types:
                self._event_handlers.setdefault(int(t), []).append(fn)
            return fn
        return deco

    def command(self, name: str) -> Callable:
        """Decorator: handle a text command like ``/name ...``."""
        def deco(fn: Callable[[MessageContext], Any]) -> Callable:
            self._commands[name] = fn
            return fn
        return deco

    # -- dispatch ------------------------------------------------------------
    def dispatch(self, op: Operation) -> None:
        # type-specific handlers
        for fn in self._event_handlers.get(op.type or -1, []):
            self._safe(fn, EventContext(self.api, self, op))

        if op.type == OpType.RECEIVE_MESSAGE and op.message:
            if self.ignore_self and self._self_mid and \
                    op.message.get("from") == self._self_mid:
                return
            ctx = MessageContext(self.api, self, op, message=op.message)
            if self.auto_mark_read:
                self._safe(ctx.mark_read)
            # command routing
            text = ctx.text or ""
            if text.startswith(self.command_prefix):
                name = text[len(self.command_prefix):].split(None, 1)[0]
                if name in self._commands:
                    self._safe(self._commands[name], ctx)
                    return
            for fn in self._message_handlers:
                self._safe(fn, ctx)

    def _safe(self, fn: Callable, *args: Any) -> None:
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 - one handler must not kill the loop
            log.exception("handler %s failed: %s", getattr(fn, "__name__", fn), exc)

    # -- run loop ------------------------------------------------------------
    def run(self, *, reconnect: bool = True) -> None:
        """Stream operations and dispatch them. Blocks until interrupted."""
        if not self._self_mid:
            try:
                prof = self.api.get_profile()
                self._self_mid = prof.get("mid") if isinstance(prof, dict) else None
            except Exception:  # pragma: no cover - network
                pass
        log.info("bot running as %s", self._self_mid or "unknown")
        for op in self.api.ops.iter_operations(reconnect=reconnect):
            self.dispatch(op)
