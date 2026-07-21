"""SQLite v1 MemoryRepository with scope-aware uniqueness and audit."""
# ruff: noqa: D102,D107

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from open_deep_research.memory.in_memory_repository import InMemoryMemoryRepository
from open_deep_research.memory.models import (
    MemoryAuditEvent,
    MemoryRecord,
    MemoryWriteProposal,
)


class SQLiteMemoryRepository(InMemoryMemoryRepository):
    """Durable repository; an in-memory projection is rehydrated on open."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._setup_and_load()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _setup_and_load(self):
        with self._connect() as conn:
            conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS memory_schema(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO memory_schema(version) VALUES(1);
            CREATE TABLE IF NOT EXISTS memory_proposals(
              namespace TEXT NOT NULL, proposal_id TEXT NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(namespace, proposal_id));
            CREATE TABLE IF NOT EXISTS memories(
              namespace TEXT NOT NULL, memory_id TEXT NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(namespace, memory_id));
            CREATE TABLE IF NOT EXISTS memory_audit(
              audit_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, entity_id TEXT NOT NULL,
              payload TEXT NOT NULL);
            """)
            for row in conn.execute("SELECT namespace,proposal_id,payload FROM memory_proposals"):
                item = MemoryWriteProposal.model_validate_json(row["payload"])
                self.proposals[(item.namespace, item.proposal_id)] = item
            for row in conn.execute("SELECT namespace,memory_id,payload FROM memories"):
                item = MemoryRecord.model_validate_json(row["payload"])
                self.records[(item.namespace, item.memory_id)] = item
            self.audits = [MemoryAuditEvent.model_validate_json(row["payload"]) for row in conn.execute("SELECT payload FROM memory_audit ORDER BY rowid")]

    @staticmethod
    def _ns(namespace):
        return "\x1f".join(namespace)

    def _flush(self):
        with self._connect() as conn:
            conn.executemany("INSERT OR REPLACE INTO memory_proposals VALUES(?,?,?)", [(self._ns(ns), pid, value.model_dump_json()) for (ns,pid),value in self.proposals.items()])
            conn.executemany("INSERT OR REPLACE INTO memories VALUES(?,?,?)", [(self._ns(ns), mid, value.model_dump_json()) for (ns,mid),value in self.records.items()])
            conn.executemany("INSERT OR IGNORE INTO memory_audit VALUES(?,?,?,?)", [(a.audit_id,self._ns(a.namespace),a.entity_id,a.model_dump_json()) for a in self.audits])

    async def propose(self, proposal, *, actor):
        async with self._lock:
            result = await super().propose(proposal, actor=actor)
            await asyncio.to_thread(self._flush)
            return result

    async def apply_decision(self, proposal, decision, *, actor):
        async with self._lock:
            result = await super().apply_decision(proposal, decision, actor=actor)
            await asyncio.to_thread(self._flush)
            return result

    async def mark_status(self, namespace, memory_id, status, *, actor, reason):
        async with self._lock:
            result = await super().mark_status(namespace, memory_id, status, actor=actor, reason=reason)
            await asyncio.to_thread(self._flush)
            return result

    async def soft_delete(self, namespace, memory_id, *, actor, reason):
        async with self._lock:
            result = await super().soft_delete(namespace, memory_id, actor=actor, reason=reason)
            await asyncio.to_thread(self._flush)
            return result
