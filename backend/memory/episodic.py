"""Episodic Memory Store.
Persists past testing runs, interventions, verified patches, and outcomes.
Provides structured query capabilities and recurring failure analysis.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.memory.models import ExperienceRecord, FailureCategory, FailureOrigin
from backend.models.contracts import AgentType


class EpisodicMemoryStore:
    """Manages persistent SQLite storage of raw testing experiences."""

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
                CREATE TABLE IF NOT EXISTS episodic_experiences (
                    experience_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    run_id TEXT,
                    agent_type TEXT,
                    task_type TEXT,
                    original_objective TEXT,
                    strategy_id TEXT,
                    strategy_version TEXT,
                    failure_category TEXT,
                    failure_origin TEXT,
                    is_root_cause_confirmed INTEGER,
                    produced_measurable_improvement INTEGER,
                    duration_ms REAL,
                    token_usage INTEGER,
                    estimated_cost_usd REAL,
                    record_json TEXT,
                    created_at TEXT
                )
                """
            )
            conn.commit()

    def record_experience(self, exp: ExperienceRecord) -> ExperienceRecord:
        """Stores an experience record with indexed search columns."""
        record_json = exp.model_dump_json()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO episodic_experiences (
                    experience_id, project_id, run_id, agent_type, task_type,
                    original_objective, strategy_id, strategy_version,
                    failure_category, failure_origin, is_root_cause_confirmed,
                    produced_measurable_improvement, duration_ms, token_usage,
                    estimated_cost_usd, record_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exp.experience_id,
                    exp.project_id,
                    exp.run_id,
                    exp.agent_type.value,
                    exp.task_type,
                    exp.original_objective,
                    exp.strategy_id,
                    exp.strategy_version,
                    exp.failure_category.value if exp.failure_category else None,
                    exp.failure_origin.value if exp.failure_origin else None,
                    1 if exp.is_root_cause_confirmed else 0,
                    1 if exp.produced_measurable_improvement else 0,
                    exp.duration_ms,
                    exp.token_usage,
                    exp.estimated_cost_usd,
                    record_json,
                    exp.created_at,
                ),
            )
            conn.commit()
        return exp

    def query_experiences(
        self,
        project_id: Optional[str] = None,
        agent_type: Optional[AgentType] = None,
        task_type: Optional[str] = None,
        failure_category: Optional[FailureCategory] = None,
        only_improvements: bool = False,
        limit: int = 50,
    ) -> List[ExperienceRecord]:
        """Queries stored experiences by contextual filters."""
        query = "SELECT record_json FROM episodic_experiences WHERE 1=1"
        params: List[Any] = []

        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if agent_type:
            query += " AND agent_type = ?"
            params.append(agent_type.value)
        if task_type:
            query += " AND task_type = ?"
            params.append(task_type)
        if failure_category:
            query += " AND failure_category = ?"
            params.append(failure_category.value)
        if only_improvements:
            query += " AND produced_measurable_improvement = 1"

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()

        return [ExperienceRecord.model_validate_json(r["record_json"]) for r in rows]

    def get_recurring_failure_patterns(self, min_count: int = 2) -> List[Dict[str, Any]]:
        """Identifies recurring failure categories and origins across projects."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT failure_category, failure_origin, COUNT(*) as frequency
                FROM episodic_experiences
                WHERE failure_category IS NOT NULL
                GROUP BY failure_category, failure_origin
                HAVING COUNT(*) >= ?
                ORDER BY frequency DESC
                """,
                (min_count,),
            ).fetchall()

        return [
            {
                "failure_category": r["failure_category"],
                "failure_origin": r["failure_origin"],
                "frequency": r["frequency"],
            }
            for r in rows
        ]
