"""Specialized Performance Testing Agent.
Generates load test scenarios, executes statistical latency profiles,
computes P50/P90/P95/P99 percentiles, and detects performance regressions.
"""
from __future__ import annotations

import asyncio
import statistics
import time
from typing import Dict, List, Optional
import httpx
from pydantic import BaseModel, Field

from backend.adapters.base import BaseModelAdapter
from backend.models.contracts import AgentType
from backend.learning.agent_interface import BaseContinuousLearningAgent


class PerformanceThresholds(BaseModel):
    max_p95_latency_ms: float = 250.0
    max_p99_latency_ms: float = 500.0
    max_error_rate_pct: float = 1.0


class LatencyDistribution(BaseModel):
    sample_count: int
    min_ms: float
    max_ms: float
    avg_ms: float
    p50_ms: float
    p90_ms: float
    p95_ms: float
    p99_ms: float
    requests_per_second: float
    error_count: int
    error_rate_pct: float
    slo_met: bool
    violations: List[str] = Field(default_factory=list)


class PerformanceTestingAgent(BaseContinuousLearningAgent):
    """Coordinates load testing and latency percentile benchmarking."""

    def __init__(self, model_adapter: Optional[BaseModelAdapter] = None):
        super().__init__(agent_type=AgentType.PERFORMANCE)
        self.adapter = model_adapter

    def calculate_percentiles(
        self,
        latencies_ms: List[float],
        total_time_seconds: float,
        error_count: int = 0,
        thresholds: Optional[PerformanceThresholds] = None,
    ) -> LatencyDistribution:
        """Computes statistical metrics and evaluates SLO thresholds."""
        if not latencies_ms:
            return LatencyDistribution(
                sample_count=0,
                min_ms=0,
                max_ms=0,
                avg_ms=0,
                p50_ms=0,
                p90_ms=0,
                p95_ms=0,
                p99_ms=0,
                requests_per_second=0,
                error_count=error_count,
                error_rate_pct=100.0 if error_count > 0 else 0.0,
                slo_met=False,
                violations=["No successful samples collected"],
            )

        sorted_lat = sorted(latencies_ms)
        n = len(sorted_lat)
        total_samples = n + error_count

        def get_p(pct: float) -> float:
            idx = int(n * (pct / 100.0))
            return sorted_lat[min(idx, n - 1)]

        p50 = get_p(50.0)
        p90 = get_p(90.0)
        p95 = get_p(95.0)
        p99 = get_p(99.0)
        avg = round(statistics.mean(sorted_lat), 2)
        rps = round(total_samples / max(total_time_seconds, 0.001), 2)
        error_rate = round((error_count / total_samples) * 100.0, 2)

        violations: List[str] = []
        slo_met = True

        t = thresholds or PerformanceThresholds()
        if p95 > t.max_p95_latency_ms:
            slo_met = False
            violations.append(f"P95 latency {p95}ms exceeds threshold {t.max_p95_latency_ms}ms")
        if p99 > t.max_p99_latency_ms:
            slo_met = False
            violations.append(f"P99 latency {p99}ms exceeds threshold {t.max_p99_latency_ms}ms")
        if error_rate > t.max_error_rate_pct:
            slo_met = False
            violations.append(f"Error rate {error_rate}% exceeds threshold {t.max_error_rate_pct}%")

        return LatencyDistribution(
            sample_count=total_samples,
            min_ms=round(sorted_lat[0], 2),
            max_ms=round(sorted_lat[-1], 2),
            avg_ms=avg,
            p50_ms=round(p50, 2),
            p90_ms=round(p90, 2),
            p95_ms=round(p95, 2),
            p99_ms=round(p99, 2),
            requests_per_second=rps,
            error_count=error_count,
            error_rate_pct=error_rate,
            slo_met=slo_met,
            violations=violations,
        )

    async def run_benchmark(
        self,
        target_url: str,
        total_requests: int = 100,
        concurrency: int = 10,
        thresholds: Optional[PerformanceThresholds] = None,
    ) -> LatencyDistribution:
        """Executes concurrent asynchronous HTTP requests and samples latency distributions."""
        latencies: List[float] = []
        error_count = 0
        semaphore = asyncio.Semaphore(concurrency)

        start_total = time.perf_counter()

        async def fetch(client: httpx.AsyncClient):
            nonlocal error_count
            async with semaphore:
                start = time.perf_counter()
                try:
                    resp = await client.get(target_url, timeout=10.0)
                    duration_ms = (time.perf_counter() - start) * 1000.0
                    if resp.status_code < 400:
                        latencies.append(duration_ms)
                    else:
                        error_count += 1
                except Exception:
                    error_count += 1

        async with httpx.AsyncClient() as client:
            tasks = [asyncio.create_task(fetch(client)) for _ in range(total_requests)]
            await asyncio.gather(*tasks)

        total_time = time.perf_counter() - start_total
        return self.calculate_percentiles(latencies, total_time, error_count, thresholds)
