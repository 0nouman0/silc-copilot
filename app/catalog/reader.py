"""Row readers for the catalog builder.

`MySqlReader` runs the fixed queries from `queries.py` against the read-only replica with a SELECT-only account. Only the builder
talks to the replica; the model never does. Tests inject a fake reader with the same two methods.
"""
from __future__ import annotations

import os
from typing import Any, Protocol

from ..sources.base import DataNotGranted, SourceUnavailable
from .queries import ALLOWED_TABLES, QUERIES

ROW_CAP = 200_000
MAX_MS = 60_000


class RowReader(Protocol):
    def rows(self, name: str, params: tuple = ()) -> list[dict[str, Any]]: ...
    def checksums(self) -> dict[str, int]: ...
    def tables_read(self) -> set[str]: ...


class MySqlReader:
    def __init__(self, host: str, port: int, db: str, user: str, password: str):
        self._cfg = dict(host=host, port=port, database=db, user=user, password=password)
        self._read: set[str] = set()

    @classmethod
    def from_env(cls) -> "MySqlReader | None":
        user, pw = os.getenv("AGENT_DB_USER"), os.getenv("AGENT_DB_PASSWORD")
        if not user or not pw:
            return None
        return cls(os.getenv("APPLICANT_DB_HOST", "127.0.0.1"), int(os.getenv("APPLICANT_DB_PORT", "3306")),
                   os.getenv("APPLICANT_DB_NAME", "applicant"), user, pw)

    def _connect(self):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
            return pymysql.connect(**self._cfg, charset="utf8mb4", cursorclass=DictCursor, connect_timeout=5, read_timeout=120, autocommit=True)
        except Exception as e:  # noqa: BLE001
            raise SourceUnavailable(f"database unreachable: {type(e).__name__}") from e

    def _run(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                try:
                    cur.execute(f"SET SESSION MAX_EXECUTION_TIME={MAX_MS}")
                except Exception:  # noqa: BLE001  MariaDB uses max_statement_time instead
                    pass
                cur.execute(sql, params)
                return list(cur.fetchmany(ROW_CAP))
        except Exception as e:  # noqa: BLE001
            if (e.args or [None])[0] in (1142, 1143):
                raise DataNotGranted(f"no SELECT grant ({e.args[0]})") from e
            raise SourceUnavailable(f"query failed: {type(e).__name__}") from e
        finally:
            conn.close()

    def rows(self, name: str, params: tuple = ()) -> list[dict[str, Any]]:
        table, sql = QUERIES[name]
        if table not in ALLOWED_TABLES:       # defence in depth: the allow-list is also enforced here
            raise PermissionError(f"table {table} is not a catalog table")
        if "{ids}" in sql:
            if not params:
                return []
            sql = sql.format(ids=",".join(["%s"] * len(params)))
        self._read.add(table)
        return self._run(sql, params)

    def checksums(self) -> dict[str, int]:
        """CHECKSUM TABLE per loaded table (course_related_links and course_info have no modified-date column)."""
        out: dict[str, int] = {}
        for t in sorted(ALLOWED_TABLES):
            try:
                r = self._run(f"CHECKSUM TABLE `{t}`")     # t comes from the fixed allow-list above, never from input
                out[t] = int(r[0].get("Checksum") or 0) if r else 0
            except DataNotGranted:
                out[t] = -1
        return out

    def tables_read(self) -> set[str]:
        return set(self._read)
