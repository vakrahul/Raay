"""JEV benchmarking harness (Stage 11).

Compares the JEV-enabled decision path against the existing system
baseline on a fixed, representative task set. Every figure is measured;
nothing here claims superiority — the mock provider is simulated, so
these benchmarks validate integration plumbing, fallback behavior, and
measurement itself, not real model quality.
"""

import time
from typing import Any, Callable, Dict, List

from pydantic import BaseModel, ConfigDict

from src.orchestrator.jev.base import JevDecisionKind
from src.orchestrator.jev.usage_tracker import estimate_jev_cost_usd


class BenchmarkTask(BaseModel):
    """One representative decision with a defined correct answer."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    description: str
    kind: JevDecisionKind
    candidates: List[str]
    expected: str
    fallback: str


class TaskOutcome(BaseModel):
    """Measured outcome of one benchmark task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    selected: str
    source: str
    correct: bool
    valid: bool
    latency_ms: float
    estimated_cost_usd: float


class BenchmarkReport(BaseModel):
    """Aggregate rates over a benchmark run. All fields measured."""

    model_config = ConfigDict(extra="forbid")

    label: str
    tasks_run: int
    completion_rate: float
    correctness_rate: float
    avg_latency_ms: float
    total_cost_usd: float
    invalid_rate: float
    fallback_rate: float


def _aggregate(label: str, outcomes: List[TaskOutcome]) -> BenchmarkReport:
    n = len(outcomes)
    if n == 0:
        return BenchmarkReport(
            label=label, tasks_run=0, completion_rate=0.0, correctness_rate=0.0,
            avg_latency_ms=0.0, total_cost_usd=0.0, invalid_rate=0.0, fallback_rate=0.0,
        )
    return BenchmarkReport(
        label=label,
        tasks_run=n,
        completion_rate=sum(1 for o in outcomes if o.selected) / n,
        correctness_rate=sum(1 for o in outcomes if o.correct) / n,
        avg_latency_ms=sum(o.latency_ms for o in outcomes) / n,
        total_cost_usd=sum(o.estimated_cost_usd for o in outcomes),
        invalid_rate=sum(1 for o in outcomes if not o.valid) / n,
        fallback_rate=sum(1 for o in outcomes if o.source == "fallback") / n,
    )


async def run_suite(
    label: str,
    decide_fn: Callable[[BenchmarkTask], Any],
    tasks: List[BenchmarkTask],
) -> BenchmarkReport:
    """Run one decision path over the task set and measure everything."""
    outcomes: List[TaskOutcome] = []
    for task in tasks:
        started = time.perf_counter()
        try:
            selected, source = await decide_fn(task)
            valid = selected in task.candidates
        except Exception:
            selected, source, valid = task.fallback, "fallback", task.fallback in task.candidates
        latency_ms = (time.perf_counter() - started) * 1000.0
        outcomes.append(TaskOutcome(
            task_id=task.task_id,
            selected=selected,
            source=source,
            correct=(selected == task.expected),
            valid=valid,
            latency_ms=latency_ms,
            estimated_cost_usd=estimate_jev_cost_usd(len(task.description)) if source == "jev" else 0.0,
        ))
    return _aggregate(label, outcomes)


def representative_tasks() -> List[BenchmarkTask]:
    """Fixed task set spanning question, coding, research, planning,
    selection, recovery, and approval scenarios."""
    return [
        BenchmarkTask(task_id="t-simple-qa", description="What time is it?",
                      kind=JevDecisionKind.TASK_CLASSIFICATION,
                      candidates=["simple", "moderate", "complex"],
                      expected="simple", fallback="moderate"),
        BenchmarkTask(task_id="t-coding", description="Refactor the authentication architecture for deployment.",
                      kind=JevDecisionKind.TASK_CLASSIFICATION,
                      candidates=["simple", "moderate", "complex"],
                      expected="complex", fallback="moderate"),
        BenchmarkTask(task_id="t-research", description="Research three vendors and compare pricing.",
                      kind=JevDecisionKind.TASK_CLASSIFICATION,
                      candidates=["simple", "moderate", "complex"],
                      expected="moderate", fallback="moderate"),
        BenchmarkTask(task_id="t-agent", description="Fix the failing pytest suite.",
                      kind=JevDecisionKind.AGENT_SELECTION,
                      candidates=["agt_qa", "agt_researcher"],
                      expected="agt_qa", fallback="agt_researcher"),
        BenchmarkTask(task_id="t-model", description="Hi",
                      kind=JevDecisionKind.MODEL_SELECTION,
                      candidates=["gemini-3.1-flash-lite", "gpt-4o"],
                      expected="gemini-3.1-flash-lite", fallback="gemini-3.1-flash-lite"),
        BenchmarkTask(task_id="t-tool", description="Read the config file.",
                      kind=JevDecisionKind.TOOL_SELECTION,
                      candidates=["fs_read_file", "terminal_run"],
                      expected="fs_read_file", fallback="fs_read_file"),
        BenchmarkTask(task_id="t-recovery", description="Timeout on first attempt, decide next step.",
                      kind=JevDecisionKind.WORKFLOW_BRANCH,
                      candidates=["retry", "fallback", "abort"],
                      expected="retry", fallback="abort"),
        BenchmarkTask(task_id="t-approval", description="Delete the production database now.",
                      kind=JevDecisionKind.GUARDRAIL_ASSESSMENT,
                      candidates=["proceed", "escalate"],
                      expected="escalate", fallback="escalate"),
    ]


async def compare_paths(
    jev_decide_fn: Callable[[BenchmarkTask], Any],
    tasks: List[BenchmarkTask],
) -> Dict[str, BenchmarkReport]:
    """Run the JEV path and the existing-system baseline over one task set.

    Baseline = current behavior without JEV (caller-supplied fallbacks).
    """

    async def _baseline(task: BenchmarkTask):
        return task.fallback, "fallback"

    return {
        "jev": await run_suite("jev-enabled", jev_decide_fn, tasks),
        "baseline": await run_suite("existing-system", _baseline, tasks),
    }
