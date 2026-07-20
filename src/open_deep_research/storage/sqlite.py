"""Small SQLite connection and migration boundary without an ORM."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from open_deep_research.knowledge.repositories import CorruptSchemaError
from open_deep_research.storage.migrations import MIGRATION_V1


SCHEMA_VERSION = 1
REQUIRED_TABLES = frozenset(
    {
        "schema_metadata",
        "knowledge_scopes",
        "sources",
        "documents",
        "content_blobs",
        "document_versions",
        "chunks",
        "requirements",
        "evidence",
        "audit_events",
    }
)


class SQLiteDatabase:
    """Own schema migration and correctly configured short-lived connections."""

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5_000) -> None:
        if busy_timeout_ms < 1:
            raise ValueError("busy_timeout_ms must be positive")
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        """Open one foreign-key-enforcing connection for a unit of work."""
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def migrate(self) -> None:
        """Create v1 atomically or reject an unknown/newer schema."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'schema_metadata'"
            ).fetchone()
            if table_exists:
                try:
                    row = connection.execute(
                        "SELECT schema_version FROM schema_metadata WHERE singleton = 1"
                    ).fetchone()
                except sqlite3.DatabaseError as exc:
                    raise CorruptSchemaError("schema_metadata is malformed") from exc
                if row is None or int(row["schema_version"]) != SCHEMA_VERSION:
                    found = None if row is None else row["schema_version"]
                    raise CorruptSchemaError(
                        f"unsupported SQLite schema version: {found!r}"
                    )
                present = {
                    str(item["name"])
                    for item in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                missing = REQUIRED_TABLES - present
                if missing:
                    raise CorruptSchemaError(
                        f"SQLite schema v1 is missing tables: {sorted(missing)}"
                    )
            else:
                for statement in MIGRATION_V1.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_metadata(singleton, schema_version, applied_at) "
                    "VALUES (1, ?, ?)",
                    (SCHEMA_VERSION, datetime.now(UTC).isoformat()),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def schema_version(self) -> int:
        """Return the installed schema version after integrity checks."""
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT schema_version FROM schema_metadata WHERE singleton = 1"
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise CorruptSchemaError("schema_metadata row is missing")
        value = int(row["schema_version"])
        if value != SCHEMA_VERSION:
            raise CorruptSchemaError(f"unsupported SQLite schema version: {value}")
        return value
