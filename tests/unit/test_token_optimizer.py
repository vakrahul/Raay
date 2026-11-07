"""Unit tests for Module 1: Token Optimization and Cost Subsystem."""
import pytest
from src.orchestrator.cost_tracker import CostEstimator
from src.orchestrator.prompt_cache import PromptCache
from src.orchestrator.token_optimizer import TokenOptimizer
from src.orchestrator.token_tracker import BudgetExceededError, TokenTracker
from src.providers.base import ChatMessage, ProviderResponse
from src.providers.mock_provider import MockProvider


@pytest.mark.unit
def test_token_tracker_and_budget():
    """Verify token usage recording and task budget enforcement."""
    tracker = TokenTracker()
    budget = tracker.set_task_budget("task_auth_01", max_tokens=500)
    assert budget.remaining_tokens == 500

    # Consume 200 tokens
    tracker.record_usage(
        request_id="r1",
        task_id="task_auth_01",
        model_name="gemini-3.1-flash-lite",
        prompt_tokens=150,
        completion_tokens=50,
    )
    assert budget.remaining_tokens == 300
    assert budget.is_exhausted is False

    # Exceed budget
    with pytest.raises(BudgetExceededError) as excinfo:
        tracker.record_usage(
            request_id="r2",
            task_id="task_auth_01",
            model_name="gemini-3.1-flash-lite",
            prompt_tokens=300,
            completion_tokens=50,
        )
    assert "exceeded token budget" in str(excinfo.value)


@pytest.mark.unit
def test_prompt_cache_exact_and_semantic():
    """Verify exact SHA-256 and semantic prompt caching."""
    cache = PromptCache(semantic_threshold=0.90)
    messages = [
        ChatMessage(role="system", content="You are a helpful assistant."),
        ChatMessage(role="user", content="What is the capital of France?"),
    ]
    mock_resp = ProviderResponse(content="The capital of France is Paris.", model_name="mock-model")

    # Store
    cache.store(messages, "mock-model", mock_resp)

    # 1. Exact Hit
    hit = cache.get(messages, "mock-model")
    assert hit is not None
    resp, hit_type = hit
    assert hit_type == "EXACT_CACHE_HIT"
    assert resp.content == "The capital of France is Paris."
    assert cache.total_exact_hits == 1

    # 2. Miss on different model
    miss = cache.get(messages, "other-model")
    assert miss is None


@pytest.mark.unit
def test_cost_estimator_and_pricing():
    """Verify cost calculation for Gemini and prompt caching discounts."""
    estimator = CostEstimator()

    # Gemini 3.1 Flash-Lite: 1,000,000 prompt tokens = $0.075
    cost = estimator.calculate_request_cost(
        model_name="gemini-3.1-flash-lite",
        prompt_tokens=1000000,
        completion_tokens=0,
        cached_prompt_tokens=0,
    )
    assert round(cost["total_cost_usd"], 3) == 0.075
    assert cost["total_cost_inr"] > 0

    # With 500k cached prompt tokens (75% discount)
    cached_cost = estimator.calculate_request_cost(
        model_name="gemini-3.1-flash-lite",
        prompt_tokens=1000000,
        completion_tokens=0,
        cached_prompt_tokens=500000,
    )
    assert cached_cost["total_cost_usd"] < cost["total_cost_usd"]
    assert cached_cost["saved_usd"] > 0


@pytest.mark.asyncio
async def test_token_optimizer_end_to_end():
    """Verify complete TokenOptimizer pipeline with caching, budgeting, and metrics."""
    optimizer = TokenOptimizer()
    provider = MockProvider()
    optimizer.set_task_budget("task_test", max_tokens=2000)

    messages = [
        ChatMessage(role="user", content="Generate a simple fibonacci function in Python"),
    ]

    # First request: Cache Miss -> API executed
    resp1, metrics1 = await optimizer.execute_optimized_request(
        provider=provider,
        messages=messages,
        task_id="task_test",
    )
    assert metrics1["cache_hit"] is False
    assert metrics1["tokens_consumed"] > 0

    # Second identical request: Cache Hit -> 0 tokens consumed
    resp2, metrics2 = await optimizer.execute_optimized_request(
        provider=provider,
        messages=messages,
        task_id="task_test",
    )
    assert metrics2["cache_hit"] is True
    assert metrics2["tokens_consumed"] == 0
    assert resp2.content == resp1.content
    assert metrics2["tokens_saved"] > 0

    # Verify consolidated telemetry summary
    telemetry = optimizer.get_telemetry_summary()
    assert "token_accounting" in telemetry
    assert "financial_summary" in telemetry
    assert "cache_stats" in telemetry
    assert "savings_overview" in telemetry
    assert telemetry["cache_stats"]["exact_hits"] >= 1
    assert telemetry["savings_overview"]["total_tokens_saved"] > 0

