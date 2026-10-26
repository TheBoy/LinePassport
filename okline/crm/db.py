"""A small SQL layer for the CRM: SQLite by default, PostgreSQL when configured.

SQL is written once with ``?`` placeholders and portable types (TEXT ids,
BIGINT millisecond timestamps, JSON stored as TEXT, ``ON CONFLICT`` upserts),
so the same statements run on both engines. One connection is shared behind a
lock; LinePassport already runs as a single process (the PostgreSQL state store
takes an advisory lock), so there is no cross-process writer to coordinate.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from .errors import CrmError
from .schema import MIGRATIONS


class CrmDatabase:
    def __init__(
        self, *, sqlite_path: str | None = None, database_url: str | None = None
    ) -> None:
        self.lock = threading.RLock()
        self.dialect = "postgres" if database_url else "sqlite"
        self._sqlite_path = sqlite_path or ":memory:"
        self._database_url = database_url or ""
        self._conn: Any = None
        self._depth = 0
        self._connect()

    # -- connection ----------------------------------------------------------
    def _connect(self) -> None:
        if self.dialect == "sqlite":
            if self._sqlite_path != ":memory:" and not os.path.exists(self._sqlite_path):
                # Customer data stays private to the service user, like the other state files.
                os.close(os.open(self._sqlite_path, os.O_CREAT | os.O_WRONLY, 0o600))
            conn = sqlite3.connect(
                self._sqlite_path,
                check_same_thread=False,
                isolation_level=None,
                timeout=30,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._conn = conn
            return
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise CrmError(
                500,
                'PostgreSQL support requires psycopg. Run: pip install "okline[web]"',
                "database_driver_missing",
            ) from exc
        self._conn = psycopg.connect(
            self._database_url, autocommit=True, row_factory=dict_row
        )

    def close(self) -> None:
        with self.lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "postgres" else sql

    def _run(self, sql: str, params: Sequence[Any]) -> Any:
        if self._conn is None:
            self._connect()
        try:
            return self._conn.execute(self._sql(sql), tuple(params))
        except Exception as exc:
            if self.dialect != "postgres" or self._depth or not _is_connection_error(exc):
                raise
            # A dropped PostgreSQL connection outside a transaction: reconnect once.
            self.close()
            self._connect()
            return self._conn.execute(self._sql(sql), tuple(params))

    # -- statements ----------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.lock:
            cursor = self._run(sql, params)
            return int(getattr(cursor, "rowcount", 0) or 0)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self.lock:
            cursor = self._run(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.query_one(sql, params)
        if not row:
            return None
        return next(iter(row.values()))

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.lock:
            if self.dialect == "postgres":
                if self._conn is None:
                    self._connect()
                self._depth += 1
                try:
                    with self._conn.transaction():
                        yield
                finally:
                    self._depth -= 1
                return
            outer = self._depth == 0
            if outer:
                self._run("BEGIN IMMEDIATE", ())
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outer:
                    self._run("ROLLBACK", ())
                raise
            self._depth -= 1
            if outer:
                self._run("COMMIT", ())

    # -- schema --------------------------------------------------------------
    def migrate(self) -> int:
        with self.lock:
            self.execute(
                "CREATE TABLE IF NOT EXISTS crm_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            current = int(
                self.scalar("SELECT value FROM crm_meta WHERE key = ?", ("schema",)) or 0
            )
            for version, statements in MIGRATIONS:
                if version <= current:
                    continue
                with self.transaction():
                    for statement in statements:
                        self.execute(statement)
                    self.execute(
                        "INSERT INTO crm_meta (key, value) VALUES (?, ?) "
                        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                        ("schema", str(version)),
                    )
                current = version
            return current


def _is_connection_error(exc: Exception) -> bool:
    name = type(exc).__name__
    return name in {"OperationalError", "InterfaceError"}
