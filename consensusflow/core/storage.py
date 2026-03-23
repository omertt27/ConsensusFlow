"""
storage.py — Persistent SQLite storage for VerificationReport records.

Provides async CRUD over an ``aiosqlite``-backed database so every
verification run is persisted and can be retrieved, listed, or deleted.

Schema
------
Table: reports
  run_id        TEXT  PRIMARY KEY
  prompt        TEXT  NOT NULL
  status        TEXT  NOT NULL
  final_answer  TEXT
  gotcha_score  INTEGER
  total_tokens  INTEGER
  total_latency_ms REAL
  created_at    TEXT  NOT NULL  (ISO-8601 UTC)
  chain_models  TEXT  (JSON array)
  payload       TEXT  NOT NULL  (full JSON serialisation of the report dict)

Usage::

    from consensusflow.core.storage import ReportStore
    store = ReportStore()               # defaults to ~/.consensusflow/reports.db
    await store.init()
    await store.save(report_dict)
    history = await store.list(limit=20)
    record  = await store.get("run-id")
    await store.delete("run-id")
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import aiosqlite

log = logging.getLogger("consensusflow.storage")

_DEFAULT_DB_PATH = Path.home() / ".consensusflow" / "reports.db"

# DDL
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS reports (
    run_id           TEXT PRIMARY KEY,
    prompt           TEXT NOT NULL,
    status           TEXT NOT NULL,
    final_answer     TEXT,
    gotcha_score     INTEGER,
    total_tokens     INTEGER,
    total_latency_ms REAL,
    created_at       TEXT NOT NULL,
    chain_models     TEXT,
    payload          TEXT NOT NULL
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_reports_created_at
    ON reports (created_at DESC);
"""


class ReportStore:
    """
    Async SQLite-backed store for verification report dictionaries.

    Parameters
    ----------
    db_path : str or Path, optional
        Location of the SQLite file.  Defaults to
        ``~/.consensusflow/reports.db``.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def init(self) -> None:
        """Create the table and index if they don't exist yet."""
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.execute(_CREATE_INDEX)
            await db.commit()
        self._initialized = True
        log.info("ReportStore initialised at %s", self._db_path)

    # ── CRUD ─────────────────────────────────────────────────────────────────

    async def save(self, report_dict: dict[str, Any]) -> None:
        """
        Persist a report dictionary.  Idempotent — uses INSERT OR REPLACE.

        Parameters
        ----------
        report_dict : dict
            The full dict returned by ``_report_to_dict()`` in the backend.
        """
        await self.init()
        row = _extract_row(report_dict)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO reports
                (run_id, prompt, status, final_answer, gotcha_score,
                 total_tokens, total_latency_ms, created_at, chain_models, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            await db.commit()
        log.debug("Saved report run_id=%s", report_dict.get("run_id"))

    async def get(self, run_id: str) -> Optional[dict[str, Any]]:
        """
        Retrieve a single report by run_id.

        Returns the full deserialized payload dict, or ``None`` if not found.
        """
        await self.init()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT payload FROM reports WHERE run_id = ?", (run_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return json.loads(row["payload"])

    async def list(
        self,
        limit: int = 50,
        offset: int = 0,
        status_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return a paginated list of summary rows (not full payloads).

        Each element contains: run_id, prompt (truncated), status,
        gotcha_score, total_tokens, total_latency_ms, created_at.

        Parameters
        ----------
        limit : int
            Max results to return (default 50).
        offset : int
            Pagination offset.
        status_filter : str, optional
            If given, only rows with this status are returned.
        """
        await self.init()
        where  = "WHERE status = ?" if status_filter else ""
        params: tuple = (status_filter, limit, offset) if status_filter else (limit, offset)
        sql = f"""
            SELECT run_id, prompt, status, gotcha_score,
                   total_tokens, total_latency_ms, created_at, chain_models
            FROM reports
            {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()

        return [
            {
                "run_id":           r["run_id"],
                "prompt":           r["prompt"][:120] + ("…" if len(r["prompt"]) > 120 else ""),
                "status":           r["status"],
                "gotcha_score":     r["gotcha_score"],
                "total_tokens":     r["total_tokens"],
                "total_latency_ms": r["total_latency_ms"],
                "created_at":       r["created_at"],
                "chain_models":     json.loads(r["chain_models"] or "[]"),
            }
            for r in rows
        ]

    async def delete(self, run_id: str) -> bool:
        """
        Delete a report by run_id.

        Returns True if a row was deleted, False if not found.
        """
        await self.init()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM reports WHERE run_id = ?", (run_id,)
            )
            await db.commit()
            deleted = cursor.rowcount > 0
        log.debug("Deleted report run_id=%s  found=%s", run_id, deleted)
        return deleted

    async def count(self) -> int:
        """Return the total number of persisted reports."""
        await self.init()
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM reports") as cursor:
                row = await cursor.fetchone()
        return row[0] if row else 0

    async def clear_all(self) -> int:
        """Delete ALL reports.  Returns the number of rows deleted."""
        await self.init()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("DELETE FROM reports")
            await db.commit()
            count = cursor.rowcount
        log.warning("Cleared all %d reports from store", count)
        return count


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_row(d: dict[str, Any]) -> tuple:
    """Extract a flat tuple suitable for an INSERT statement."""
    from datetime import datetime, timezone

    gotcha = d.get("gotcha_score", {})
    score  = gotcha.get("score") if isinstance(gotcha, dict) else None

    return (
        d.get("run_id", ""),
        d.get("prompt", ""),
        d.get("status", ""),
        d.get("final_answer", ""),
        score,
        d.get("total_tokens", 0),
        d.get("total_latency_ms", 0.0),
        d.get("created_at") or datetime.now(timezone.utc).isoformat(),
        json.dumps(d.get("chain_models", [])),
        json.dumps(d),
    )
