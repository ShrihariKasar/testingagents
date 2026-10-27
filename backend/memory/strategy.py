"""Strategy Memory Store.
Manages versioned prompts, tool-selection policies, benchmark baselines,
controlled promotion gates, and automated rollback triggers.
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.memory.models import StrategyRecord, StrategyStatus
from backend.models.contracts import AgentType


class StrategyMemoryStore:
    """Manages versioned strategy registry and promotion state machine."""

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
                CREATE TABLE IF NOT EXISTS strategy_registry (
                    strategy_id TEXT PRIMARY KEY,
                    agent_type TEXT,
                    version TEXT,
                    is_production INTEGER,
                    status TEXT,
                    rollback_target_version TEXT,
                    record_json TEXT,
                    created_at TEXT
                )
                """
            )
            conn.commit()

    def register_strategy(self, strategy: StrategyRecord) -> StrategyRecord:
        """Registers a new candidate or baseline strategy version."""
        record_json = strategy.model_dump_json()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO strategy_registry (
                    strategy_id, agent_type, version, is_production,
                    status, rollback_target_version, record_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy.strategy_id,
                    strategy.agent_type.value,
                    strategy.version,
                    1 if strategy.is_production else 0,
                    strategy.status.value,
                    strategy.rollback_target_version,
                    record_json,
                    strategy.created_at,
                ),
            )
            conn.commit()
        return strategy

    def get_production_strategy(self, agent_type: AgentType) -> Optional[StrategyRecord]:
        """Retrieves the active, verified production strategy for an agent."""
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT record_json FROM strategy_registry
                WHERE agent_type = ? AND is_production = 1
                ORDER BY created_at DESC LIMIT 1
                """,
                (agent_type.value,),
            ).fetchone()

        if row:
            return StrategyRecord.model_validate_json(row["record_json"])
        return None

    def promote_to_production(
        self,
        strategy_id: str,
        benchmark_scores: Dict[str, float],
    ) -> StrategyRecord:
        """
        Promotes a candidate strategy to production after passing independent evaluation.
        Sets rollback pointer to the previous production version.
        """
        with self._get_conn() as conn:
            # 1. Fetch target candidate
            row = conn.execute("SELECT record_json FROM strategy_registry WHERE strategy_id = ?", (strategy_id,)).fetchone()
            if not row:
                raise ValueError(f"Strategy {strategy_id} not found")

            candidate = StrategyRecord.model_validate_json(row["record_json"])

            # 2. Find current production strategy for rollback pointer
            current_prod = self.get_production_strategy(candidate.agent_type)
            if current_prod:
                candidate.rollback_target_version = current_prod.version
                # Demote current prod
                current_prod.is_production = False
                current_prod.status = StrategyStatus.DEPRECATED
                self.register_strategy(current_prod)

            # 3. Promote candidate
            candidate.is_production = True
            candidate.status = StrategyStatus.PRODUCTION
            candidate.benchmark_scores = benchmark_scores
            candidate.promoted_at = datetime.datetime.utcnow().isoformat()
            self.register_strategy(candidate)

        return candidate

    def rollback(self, agent_type: AgentType) -> Optional[StrategyRecord]:
        """
        Automated Rollback Safeguard:
        Reverts active production strategy to the designated rollback target version if performance degrades.
        """
        current_prod = self.get_production_strategy(agent_type)
        if not current_prod or not current_prod.rollback_target_version:
            return None

        target_version = current_prod.rollback_target_version

        with self._get_conn() as conn:
            # Locate previous version
            row = conn.execute(
                """
                SELECT record_json FROM strategy_registry
                WHERE agent_type = ? AND version = ?
                """,
                (agent_type.value, target_version),
            ).fetchone()

            if not row:
                return None

            prev_strategy = StrategyRecord.model_validate_json(row["record_json"])

            # Mark current as rolled back
            current_prod.is_production = False
            current_prod.status = StrategyStatus.ROLLED_BACK
            self.register_strategy(current_prod)

            # Re-promote previous version
            prev_strategy.is_production = True
            prev_strategy.status = StrategyStatus.PRODUCTION
            self.register_strategy(prev_strategy)

            return prev_strategy

    def list_strategies(self, agent_type: Optional[AgentType] = None) -> List[StrategyRecord]:
        """Lists all registered strategy versions."""
        query = "SELECT record_json FROM strategy_registry WHERE 1=1"
        params: List[Any] = []
        if agent_type:
            query += " AND agent_type = ?"
            params.append(agent_type.value)
        query += " ORDER BY created_at DESC"

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()

        return [StrategyRecord.model_validate_json(r["record_json"]) for r in rows]
