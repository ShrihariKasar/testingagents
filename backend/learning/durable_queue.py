"""Durable, Persistent Transactional Event Queue for Continuous Learning.
Guarantees at-least-once delivery, idempotent deduplication, priority ordering,
and resilience across service outages and restarts.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.learning.models import LearningEvent


class DurableEventQueue:
    """Manages transactional SQLite storage for the continuous learning event stream."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Path(".agent_memory.db")
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_event_queue (
                    event_id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE,
                    project_id TEXT,
                    run_id TEXT,
                    agent_type TEXT,
                    event_type TEXT,
                    priority INTEGER,
                    status TEXT,
                    retry_count INTEGER,
                    error_message TEXT,
                    record_json TEXT,
                    created_at TEXT,
                    processed_at TEXT
                )
                """
            )
            conn.commit()

    def enqueue(self, event: LearningEvent) -> bool:
        """
        Enqueues an event atomically.
        If idempotency_key already exists, the event is safely ignored to prevent duplicate learning.
        Returns:
            True if newly enqueued, False if duplicate detected and deduplicated.
        """
        record_json = event.model_dump_json()
        with self._get_conn() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO learning_event_queue (
                    event_id, idempotency_key, project_id, run_id,
                    agent_type, event_type, priority, status,
                    retry_count, error_message, record_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.idempotency_key,
                    event.project_id,
                    event.run_id,
                    event.agent_type.value,
                    event.event_type.value,
                    event.priority,
                    event.status,
                    event.retry_count,
                    event.error_message,
                    record_json,
                    event.created_at,
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def fetch_pending_batch(self, limit: int = 10) -> List[LearningEvent]:
        """
        Fetches highest-priority pending events and transitions them to PROCESSING.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT event_id, record_json FROM learning_event_queue
                WHERE status = 'PENDING'
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            events: List[LearningEvent] = []
            for r in rows:
                ev = LearningEvent.model_validate_json(r["record_json"])
                ev.status = "PROCESSING"
                conn.execute(
                    "UPDATE learning_event_queue SET status = 'PROCESSING' WHERE event_id = ?",
                    (ev.event_id,),
                )
                events.append(ev)

            conn.commit()
            return events

    def acknowledge(self, event_id: str, success: bool = True, error: Optional[str] = None):
        """Acknowledge completed event processing or trigger retry."""
        now = datetime.datetime.utcnow().isoformat()
        with self._get_conn() as conn:
            if success:
                conn.execute(
                    """
                    UPDATE learning_event_queue
                    SET status = 'PROCESSED', processed_at = ?
                    WHERE event_id = ?
                    """,
                    (now, event_id),
                )
            else:
                row = conn.execute("SELECT retry_count FROM learning_event_queue WHERE event_id = ?", (event_id,)).fetchone()
                retries = (row["retry_count"] if row else 0) + 1
                new_status = "FAILED" if retries >= 3 else "PENDING"
                conn.execute(
                    """
                    UPDATE learning_event_queue
                    SET status = ?, retry_count = ?, error_message = ?
                    WHERE event_id = ?
                    """,
                    (new_status, retries, error, event_id),
                )
            conn.commit()

    def get_stats(self) -> Dict[str, int]:
        """Returns live statistics of queue backlog and throughput."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) as cnt
                FROM learning_event_queue
                GROUP BY status
                """
            ).fetchall()

        stats = {"PENDING": 0, "PROCESSING": 0, "PROCESSED": 0, "FAILED": 0, "TOTAL": 0}
        total = 0
        for r in rows:
            st = r["status"]
            cnt = r["cnt"]
            if st in stats:
                stats[st] = cnt
            total += cnt
        stats["TOTAL"] = total
        return stats

    def recover_stuck_processing_jobs(self, max_retry: int = 3) -> int:
        """
        Recovers jobs left in 'PROCESSING' state after a daemon/worker crash or restart.
        If retry_count >= max_retry, transitions them to FAILED (dead-letter);
        otherwise, resets them to PENDING so they are resumed.
        """
        with self._get_conn() as conn:
            # Set to FAILED if retries exceeded
            conn.execute(
                """
                UPDATE learning_event_queue
                SET status = 'FAILED', error_message = 'Max retries exceeded during recovery'
                WHERE status = 'PROCESSING' AND retry_count >= ?
                """,
                (max_retry,),
            )
            # Reset remaining to PENDING and increment retry count
            cur = conn.execute(
                """
                UPDATE learning_event_queue
                SET status = 'PENDING', retry_count = retry_count + 1
                WHERE status = 'PROCESSING'
                """
            )
            conn.commit()
            return cur.rowcount

    def get_dead_letter_events(self, limit: int = 50) -> List[LearningEvent]:
        """Returns events that have permanently failed processing (dead letters)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT record_json FROM learning_event_queue
                WHERE status = 'FAILED'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [LearningEvent.model_validate_json(r["record_json"]) for r in rows]

    def replay_dead_letter_event(self, event_id: str) -> bool:
        """Re-enqueues a dead-lettered event for reprocessing."""
        with self._get_conn() as conn:
            cur = conn.execute(
                """
                UPDATE learning_event_queue
                SET status = 'PENDING', retry_count = 0, error_message = NULL
                WHERE event_id = ? AND status = 'FAILED'
                """,
                (event_id,),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_recent_events(self, limit: int = 50, status_filter: Optional[str] = None) -> List[LearningEvent]:
        """Lists recent events for monitoring and audit dashboards."""
        query = "SELECT record_json FROM learning_event_queue"
        params: List[Any] = []
        if status_filter:
            query += " WHERE status = ?"
            params.append(status_filter)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [LearningEvent.model_validate_json(r["record_json"]) for r in rows]
