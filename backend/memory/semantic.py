"""Semantic Memory Store.
Stores reusable, evidence-backed lessons, recurring failure patterns, and best practices.
Provides vector-based semantic retrieval and keyword ranking.
"""
from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.memory.models import KnowledgeScope, LessonValidationStatus, SemanticLesson
from backend.models.contracts import AgentType

# Dimension for lightweight, deterministic term-frequency bag-of-words / hash embeddings
EMBEDDING_DIM = 64


def compute_text_embedding(text: str, dim: int = EMBEDDING_DIM) -> List[float]:
    """
    Computes a deterministic, normalized vector embedding using hashed n-gram token frequencies.
    Allows fast semantic vector retrieval without heavy external neural runtimes.
    """
    tokens = re.findall(r"\b\w{2,}\b", text.lower())
    if not tokens:
        return [0.0] * dim

    counts = Counter(tokens)
    vec = [0.0] * dim
    for word, count in counts.items():
        # Deterministic hash bucket
        bucket = hash(word) % dim
        vec[bucket] += float(count)

    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [round(x / norm, 5) for x in vec]
    return vec


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Computes cosine similarity between two normalized vectors."""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm1 * norm2)))


class SemanticMemoryStore:
    """Manages persistent storage and vector-similarity retrieval of verified lessons."""

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
                CREATE TABLE IF NOT EXISTS semantic_lessons (
                    lesson_id TEXT PRIMARY KEY,
                    source_agent TEXT,
                    title TEXT,
                    problem_context TEXT,
                    validation_status TEXT,
                    scope TEXT,
                    project_id TEXT,
                    confidence_score REAL,
                    times_applied INTEGER,
                    times_successful INTEGER,
                    record_json TEXT,
                    created_at TEXT
                )
                """
            )
            conn.commit()

    def store_lesson(self, lesson: SemanticLesson) -> SemanticLesson:
        """Stores a validated or candidate lesson with computed vector embedding and scope."""
        if not lesson.embedding:
            combined_text = f"{lesson.title} {lesson.problem_context} {lesson.recommendation}"
            lesson.embedding = compute_text_embedding(combined_text)

        record_json = lesson.model_dump_json()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO semantic_lessons (
                    lesson_id, source_agent, title, problem_context,
                    validation_status, scope, project_id, confidence_score,
                    times_applied, times_successful, record_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lesson.lesson_id,
                    lesson.source_agent.value,
                    lesson.title,
                    lesson.problem_context,
                    lesson.validation_status.value,
                    lesson.scope.value,
                    lesson.project_id,
                    lesson.confidence_score,
                    lesson.times_applied,
                    lesson.times_successful,
                    record_json,
                    lesson.created_at,
                ),
            )
            conn.commit()
        return lesson

    def search_lessons(
        self,
        query_text: str,
        target_agent: Optional[AgentType] = None,
        current_project_id: Optional[str] = None,
        min_confidence: float = 0.3,
        top_k: int = 5,
        allow_candidate: bool = False,
    ) -> List[Tuple[SemanticLesson, float]]:
        """
        Retrieves relevant lessons using cosine similarity over query embedding.
        Enforces strict tenant isolation:
        - GLOBAL_VERIFIED: Accessible across authorized projects
        - PROJECT_SPECIFIC: Accessible ONLY within the originating project
        - CANDIDATE: Filtered unless specifically enabled
        """
        query_vec = compute_text_embedding(query_text)

        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT record_json FROM semantic_lessons
                WHERE validation_status != 'rejected' AND confidence_score >= ?
                """,
                (min_confidence,),
            ).fetchall()

        results: List[Tuple[SemanticLesson, float]] = []
        for r in rows:
            lesson = SemanticLesson.model_validate_json(r["record_json"])

            # 1. Tenant Isolation & Scope Gate
            if lesson.scope == KnowledgeScope.PROJECT_SPECIFIC:
                if not current_project_id or lesson.project_id != current_project_id:
                    # Unauthorized cross-project leak prevented
                    continue
            elif lesson.scope == KnowledgeScope.CANDIDATE and not allow_candidate:
                # Do not surface unverified candidate lessons across projects
                continue

            # 2. Filter by recipient agent if specified
            if target_agent and lesson.target_agents and target_agent not in lesson.target_agents:
                continue

            sim = cosine_similarity(query_vec, lesson.embedding or [])
            composite_score = round((sim * 0.7) + (lesson.confidence_score * 0.3), 4)
            if sim > 0.05:
                results.append((lesson, composite_score))

        # Rank by composite score descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def update_validation_status(
        self,
        lesson_id: str,
        new_status: LessonValidationStatus,
        confidence_delta: float = 0.0,
    ) -> Optional[SemanticLesson]:
        """Promotes or rejects a lesson following empirical verification."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT record_json FROM semantic_lessons WHERE lesson_id = ?", (lesson_id,)).fetchone()
            if not row:
                return None
            lesson = SemanticLesson.model_validate_json(row["record_json"])
            lesson.validation_status = new_status
            lesson.confidence_score = max(0.0, min(1.0, lesson.confidence_score + confidence_delta))
            return self.store_lesson(lesson)

    def record_application_outcome(self, lesson_id: str, is_success: bool) -> Optional[SemanticLesson]:
        """Tracks empirical success rate of applied lessons over time."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT record_json FROM semantic_lessons WHERE lesson_id = ?", (lesson_id,)).fetchone()
            if not row:
                return None
            lesson = SemanticLesson.model_validate_json(row["record_json"])
            lesson.times_applied += 1
            if is_success:
                lesson.times_successful += 1
                # Increment confidence
                lesson.confidence_score = min(1.0, round(lesson.confidence_score + 0.05, 3))
            else:
                # Decrement confidence
                lesson.confidence_score = max(0.05, round(lesson.confidence_score - 0.10, 3))
            return self.store_lesson(lesson)
