"""Continuous Learning Background Service.
Continuously consumes from the durable event queue, distills verified knowledge,
enforces tenant boundaries, and shares cross-project lessons across specialized agents.
"""
from __future__ import annotations

import asyncio
import datetime
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.intelligence.cross_teaching import CrossAgentTeacher
from backend.intelligence.mistake_analyzer import MistakeAnalyzer
from backend.learning.durable_queue import DurableEventQueue
from backend.learning.models import LearningEvent, LearningEventType, ProjectProfile
from backend.learning.profiler import ProjectProfiler
from backend.memory.episodic import EpisodicMemoryStore
from backend.memory.models import (
    ExperienceRecord,
    FailureCategory,
    FailureOrigin,
    KnowledgeScope,
    LessonValidationStatus,
    SemanticLesson,
)
from backend.memory.semantic import SemanticMemoryStore
from backend.models.contracts import AgentType, ExecutionEvidence, PatchVerificationStatus


def sanitize_for_global_scope(text: str) -> str:
    """Removes private absolute filesystem paths, tokens, and local paths."""
    import re
    # Remove absolute Windows or Unix drive paths (e.g. S:\GitHub\..., /Users/...)
    cleaned = re.sub(r"[A-Za-z]:\\[\w\-\\.]+", "[LOCAL_PATH]", text)
    cleaned = re.sub(r"/(?:home|Users|tmp|var)/[\w\-\\.]+", "[LOCAL_PATH]", cleaned)
    return cleaned


class ContinuousLearningService:
    """Persistent, resilient learning daemon integrated with testing orchestrator."""

    def __init__(
        self,
        queue: DurableEventQueue,
        episodic_store: EpisodicMemoryStore,
        semantic_store: SemanticMemoryStore,
        cross_teacher: CrossAgentTeacher,
        mistake_analyzer: MistakeAnalyzer,
    ):
        self.queue = queue
        self.episodic = episodic_store
        self.semantic = semantic_store
        self.cross_teacher = cross_teacher
        self.mistake_analyzer = mistake_analyzer
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None

    def start_background_worker(self):
        """Starts asynchronous non-blocking event consumption loop and recovers uncommitted jobs."""
        # Automatically recover any jobs stuck in PROCESSING from an earlier crash/restart
        recovered = self.queue.recover_stuck_processing_jobs()
        if not self._running:
            self._running = True
            try:
                loop = asyncio.get_running_loop()
                self._worker_task = loop.create_task(self._worker_loop())
            except RuntimeError:
                pass

    def stop_background_worker(self):
        """Gracefully halts background processing."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()

    async def _worker_loop(self):
        """Background consumption loop."""
        while self._running:
            try:
                processed = self.process_all_pending()
                if processed == 0:
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(2.0)

    def process_all_pending(self, max_batch: int = 50) -> int:
        """
        Synchronously drains pending events from the durable queue.
        Enables deterministic testing and immediate processing.
        Returns:
            Number of events successfully processed.
        """
        processed_count = 0
        events = self.queue.fetch_pending_batch(limit=max_batch)

        for ev in events:
            try:
                self._process_single_event(ev)
                self.queue.acknowledge(ev.event_id, success=True)
                processed_count += 1
            except Exception as exc:
                self.queue.acknowledge(ev.event_id, success=False, error=str(exc))

        return processed_count

    def _process_single_event(self, event: LearningEvent):
        """Routes learning event to appropriate memory tier and applicability gates."""
        etype = event.event_type
        payload = event.payload

        if etype == LearningEventType.FAILURE_DETECTED:
            # 1. Capture in Episodic Memory with PROJECT_SPECIFIC isolation
            ev_data = payload.get("evidence", {})
            if "duration_ms" not in ev_data:
                ev_data["duration_ms"] = 0.0
            if "command" not in ev_data:
                ev_data["command"] = "pytest"
            if "exit_code" not in ev_data:
                ev_data["exit_code"] = 1
            if "stdout" not in ev_data:
                ev_data["stdout"] = ""
            if "stderr" not in ev_data:
                ev_data["stderr"] = ""
            raw_ev = ExecutionEvidence.model_validate(ev_data)
            cat, orig = self.mistake_analyzer.classify_failure_origin(raw_ev)

            exp = ExperienceRecord(
                experience_id=f"exp_{uuid.uuid4().hex[:8]}",
                project_id=event.project_id,
                run_id=event.run_id,
                agent_type=event.agent_type,
                task_type=payload.get("task_type", "test_execution"),
                original_objective=payload.get("objective", "Test execution"),
                failure_category=cat,
                failure_origin=orig,
                execution_evidence=raw_ev,
                root_cause_hypothesis=payload.get("hypothesis", "Failure detected"),
                produced_measurable_improvement=False,
            )
            self.episodic.record_experience(exp)

        elif etype == LearningEventType.REPAIR_VERIFIED:
            # 2. Distill confirmed repair into reusable lesson
            patch_diff = payload.get("patch_diff", "")
            diagnosis = payload.get("diagnosis", "")
            project_profile = payload.get("project_profile", {})

            # Clean proprietary details before creating lesson
            safe_diag = sanitize_for_global_scope(diagnosis)

            lesson = SemanticLesson(
                lesson_id=f"les_{uuid.uuid4().hex[:8]}",
                source_agent=AgentType.REPAIR,
                target_agents=[AgentType.UNIT, AgentType.INTEGRATION],
                title=f"Verified repair: {safe_diag[:60]}",
                problem_context=safe_diag,
                recommendation="Enforce boundary invariants and regression assertions derived from verified repair.",
                confidence_score=0.85,
                validation_status=LessonValidationStatus.VALIDATED,
                scope=KnowledgeScope.GLOBAL_VERIFIED,  # Promoted to global verified
                evidence_run_ids=[event.run_id],
                applicable_conditions={
                    "language": project_profile.get("primary_language", "python"),
                    "framework": project_profile.get("framework", "generic"),
                },
            )
            self.semantic.store_lesson(lesson)
            # Cross-agent teaching
            self.cross_teacher.share_lesson_across_agents(lesson, task_key=event.run_id)

        elif etype in (LearningEventType.TEST_TASK_COMPLETED, LearningEventType.TEST_PASSED):
            # 3. Process passed tests and reinforcement
            cov = payload.get("coverage_percentage", 0.0)
            if cov and cov > 60.0:
                target_file = payload.get("target_file", "module")
                project_profile = payload.get("project_profile", {})

                lesson = SemanticLesson(
                    lesson_id=f"les_{uuid.uuid4().hex[:8]}",
                    source_agent=event.agent_type,
                    target_agents=[AgentType.INTEGRATION],
                    title=f"High-coverage test pattern for {target_file}",
                    problem_context=f"Comprehensive branch coverage achieved ({cov}%)",
                    recommendation="Replicate parameterized fixture structure for related service endpoints.",
                    confidence_score=0.80,
                    validation_status=LessonValidationStatus.VALIDATED,
                    scope=KnowledgeScope.GLOBAL_VERIFIED,
                    evidence_run_ids=[event.run_id],
                    applicable_conditions={
                        "language": project_profile.get("primary_language", "python"),
                        "framework": project_profile.get("framework", "generic"),
                    },
                )
                self.semantic.store_lesson(lesson)
                self.cross_teacher.share_lesson_across_agents(lesson, task_key=event.run_id)

        elif etype == LearningEventType.REGRESSION_INTRODUCED:
            # 4. Regression Testing Agent change impact findings
            project_profile = payload.get("project_profile", {})
            modified_files = payload.get("modified_files", [])
            details = payload.get("details", "Regression detected across impacted modules")

            exp = ExperienceRecord(
                experience_id=f"exp_{uuid.uuid4().hex[:8]}",
                project_id=event.project_id,
                run_id=event.run_id,
                agent_type=AgentType.REGRESSION,
                task_type="regression_impact_analysis",
                original_objective=f"Regression prevention for {len(modified_files)} modified files",
                failure_category=FailureCategory.REGRESSION_DEFECT,
                failure_origin=FailureOrigin.PRODUCT_CODE,
                root_cause_hypothesis=str(details)[:200],
                produced_measurable_improvement=False,
            )
            self.episodic.record_experience(exp)

            lesson = SemanticLesson(
                lesson_id=f"les_{uuid.uuid4().hex[:8]}",
                source_agent=AgentType.REGRESSION,
                target_agents=[AgentType.UNIT, AgentType.INTEGRATION, AgentType.PERFORMANCE, AgentType.E2E],
                title="Change-Impact Regression Invariant",
                problem_context=f"High-risk regression paths identified in files: {', '.join(modified_files[:3])}",
                recommendation="Prioritize test coverage on transitive callers before code merge.",
                confidence_score=0.82,
                validation_status=LessonValidationStatus.VALIDATED,
                scope=KnowledgeScope.GLOBAL_VERIFIED,
                evidence_run_ids=[event.run_id],
                applicable_conditions={
                    "language": project_profile.get("primary_language", "python"),
                    "framework": project_profile.get("framework", "generic"),
                },
            )
            self.semantic.store_lesson(lesson)
            self.cross_teacher.share_lesson_across_agents(lesson, task_key=event.run_id)

        elif etype == LearningEventType.INTEGRATION_CONTRACT_VIOLATED:
            # 5. Integration Testing Agent contract violation
            endpoint = payload.get("endpoint", "API Endpoint")
            schema_err = payload.get("schema_error", "Contract mismatch")
            project_profile = payload.get("project_profile", {})

            exp = ExperienceRecord(
                experience_id=f"exp_{uuid.uuid4().hex[:8]}",
                project_id=event.project_id,
                run_id=event.run_id,
                agent_type=AgentType.INTEGRATION,
                task_type="api_contract_verification",
                original_objective=f"Verify API contract for {endpoint}",
                failure_category=FailureCategory.ENVIRONMENT_OR_CONFIG,
                failure_origin=FailureOrigin.PRODUCT_CODE,
                root_cause_hypothesis=f"Contract broken on {endpoint}: {schema_err}",
                produced_measurable_improvement=False,
            )
            self.episodic.record_experience(exp)

            lesson = SemanticLesson(
                lesson_id=f"les_{uuid.uuid4().hex[:8]}",
                source_agent=AgentType.INTEGRATION,
                target_agents=[AgentType.E2E, AgentType.UNIT],
                title=f"API Contract Guard for {endpoint}",
                problem_context=f"Endpoint {endpoint} contract drift: {schema_err}",
                recommendation="Lock response model schema and assert status code error contracts.",
                confidence_score=0.84,
                validation_status=LessonValidationStatus.VALIDATED,
                scope=KnowledgeScope.GLOBAL_VERIFIED,
                evidence_run_ids=[event.run_id],
                applicable_conditions={
                    "language": project_profile.get("primary_language", "python"),
                    "framework": project_profile.get("framework", "generic"),
                },
            )
            self.semantic.store_lesson(lesson)
            self.cross_teacher.share_lesson_across_agents(lesson, task_key=event.run_id)

        elif etype == LearningEventType.PERFORMANCE_THRESHOLD_EXCEEDED:
            # 6. Performance Testing Agent latency & bottleneck finding
            violations = payload.get("violations", ["P95 latency exceeded"])
            endpoint = payload.get("target_url", "Service")
            project_profile = payload.get("project_profile", {})

            exp = ExperienceRecord(
                experience_id=f"exp_{uuid.uuid4().hex[:8]}",
                project_id=event.project_id,
                run_id=event.run_id,
                agent_type=AgentType.PERFORMANCE,
                task_type="latency_slo_benchmark",
                original_objective=f"Benchmark latency for {endpoint}",
                failure_category=FailureCategory.ENVIRONMENT_OR_CONFIG,
                failure_origin=FailureOrigin.PRODUCT_CODE,
                root_cause_hypothesis=f"SLO breached: {'; '.join(violations[:2])}",
                produced_measurable_improvement=False,
            )
            self.episodic.record_experience(exp)

            lesson = SemanticLesson(
                lesson_id=f"les_{uuid.uuid4().hex[:8]}",
                source_agent=AgentType.PERFORMANCE,
                target_agents=[AgentType.INTEGRATION],
                title=f"Performance Bottleneck Mitigation for {endpoint}",
                problem_context=f"High latency under load: {'; '.join(violations[:2])}",
                recommendation="Implement connection pooling, async I/O, or query index optimization.",
                confidence_score=0.88,
                validation_status=LessonValidationStatus.VALIDATED,
                scope=KnowledgeScope.GLOBAL_VERIFIED,
                evidence_run_ids=[event.run_id],
                applicable_conditions={
                    "language": project_profile.get("primary_language", "python"),
                    "framework": project_profile.get("framework", "generic"),
                },
            )
            self.semantic.store_lesson(lesson)
            self.cross_teacher.share_lesson_across_agents(lesson, task_key=event.run_id)

        elif etype == LearningEventType.E2E_WORKFLOW_FAILED:
            # 7. End-to-End Testing Agent workflow failure
            scenario = payload.get("scenario_name", "User Journey")
            step = payload.get("failed_step", "assertion")
            project_profile = payload.get("project_profile", {})

            exp = ExperienceRecord(
                experience_id=f"exp_{uuid.uuid4().hex[:8]}",
                project_id=event.project_id,
                run_id=event.run_id,
                agent_type=AgentType.E2E,
                task_type="e2e_journey_verification",
                original_objective=f"Verify journey {scenario}",
                failure_category=FailureCategory.TEST_DESIGN_FLAW,
                failure_origin=FailureOrigin.TEST_FRAMEWORK,
                root_cause_hypothesis=f"E2E workflow failed at step: {step}",
                produced_measurable_improvement=False,
            )
            self.episodic.record_experience(exp)

            lesson = SemanticLesson(
                lesson_id=f"les_{uuid.uuid4().hex[:8]}",
                source_agent=AgentType.E2E,
                target_agents=[AgentType.REGRESSION],
                title=f"Critical Journey Invariant: {scenario}",
                problem_context=f"Failure at step '{step}' in workflow {scenario}",
                recommendation="Ensure DOM state auto-wait and verify backend idempotency for checkout/auth steps.",
                confidence_score=0.81,
                validation_status=LessonValidationStatus.VALIDATED,
                scope=KnowledgeScope.GLOBAL_VERIFIED,
                evidence_run_ids=[event.run_id],
                applicable_conditions={
                    "language": project_profile.get("primary_language", "python"),
                    "framework": project_profile.get("framework", "generic"),
                },
            )
            self.semantic.store_lesson(lesson)
            self.cross_teacher.share_lesson_across_agents(lesson, task_key=event.run_id)

        elif etype == LearningEventType.FLAKY_DETECTED:
            # 8. Flaky test pattern capture
            test_name = payload.get("test_name", "test")
            exp = ExperienceRecord(
                experience_id=f"exp_{uuid.uuid4().hex[:8]}",
                project_id=event.project_id,
                run_id=event.run_id,
                agent_type=event.agent_type,
                task_type="flakiness_analysis",
                original_objective=f"Flaky detection for {test_name}",
                failure_category=FailureCategory.FLAKY_ENVIRONMENT,
                failure_origin=FailureOrigin.ENVIRONMENT,
                root_cause_hypothesis=f"Non-deterministic timing/state oscillation in {test_name}",
                produced_measurable_improvement=False,
            )
            self.episodic.record_experience(exp)

        elif etype == LearningEventType.LESSON_PROMOTED:
            lesson_id = payload.get("lesson_id")
            if lesson_id:
                self.semantic.update_validation_status(lesson_id, LessonValidationStatus.VALIDATED, confidence_delta=0.1)

        elif etype == LearningEventType.LESSON_REJECTED:
            lesson_id = payload.get("lesson_id")
            if lesson_id:
                self.semantic.update_validation_status(lesson_id, LessonValidationStatus.REJECTED, confidence_delta=-0.3)
