"""Unified Token Optimization and Cost Control Subsystem (Module 1)."""
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple
from src.orchestrator.cost_tracker import CostEstimator
from src.orchestrator.prompt_cache import PromptCache
from src.orchestrator.token_tracker import BudgetExceededError, TaskTokenBudget, TokenTracker
from src.providers.base import BaseModelProvider, ChatMessage, ProviderResponse


class TokenOptimizer:
    """Coordinates caching, token budgeting, prefix alignment, and cost estimation."""

    def __init__(self):
        self.tracker = TokenTracker()
        self.cache = PromptCache()
        self.cost_estimator = CostEstimator()

    def set_task_budget(self, task_id: str, max_tokens: int) -> TaskTokenBudget:
        return self.tracker.set_task_budget(task_id, max_tokens)

    async def execute_optimized_request(
        self,
        provider: BaseModelProvider,
        messages: List[ChatMessage],
        task_id: str = "default_task",
        model_name: Optional[str] = None,
        use_cache: bool = True,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> Tuple[ProviderResponse, Dict[str, Any]]:
        """Execute inference with exact/semantic caching, budgeting, and cost accounting.

        Returns (ProviderResponse, metrics_dict).
        """
        active_model = model_name or provider.get_capabilities().model_name

        # 1. Check Cache (Exact & Semantic)
        if use_cache:
            cache_hit = self.cache.get(messages, active_model)
            if cache_hit:
                cached_resp, hit_type = cache_hit
                saved_tokens = max(10, len(cached_resp.content) // 4)
                saved_usd = self.cost_estimator.record_cache_hit_savings(active_model, saved_tokens)

                metrics = {
                    "cache_hit": True,
                    "hit_type": hit_type,
                    "tokens_consumed": 0,
                    "tokens_saved": saved_tokens,
                    "request_cost_usd": 0.0,
                    "saved_usd": saved_usd,
                    "budget_remaining": self.tracker.get_task_summary(task_id).get("budget_remaining"),
                }
                return cached_resp, metrics

        # 2. Estimate Prompt Tokens & Verify Budget Pre-flight
        estimated_prompt_tokens = sum(max(1, len(m.content) // 4) for m in messages)
        budget = self.tracker.get_task_budget(task_id)
        if budget and (budget.tokens_consumed + estimated_prompt_tokens > budget.max_tokens):
            raise BudgetExceededError(
                f"Pre-flight check failed: Task '{task_id}' has {budget.remaining_tokens} tokens remaining, "
                f"but prompt requires ~{estimated_prompt_tokens} tokens. Execution halted to preserve budget."
            )

        # 3. Call Provider
        response = await provider.complete(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # 4. Extract or Estimate Actual Tokens
        usage = response.usage or {}
        p_tokens = usage.get("prompt_tokens") or estimated_prompt_tokens
        c_tokens = usage.get("completion_tokens") or max(1, len(response.content) // 4)
        cached_p_tokens = usage.get("cached_prompt_tokens", 0)

        # 5. Record Usage in Budget & Tracker
        req_id = f"req_{int(time.time() * 1000)}"
        self.tracker.record_usage(
            request_id=req_id,
            task_id=task_id,
            model_name=active_model,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            cached_prompt_tokens=cached_p_tokens,
        )

        # 6. Calculate Cost
        cost_info = self.cost_estimator.calculate_request_cost(
            model_name=active_model,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            cached_prompt_tokens=cached_p_tokens,
        )

        # 7. Store in Cache
        if use_cache:
            self.cache.store(messages, active_model, response)

        metrics = {
            "cache_hit": False,
            "tokens_consumed": p_tokens + c_tokens,
            "prompt_tokens": p_tokens,
            "completion_tokens": c_tokens,
            "cached_prompt_tokens": cached_p_tokens,
            "request_cost_usd": cost_info["total_cost_usd"],
            "request_cost_inr": cost_info["total_cost_inr"],
            "budget_remaining": self.tracker.get_task_summary(task_id).get("budget_remaining"),
        }
        return response, metrics

    def get_telemetry_summary(self) -> Dict[str, Any]:
        """Aggregate global token metrics, financial spend/savings, and cache telemetry."""
        global_usage = self.tracker.get_global_summary()
        cost_summary = self.cost_estimator.get_summary()
        cache_stats = self.cache.get_stats()

        total_saved_tokens = cache_stats.get("estimated_tokens_saved", 0) + global_usage.get("total_cached_prompt_tokens", 0)
        total_tokens_evaluated = global_usage.get("grand_total_tokens", 0) + total_saved_tokens
        savings_percentage = round((total_saved_tokens / total_tokens_evaluated * 100), 1) if total_tokens_evaluated > 0 else 0.0

        return {
            "token_accounting": global_usage,
            "financial_summary": cost_summary,
            "cache_stats": cache_stats,
            "savings_overview": {
                "total_tokens_saved": total_saved_tokens,
                "total_tokens_evaluated": total_tokens_evaluated,
                "reduction_percentage": savings_percentage,
            },
        }

