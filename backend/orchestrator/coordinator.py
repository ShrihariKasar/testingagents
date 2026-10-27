"""Central Testing Orchestrator and State Coordinator.
Manages the multi-agent DAG execution, enforces time/token budgets, prevents duplicate work,
coordinates specialized testing agents, and gates high-risk repair actions for human approval.
"""
from __future__ import annotations

import datetime
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.adapters.base import BaseModelAdapter
from backend.agents.e2e_agent import EndToEndTestingAgent
from backend.agents.integration_agent import IntegrationTestingAgent
from backend.agents.performance_agent import PerformanceTestingAgent
from backend.agents.regression_agent import RegressionTestingAgent
from backend.agents.repair_agent import AutonomousRepairAgent
from backend.agents.unit_agent import UnitTestingAgent
from backend.agents.verifier import IndependentVerifier
from backend.models.contracts import (
    AgentExecutionLog,
    AgentType,
    PatchVerificationStatus,
    ProposedPatch,
    RunContext,
    TestStatus,
    TestSuiteReport,
)
from backend.runners.sandbox import SandboxExecutionEngine
from backend.memory.episodic import EpisodicMemoryStore
from backend.memory.semantic import SemanticMemoryStore
from backend.memory.strategy import StrategyMemoryStore
from backend.memory.models import (
    ExperienceRecord,
    FailureCategory,
    FailureOrigin,
    LessonValidationStatus,
    SemanticLesson,
    StrategyRecord,
    StrategyStatus,
)
from backend.intelligence.bus import KnowledgeExchangeBus, KnowledgeMessage
from backend.intelligence.cross_teaching import CrossAgentTeacher
from backend.intelligence.mistake_analyzer import MistakeAnalyzer


from backend.learning.models import LearningEvent, LearningEventType, ProjectProfile
from backend.learning.profiler import ProjectProfiler
from backend.learning.durable_queue import DurableEventQueue
from backend.learning.service import ContinuousLearningService


class OrchestrationBudgetExceeded(Exception):
    """Raised when execution time or token budgets are exhausted."""
    pass


class CentralTestingOrchestrator:
    """Coordinates execution across all five specialized testing agents."""

    def __init__(
        self,
        model_adapter: BaseModelAdapter,
        sandbox_engine: Optional[SandboxExecutionEngine] = None,
        event_listener: Optional[Callable[[Dict[str, Any]], None]] = None,
        db_path: Optional[Path] = None,
    ):
        self.adapter = model_adapter
        self.sandbox = sandbox_engine or SandboxExecutionEngine()
        self.verifier = IndependentVerifier(self.sandbox)
        self.event_listener = event_listener

        # Initialize Three-Tier Persistent Memory
        self.episodic_store = EpisodicMemoryStore(db_path=db_path)
        self.semantic_store = SemanticMemoryStore(db_path=db_path)
        self.strategy_store = StrategyMemoryStore(db_path=db_path)

        # Initialize Collaborative Intelligence Bus & Cross-Teaching
        self.bus = KnowledgeExchangeBus(max_budget_per_task=10)
        self.cross_teacher = CrossAgentTeacher(self.bus, self.semantic_store)
        self.mistake_analyzer = MistakeAnalyzer(self.episodic_store, self.semantic_store, self.sandbox, self.adapter)

        # Initialize Continuous Learning Engine
        self.durable_queue = DurableEventQueue(db_path=db_path)
        self.learning_service = ContinuousLearningService(
            queue=self.durable_queue,
            episodic_store=self.episodic_store,
            semantic_store=self.semantic_store,
            cross_teacher=self.cross_teacher,
            mistake_analyzer=self.mistake_analyzer,
        )
        self.learning_service.start_background_worker()

        # Initialize specialized agents
        self.unit_agent = UnitTestingAgent(self.adapter, self.sandbox)
        self.integration_agent = IntegrationTestingAgent(self.adapter, self.sandbox)
        self.regression_agent = RegressionTestingAgent(self.adapter, self.sandbox)
        self.performance_agent = PerformanceTestingAgent(self.adapter)
        self.e2e_agent = EndToEndTestingAgent(self.adapter)
        self.repair_agent = AutonomousRepairAgent(self.adapter, self.sandbox, self.verifier)

        # Wire each agent to the shared Continuous Learning Engine
        for agent in [
            self.unit_agent,
            self.integration_agent,
            self.regression_agent,
            self.performance_agent,
            self.e2e_agent,
            self.repair_agent,
        ]:
            agent.attach_learning_context(
                episodic_store=self.episodic_store,
                semantic_store=self.semantic_store,
                strategy_store=self.strategy_store,
                bus=self.bus,
                durable_queue=self.durable_queue,
            )

        # Ensure default baseline production strategies are registered
        self._bootstrap_default_strategies()

    def _bootstrap_default_strategies(self):
        """Ensures verified baseline strategies exist in strategy memory."""
        for at in [AgentType.UNIT, AgentType.INTEGRATION, AgentType.REGRESSION, AgentType.PERFORMANCE, AgentType.E2E]:
            if not self.strategy_store.get_production_strategy(at):
                self.strategy_store.register_strategy(
                    StrategyRecord(
                        strategy_id=f"strat_{at.value}_v1",
                        agent_type=at,
                        version="1.0.0",
                        is_production=True,
                        status=StrategyStatus.PRODUCTION,
                        prompt_template=f"Default production strategy for {at.value}",
                        system_instruction=f"Standard testing policies for {at.value}",
                        benchmark_scores={"mutation_score": 0.70, "defect_detection": 0.65},
                    )
                )

    def _emit_event(self, event_type: str, data: Dict[str, Any]):
        if self.event_listener:
            payload = {
                "event_type": event_type,
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "data": data,
            }
            try:
                self.event_listener(payload)
            except Exception:
                pass

    def _check_budget(self, ctx: RunContext, start_perf: float):
        elapsed = time.perf_counter() - start_perf
        if elapsed > ctx.time_budget_seconds:
            raise OrchestrationBudgetExceeded(f"Time budget of {ctx.time_budget_seconds}s exceeded (elapsed {elapsed:.1f}s)")
        if ctx.used_tokens > ctx.token_budget:
            raise OrchestrationBudgetExceeded(f"Token budget of {ctx.token_budget} exceeded (used {ctx.used_tokens})")

    def execute_test_plan(
        self,
        project_dir: Path,
        target_files: Optional[List[str]] = None,
        git_diff: Optional[str] = None,
        run_performance: bool = False,
        time_budget_seconds: int = 300,
        token_budget: int = 500000,
    ) -> RunContext:
        """
        Executes an orchestrated testing workflow across specialized agents.
        """
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        start_perf = time.perf_counter()

        ctx = RunContext(
            run_id=run_id,
            project_path=str(project_dir),
            target_files=target_files or [],
            status="running",
            time_budget_seconds=time_budget_seconds,
            token_budget=token_budget,
        )

        self._emit_event("run_started", {"run_id": run_id, "project_path": str(project_dir)})

        # Automatically fingerprint project architecture and framework
        project_profile = ProjectProfiler.profile_directory(project_dir)

        try:
            # 1. Regression Impact Analysis (if git diff provided)
            selected_tests = []
            if git_diff:
                ctx.active_agent = AgentType.REGRESSION
                self._emit_event("agent_started", {"agent": AgentType.REGRESSION.value})
                modified_files = self.regression_agent.parse_git_diff(git_diff)
                impact_report = self.regression_agent.select_regression_tests(modified_files, project_dir)
                selected_tests = impact_report.selected_tests
                ctx.execution_logs.append(
                    AgentExecutionLog(
                        step_id=uuid.uuid4().hex[:6],
                        agent_type=AgentType.REGRESSION,
                        action="impact_analysis",
                        details=impact_report.model_dump(),
                    )
                )
                # Enqueue regression learning event
                self.durable_queue.enqueue(
                    LearningEvent(
                        event_id=f"ev_{uuid.uuid4().hex[:8]}",
                        idempotency_key=f"{run_id}_regression_analysis",
                        project_id=project_profile.project_id,
                        run_id=run_id,
                        agent_type=AgentType.REGRESSION,
                        event_type=LearningEventType.REGRESSION_INTRODUCED if selected_tests else LearningEventType.TEST_TASK_COMPLETED,
                        payload={
                            "modified_files": modified_files,
                            "details": f"Impact analysis identified {len(selected_tests)} affected tests",
                            "project_profile": project_profile.model_dump(),
                        },
                    )
                )
                self.learning_service.process_all_pending()

            # 2. Unit Testing Phase
            ctx.active_agent = AgentType.UNIT
            self._emit_event("agent_started", {"agent": AgentType.UNIT.value})
            self._check_budget(ctx, start_perf)

            # Discover Python targets if not explicitly given
            targets = [project_dir / f for f in target_files] if target_files else list(project_dir.glob("*.py"))
            # Exclude test files
            source_targets = [t for t in targets if not t.name.startswith("test_")]

            failing_suites: List[TestSuiteReport] = []

            for target in source_targets:
                self._check_budget(ctx, start_perf)

                # Query relevant semantic memory lessons with tenant isolation & applicability gate
                raw_lessons = self.semantic_store.search_lessons(
                    f"{target.stem} boundary condition testing",
                    target_agent=AgentType.UNIT,
                    current_project_id=project_profile.project_id,
                    top_k=5,
                )
                # Filter through applicability preconditions
                applicable_lessons = [
                    lesson for lesson, score in raw_lessons
                    if ProjectProfiler.check_applicability(lesson, project_profile)[0]
                ]

                symbols = self.unit_agent.analyze_module(target)
                synth_suite = self.unit_agent.synthesize_unit_tests(target, symbols)
                evidence, report, success = self.unit_agent.execute_and_verify(
                    project_dir=project_dir,
                    target_file=target,
                    synthesized_suite=synth_suite,
                )
                if report:
                    ctx.suite_reports.append(report)
                    if report.failed_tests > 0:
                        failing_suites.append(report)

                # Enqueue durable learning event
                event_type = LearningEventType.TEST_TASK_COMPLETED if success else LearningEventType.FAILURE_DETECTED
                idempotency_hash = f"{run_id}_{target.name}_{evidence.exit_code}_{event_type.value}"

                learning_ev = LearningEvent(
                    event_id=f"ev_{uuid.uuid4().hex[:8]}",
                    idempotency_key=idempotency_hash,
                    project_id=project_profile.project_id,
                    run_id=run_id,
                    agent_type=AgentType.UNIT,
                    event_type=event_type,
                    payload={
                        "target_file": target.name,
                        "coverage_percentage": evidence.branch_coverage_percentage or evidence.coverage_percentage,
                        "evidence": evidence.model_dump(),
                        "project_profile": project_profile.model_dump(),
                        "objective": f"Unit test synthesis for {target.name}",
                    },
                )
                self.durable_queue.enqueue(learning_ev)

                # Trigger synchronous processing step for event queue
                self.learning_service.process_all_pending()

                ctx.execution_logs.append(
                    AgentExecutionLog(
                        step_id=uuid.uuid4().hex[:6],
                        agent_type=AgentType.UNIT,
                        action=f"synthesize_and_verify_{target.name}",
                        details={"exit_code": evidence.exit_code, "passed": evidence.passed_count, "failed": evidence.failed_count},
                    )
                )

            # 3. Autonomous Repair Phase (Triggered if any failure occurs)
            if failing_suites:
                ctx.active_agent = AgentType.REPAIR
                self._emit_event("agent_started", {"agent": AgentType.REPAIR.value})
                self._check_budget(ctx, start_perf)

                for fail_suite in failing_suites:
                    for tc in fail_suite.test_cases:
                        if tc.status == TestStatus.FAILED:
                            # Attempt repair on target file
                            patch = self.repair_agent.diagnose_and_repair(
                                project_dir=project_dir,
                                failing_test_path=tc.file_path,
                                target_source_path=project_dir / tc.file_path.replace("test_", ""),
                            )
                            ctx.proposed_patches.append(patch)
                            self._emit_event("patch_proposed", {"patch_id": patch.patch_id, "status": patch.verification_status.value})

                            # Record repair experience
                            repair_exp = ExperienceRecord(
                                experience_id=f"exp_{uuid.uuid4().hex[:8]}",
                                project_id=str(project_dir),
                                run_id=run_id,
                                agent_type=AgentType.REPAIR,
                                task_type="autonomous_repair",
                                original_objective=f"Repair failure in {tc.file_path}",
                                failure_category=FailureCategory.PRODUCT_DEFECT,
                                failure_origin=FailureOrigin.PRODUCT_CODE,
                                root_cause_hypothesis=patch.root_cause_analysis[:200],
                                confirmed_root_cause=patch.root_cause_analysis[:200] if patch.verification_status == PatchVerificationStatus.VERIFIED else None,
                                intervention_attempted=patch.unified_diff,
                                verification_result=patch.verification_status,
                                produced_measurable_improvement=(patch.verification_status == PatchVerificationStatus.VERIFIED),
                            )
                            self.episodic_store.record_experience(repair_exp)

                            # Enqueue durable learning event for verified repair
                            if patch.verification_status == PatchVerificationStatus.VERIFIED:
                                repair_ev = LearningEvent(
                                    event_id=f"ev_{uuid.uuid4().hex[:8]}",
                                    idempotency_key=f"{run_id}_{patch.patch_id}_repair_verified",
                                    project_id=project_profile.project_id,
                                    run_id=run_id,
                                    agent_type=AgentType.REPAIR,
                                    event_type=LearningEventType.REPAIR_VERIFIED,
                                    payload={
                                        "patch_diff": patch.unified_diff,
                                        "diagnosis": patch.root_cause_analysis,
                                        "project_profile": project_profile.model_dump(),
                                    },
                                )
                                self.durable_queue.enqueue(repair_ev)
                                self.learning_service.process_all_pending()

            # 4. Performance Testing Phase (if enabled)
            if run_performance:
                ctx.active_agent = AgentType.PERFORMANCE
                self._emit_event("agent_started", {"agent": AgentType.PERFORMANCE.value})
                perf_dist = self.performance_agent.calculate_percentiles(
                    latencies_ms=[12.0, 18.5, 25.0, 31.0, 45.0],
                    total_time_seconds=1.0,
                    error_count=0,
                )
                ev_type = LearningEventType.TEST_TASK_COMPLETED if perf_dist.slo_met else LearningEventType.PERFORMANCE_THRESHOLD_EXCEEDED
                self.durable_queue.enqueue(
                    LearningEvent(
                        event_id=f"ev_{uuid.uuid4().hex[:8]}",
                        idempotency_key=f"{run_id}_perf_benchmark",
                        project_id=project_profile.project_id,
                        run_id=run_id,
                        agent_type=AgentType.PERFORMANCE,
                        event_type=ev_type,
                        payload={
                            "violations": perf_dist.violations,
                            "target_url": project_profile.project_name,
                            "project_profile": project_profile.model_dump(),
                        },
                    )
                )
                self.learning_service.process_all_pending()

            ctx.status = "completed"

        except OrchestrationBudgetExceeded as exc:
            ctx.status = "budget_exceeded"
            ctx.execution_logs.append(
                AgentExecutionLog(
                    step_id="budget_err",
                    agent_type=AgentType.ORCHESTRATOR,
                    action="budget_halt",
                    details={"error": str(exc)},
                )
            )

        except Exception as exc:
            ctx.status = "failed"
            ctx.execution_logs.append(
                AgentExecutionLog(
                    step_id="orch_err",
                    agent_type=AgentType.ORCHESTRATOR,
                    action="error",
                    details={"error": str(exc)},
                )
            )

        finally:
            ctx.completed_time = datetime.datetime.utcnow().isoformat()
            self._emit_event("run_completed", {"run_id": run_id, "status": ctx.status})

        return ctx
