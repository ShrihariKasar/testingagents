"""Persistent Project, Test Plan, Completion Session, and Audit Store.
Implements versioned SQLite migrations and durable entity persistence for:
- Projects & Architectural Profiles
- Test Plans & Orchestration Runs
- Completion Sessions, Checklists, & Findings
- Audit Records and Patch Histories
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.completion.models import AutonomousCompletionContext
from backend.models.contracts import ChangeReviewRecord, RunContext, TaskNode


class ProjectRecord(BaseModel):
    project_id: str
    name: str
    repo_path: str
    language: str = "python"
    framework: Optional[str] = None
    dependencies: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


class AuditRecord(BaseModel):
    audit_id: str = Field(default_factory=lambda: f"aud_{uuid.uuid4().hex[:8]}")
    entity_type: str
    entity_id: str
    action: str
    actor: str = "orchestrator"
    details: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


class ProjectStore:
    """Manages versioned persistence of platform entities, runs, and completion sessions."""

    CURRENT_SCHEMA_VERSION = 3

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Path(".agent_memory.db")
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            # 1. Migration version tracker
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT,
                    description TEXT
                )
                """
            )

            # 2. Projects table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT,
                    repo_path TEXT,
                    language TEXT,
                    framework TEXT,
                    dependencies_json TEXT,
                    metadata_json TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )

            # 3. Test Runs table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS test_runs (
                    run_id TEXT PRIMARY KEY,
                    project_path TEXT,
                    status TEXT,
                    time_budget_seconds INTEGER,
                    token_budget INTEGER,
                    used_tokens INTEGER,
                    run_json TEXT,
                    created_at TEXT,
                    completed_at TEXT
                )
                """
            )

            # 4. Completion Sessions & Checklists table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS completion_sessions (
                    session_id TEXT PRIMARY KEY,
                    project_path TEXT,
                    status TEXT,
                    current_iteration INTEGER,
                    used_cost_usd REAL,
                    stopping_reason TEXT,
                    session_json TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )

            # 5. Audit Records table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_records (
                    audit_id TEXT PRIMARY KEY,
                    entity_type TEXT,
                    entity_id TEXT,
                    action TEXT,
                    actor TEXT,
                    details_json TEXT,
                    created_at TEXT
                )
                """
            )

            # 6. Tasks table (Schema v3)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    title TEXT,
                    assigned_agent TEXT,
                    status TEXT,
                    task_json TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )

            # 7. Change Reviews table (Schema v3)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS change_reviews (
                    change_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    target_file TEXT,
                    change_type TEXT,
                    responsible_agent TEXT,
                    task_id TEXT,
                    diff TEXT,
                    is_applied INTEGER,
                    approval_status TEXT,
                    record_json TEXT,
                    created_at TEXT
                )
                """
            )

            # Record migration version if not already applied
            row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            curr_ver = row[0] if (row and row[0] is not None) else 0
            if curr_ver < self.CURRENT_SCHEMA_VERSION:
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
                    (
                        self.CURRENT_SCHEMA_VERSION,
                        datetime.datetime.utcnow().isoformat(),
                        "Version 3: Added tasks and change_reviews for Autonomous AI Software Development Workspace.",
                    ),
                )
            conn.commit()

    # --------------------------------------------------------------------------
    # Project Management
    # --------------------------------------------------------------------------
    def save_project(self, project: ProjectRecord) -> ProjectRecord:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO projects (
                    project_id, name, repo_path, language, framework,
                    dependencies_json, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project.project_id,
                    project.name,
                    project.repo_path,
                    project.language,
                    project.framework,
                    json.dumps(project.dependencies),
                    json.dumps(project.metadata),
                    project.created_at,
                    project.updated_at,
                ),
            )
            conn.commit()
        return project

    def get_project(self, project_id: str) -> Optional[ProjectRecord]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
            if not row:
                return None
            return ProjectRecord(
                project_id=row["project_id"],
                name=row["name"],
                repo_path=row["repo_path"],
                language=row["language"],
                framework=row["framework"],
                dependencies=json.loads(row["dependencies_json"] or "[]"),
                metadata=json.loads(row["metadata_json"] or "{}"),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def list_projects(self) -> List[ProjectRecord]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
            return [
                ProjectRecord(
                    project_id=r["project_id"],
                    name=r["name"],
                    repo_path=r["repo_path"],
                    language=r["language"],
                    framework=r["framework"],
                    dependencies=json.loads(r["dependencies_json"] or "[]"),
                    metadata=json.loads(r["metadata_json"] or "{}"),
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ]

    # --------------------------------------------------------------------------
    # Test Run Persistence
    # --------------------------------------------------------------------------
    def save_test_run(self, ctx: RunContext) -> RunContext:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO test_runs (
                    run_id, project_path, status, time_budget_seconds,
                    token_budget, used_tokens, run_json, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ctx.run_id,
                    ctx.project_path,
                    ctx.status,
                    ctx.time_budget_seconds,
                    ctx.token_budget,
                    ctx.used_tokens,
                    ctx.model_dump_json(),
                    ctx.start_time,
                    ctx.completed_time,
                ),
            )
            conn.commit()
        return ctx

    def get_test_run(self, run_id: str) -> Optional[RunContext]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT run_json FROM test_runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                return None
            return RunContext.model_validate_json(row["run_json"])

    def list_test_runs(self, limit: int = 50) -> List[RunContext]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT run_json FROM test_runs ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
            return [RunContext.model_validate_json(r["run_json"]) for r in rows]

    # --------------------------------------------------------------------------
    # Completion Session Persistence
    # --------------------------------------------------------------------------
    def save_completion_session(self, ctx: AutonomousCompletionContext) -> AutonomousCompletionContext:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO completion_sessions (
                    session_id, project_path, status, current_iteration,
                    used_cost_usd, stopping_reason, session_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ctx.session_id,
                    ctx.project_path,
                    ctx.status.value,
                    ctx.current_iteration,
                    ctx.used_cost_usd,
                    ctx.stopping_reason,
                    ctx.model_dump_json(),
                    ctx.started_at,
                    ctx.completed_at or datetime.datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
        return ctx

    def get_completion_session(self, session_id: str) -> Optional[AutonomousCompletionContext]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT session_json FROM completion_sessions WHERE session_id = ?", (session_id,)).fetchone()
            if not row:
                return None
            return AutonomousCompletionContext.model_validate_json(row["session_json"])

    def list_completion_sessions(self, limit: int = 50) -> List[AutonomousCompletionContext]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT session_json FROM completion_sessions ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
            return [AutonomousCompletionContext.model_validate_json(r["session_json"]) for r in rows]

    # --------------------------------------------------------------------------
    # Audit Trail
    # --------------------------------------------------------------------------
    def record_audit(self, audit: AuditRecord) -> AuditRecord:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO audit_records (
                    audit_id, entity_type, entity_id, action, actor, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit.audit_id,
                    audit.entity_type,
                    audit.entity_id,
                    audit.action,
                    audit.actor,
                    json.dumps(audit.details),
                    audit.created_at,
                ),
            )
            conn.commit()
        return audit

    def list_audits(self, entity_id: Optional[str] = None, limit: int = 50) -> List[AuditRecord]:
        with self._get_conn() as conn:
            if entity_id:
                rows = conn.execute("SELECT * FROM audit_records WHERE entity_id = ? ORDER BY rowid DESC LIMIT ?", (entity_id, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM audit_records ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
            return [
                AuditRecord(
                    audit_id=r["audit_id"],
                    entity_type=r["entity_type"],
                    entity_id=r["entity_id"],
                    action=r["action"],
                    actor=r["actor"],
                    details=json.loads(r["details_json"] or "{}"),
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    # ==========================================================================
    # Task Graph Persistence (Schema v3)
    # ==========================================================================

    def save_task(self, task: TaskNode) -> TaskNode:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks (
                    task_id, project_id, title, assigned_agent, status, task_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.project_id,
                    task.title,
                    task.assigned_agent.value,
                    task.status.value,
                    task.model_dump_json(),
                    task.created_at,
                    datetime.datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
        return task

    def get_task(self, task_id: str) -> Optional[TaskNode]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT task_json FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                return None
            return TaskNode.model_validate_json(row["task_json"])

    def list_tasks(self, project_id: Optional[str] = None, limit: int = 100) -> List[TaskNode]:
        with self._get_conn() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT task_json FROM tasks WHERE project_id = ? ORDER BY rowid ASC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            else:
                rows = conn.execute("SELECT task_json FROM tasks ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
            return [TaskNode.model_validate_json(r["task_json"]) for r in rows]

    # ==========================================================================
    # Change Review Records (Schema v3)
    # ==========================================================================

    def save_change_review(self, change: ChangeReviewRecord) -> ChangeReviewRecord:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO change_reviews (
                    change_id, project_id, target_file, change_type,
                    responsible_agent, task_id, diff, is_applied,
                    approval_status, record_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    change.change_id,
                    change.project_id,
                    change.target_file,
                    change.change_type,
                    change.responsible_agent.value,
                    change.task_id,
                    change.diff,
                    1 if change.is_applied else 0,
                    change.approval_status,
                    change.model_dump_json(),
                    change.timestamp,
                ),
            )
            conn.commit()
        return change

    def list_change_reviews(self, project_id: Optional[str] = None, limit: int = 50) -> List[ChangeReviewRecord]:
        with self._get_conn() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT record_json FROM change_reviews WHERE project_id = ? ORDER BY rowid DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            else:
                rows = conn.execute("SELECT record_json FROM change_reviews ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
            return [ChangeReviewRecord.model_validate_json(r["record_json"]) for r in rows]
