"""The main :class:`OkLine` facade.

It wires together the transport, the auth flows, the operation receiver, the
OBS client and the response recorder, and mixes in one typed method per Thrift
endpoint (see ``services/``).  Every endpoint is *also* reachable generically
via :meth:`OkLine.call`, and every call can be recorded and pasted in full
detail via :pyattr:`OkLine.last` / :meth:`OkLine.dump`.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable, List, Optional

from .auth import AuthFlows, LoginResult
from .obs import ObsClient
from .operations import OperationReceiver
from .recorder import Exchange, Recorder
from .services import AllServices
from .transport import LineConfig, Tokens, Transport

log = logging.getLogger("okline")


class OkLine(AllServices):
    """A complete, high-level LINE Chrome (CHROMEOS 3.7.2) API client.

    Examples
    --------
    Log in and send a message::

        api = OkLine()
        if api.auth.email_login("me@example.com", "secret").success:
            api.send_text("uXXXX...", "hello from python")

    Re-use a token and paste every response::

        api = OkLine(access_token="...", refresh_token="...")
        api.get_profile()
        print(api.last.pretty())          # the last exchange
        print(api.dump())                 # everything this session
        api.save_log("session.har", fmt="har")

    Parameters
    ----------
    record:
        Capture every request/response (default ``True``).  Access via
        :pyattr:`history` / :pyattr:`last`, format via :meth:`dump`.
    redact:
        Mask secrets (tokens, X-Hmac, passwords) in recorded output
        (default ``True``).  Pass ``redact=False`` to reveal them.
    on_exchange:
        Optional callback invoked with each :class:`Exchange` as it completes.
    """

    def __init__(self, *, access_token: Optional[str] = None,
                 refresh_token: Optional[str] = None,
                 certificate: Optional[str] = None,
                 mid: Optional[str] = None,
                 config: Optional[LineConfig] = None,
                 transport: Optional[Transport] = None,
                 record: bool = True,
                 record_capacity: int = 500,
                 redact: bool = True,
                 on_exchange: Optional[Callable[[Exchange], None]] = None) -> None:
        tokens = Tokens(access_token=access_token, refresh_token=refresh_token,
                        certificate=certificate, mid=mid)
        self.transport = transport or Transport(config or LineConfig(), tokens)
        self.auth = AuthFlows(self.transport)
        self.ops = OperationReceiver(self.transport)
        self.obs = ObsClient(self.transport)
        self._reqseq = 0

        # Response recording.
        self.recorder: Optional[Recorder] = (
            Recorder(capacity=record_capacity, redact=redact) if record else None)
        self.transport.recorder = self.recorder
        if on_exchange:
            self.transport.hooks.append(on_exchange)

        # When loaded from a session file, persist refreshed tokens back to it.
        self._session_path: Optional[str] = None

        # Auto-refresh the access token on a 401 if we hold a refresh token.
        self.transport._refresh_hook = self._auto_refresh  # noqa: SLF001

    # -- credential helpers --------------------------------------------------
    @property
    def tokens(self) -> Tokens:
        return self.transport.tokens

    @property
    def config(self) -> LineConfig:
        return self.transport.config

    def set_access_token(self, token: str) -> None:
        self.transport.tokens.access_token = token

    def _auto_refresh(self) -> bool:
        if not self.transport.tokens.refresh_token:
            return False
        try:
            self.auth.refresh_access_token()
            log.info("access token refreshed")
            if self._session_path:        # persist the new token
                self.save_tokens(self._session_path)
            return True
        except Exception as exc:  # pragma: no cover - network
            log.warning("token refresh failed: %s", exc)
            return False

    # -- session persistence -------------------------------------------------
    def save_tokens(self, path: Optional[str] = None) -> None:
        """Save the current credentials to a JSON session file."""
        from .session import Session
        path = path or self._session_path
        if not path:
            raise ValueError("no path given and no session file attached")
        self._session_path = path
        Session.from_tokens(self.transport.tokens).save(path)

    @classmethod
    def from_tokens_file(cls, path: str, **kw: Any) -> "OkLine":
        """Build a client from a session file (and auto-save refreshed tokens)."""
        from .session import Session
        s = Session.load(path)
        api = cls(access_token=s.access_token, refresh_token=s.refresh_token,
                  certificate=s.certificate, mid=s.mid, **kw)
        api._session_path = path
        return api

    def next_req_seq(self) -> int:
        self._reqseq += 1
        return self._reqseq

    # -- generic escape hatch ------------------------------------------------
    def call(self, endpoint_key: str, *args: Any, **kw: Any) -> Any:
        """Invoke any registered endpoint by ``Namespace.Service.method`` key.

        ``api.call("Talk.TalkService.getProfile", 0)`` == ``api.get_profile()``.
        """
        return self.transport.call(endpoint_key, list(args), **kw)

    # -- recording / "paste resp" -------------------------------------------
    @property
    def history(self) -> List[Exchange]:
        """Every recorded :class:`Exchange` this session (newest last)."""
        return self.recorder.entries if self.recorder else []

    @property
    def last(self) -> Optional[Exchange]:
        """The most recent recorded :class:`Exchange` (or ``None``)."""
        return self.recorder.last if self.recorder else None

    def on_exchange(self, hook: Callable[[Exchange], None]) -> Callable:
        """Register a per-exchange callback (also usable as a decorator)."""
        self.transport.hooks.append(hook)
        return hook

    def print_last(self, *, redact: Optional[bool] = None,
                   file=None) -> None:
        """Print the last exchange as an HTTP transcript."""
        if not self.last:
            return
        r = self.recorder.redact if redact is None else redact  # type: ignore[union-attr]
        _safe_print(self.last.pretty(redact=r), file)

    def dump(self, *, redact: Optional[bool] = None) -> str:
        """Return every recorded exchange as one big transcript string."""
        return self.recorder.dump_text(redact=redact) if self.recorder else ""

    def save_log(self, path: str, *, fmt: str = "text",
                 redact: Optional[bool] = None) -> None:
        """Save the session log. ``fmt`` is ``"text"``, ``"json"`` or ``"har"``."""
        if self.recorder:
            self.recorder.save(path, fmt=fmt, redact=redact)

    def clear_log(self) -> None:
        if self.recorder:
            self.recorder.clear()

    # -- convenience login passthroughs --------------------------------------
    def login_with_email(self, email: str, password: str, **kw: Any) -> LoginResult:
        return self.auth.email_login(email, password, **kw)

    def qr_login(self, **kw: Any) -> LoginResult:
        """Shortcut for :meth:`AuthFlows.qr_login`."""
        return self.auth.qr_login(**kw)

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        """Release the LTSM Node bridge subprocess (if started)."""
        signer = getattr(self.transport, "_signer", None)
        if signer is not None:
            signer.close()

    def __enter__(self) -> "OkLine":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        who = self.transport.tokens.mid or "anonymous"
        n = len(self.history)
        return (f"<OkLine {who} app={self.config.application_header.split(chr(9))[0]} "
                f"calls={n}>")


def _safe_print(text: str, file=None) -> None:
    """Print possibly-non-ASCII text without dying on a cp1252 Windows console."""
    stream = file or sys.stdout
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", "ascii") or "ascii"
        print(text.encode(enc, "replace").decode(enc), file=stream)


# Backwards-compatible alias (the library used to be called LineApi).
LineApi = OkLine
