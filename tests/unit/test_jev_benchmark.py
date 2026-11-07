"""Stage 11: benchmark harness measures both paths honestly."""
import asyncio

from src.orchestrator.jev.base import JevDecisionKind
from src.orchestrator.jev.benchmark import (
    BenchmarkTask,
    compare_paths,
    representative_tasks,
    run_suite,
)
from src.orchestrator.jev.mock_adapter import MockJevAdapter
from src.orchestrator.jev.router import JevDecisionRouter, JevRouterConfig


def _jev_decide_fn(router):
    async def _decide(task):
        if task.kind == JevDecisionKind.TASK_CLASSIFICATION:
            out = await router.classify_task(task.description, fallback=task.fallback)
        elif task.kind == JevDecisionKind.AGENT_SELECTION:
            out = await router.select_agent(task.description, task.candidates, task.fallback)
        elif task.kind == JevDecisionKind.MODEL_SELECTION:
            out = await router.select_model(task.description, task.candidates, task.fallback)
        elif task.kind == JevDecisionKind.TOOL_SELECTION:
            out = await router.select_tool(task.description, task.candidates, task.fallback)
        elif task.kind == JevDecisionKind.WORKFLOW_BRANCH:
            out = await router.route_workflow(task.description, task.candidates, task.fallback)
        else:
            out = await router.assess_escalation(task.description, fallback=task.fallback)
        return out.selected, out.source

    return _decide


def test_representative_task_set_shape():
    tasks = representative_tasks()
    assert len(tasks) == 8
    assert {t.task_id for t in tasks} == {
        "t-simple-qa", "t-coding", "t-research", "t-agent",
        "t-model", "t-tool", "t-recovery", "t-approval",
    }
    for task in tasks:
        assert task.expected in task.candidates
        assert task.fallback in task.candidates


def test_run_suite_reports_measured_rates():
    async def _run():
        router = JevDecisionRouter(MockJevAdapter(simulated_latency_ms=0), JevRouterConfig())
        report = await run_suite("jev", _jev_decide_fn(router), representative_tasks())
        assert report.tasks_run == 8
        assert 0.0 <= report.correctness_rate <= 1.0
        assert 0.0 <= report.fallback_rate <= 1.0
        assert 0.0 <= report.invalid_rate <= 1.0
        assert report.invalid_rate == 0.0
        assert report.avg_latency_ms >= 0.0
        assert report.total_cost_usd >= 0.0

    asyncio.run(_run())


def test_compare_paths_baseline_is_fallback_only():
    async def _run():
        router = JevDecisionRouter(MockJevAdapter(simulated_latency_ms=0), JevRouterConfig())
        comparison = await compare_paths(_jev_decide_fn(router), representative_tasks())
        assert comparison["baseline"].fallback_rate == 1.0
        assert comparison["baseline"].total_cost_usd == 0.0
        assert comparison["jev"].tasks_run == comparison["baseline"].tasks_run == 8

    asyncio.run(_run())


def test_empty_suite_reports_zeros():
    async def _run():
        router = JevDecisionRouter(MockJevAdapter(simulated_latency_ms=0), JevRouterConfig())
        report = await run_suite("empty", _jev_decide_fn(router), [])
        assert report.tasks_run == 0 and report.correctness_rate == 0.0

    asyncio.run(_run())
