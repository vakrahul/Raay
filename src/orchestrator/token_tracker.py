"""Token tracking, per-task budgets, and per-model consumption accounting (Module 1)."""
from dataclasses import dataclass
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BudgetExceededError(Exception):
    """Raised when an active task exceeds its allocated token budget."""
    pass


class TokenUsageRecord(BaseModel):
    request_id: str
    task_id: str
    model_name: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cached_prompt_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(ge=0)
    timestamp: float = Field(default_factory=time.time)


class TaskTokenBudget(BaseModel):
    task_id: str
    max_tokens: int = Field(default=10000, ge=100)
    tokens_consumed: int = Field(default=0, ge=0)

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self.tokens_consumed)

    @property
    def is_exhausted(self) -> bool:
        return self.tokens_consumed >= self.max_tokens

    def consume(self, count: int) -> None:
        if self.tokens_consumed + count > self.max_tokens:
            excess = (self.tokens_consumed + count) - self.max_tokens
            raise BudgetExceededError(
                f"Task '{self.task_id}' exceeded token budget: "
                f"{self.tokens_consumed + count} tokens used, budget was {self.max_tokens} (excess: {excess})"
            )
        self.tokens_consumed += count


class TokenTracker:
    """Central accounting authority for token consumption across tasks and models."""

    def __init__(self):
        self._records: List[TokenUsageRecord] = []
        self._task_budgets: Dict[str, TaskTokenBudget] = {}

    def set_task_budget(self, task_id: str, max_tokens: int) -> TaskTokenBudget:
        budget = TaskTokenBudget(task_id=task_id, max_tokens=max_tokens)
        self._task_budgets[task_id] = budget
        return budget

    def get_task_budget(self, task_id: str) -> Optional[TaskTokenBudget]:
        return self._task_budgets.get(task_id)

    def record_usage(
        self,
        request_id: str,
        task_id: str,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_prompt_tokens: int = 0,
    ) -> TokenUsageRecord:
        total = prompt_tokens + completion_tokens
        
        # Enforce budget if registered for this task
        if task_id in self._task_budgets:
            self._task_budgets[task_id].consume(total)

        record = TokenUsageRecord(
            request_id=request_id,
            task_id=task_id,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            total_tokens=total,
        )
        self._records.append(record)
        return record

    def get_task_summary(self, task_id: str) -> Dict[str, Any]:
        task_records = [r for r in self._records if r.task_id == task_id]
        total_p = sum(r.prompt_tokens for r in task_records)
        total_c = sum(r.completion_tokens for r in task_records)
        total_cached = sum(r.cached_prompt_tokens for r in task_records)
        total = sum(r.total_tokens for r in task_records)
        
        budget = self._task_budgets.get(task_id)
        return {
            "task_id": task_id,
            "requests_count": len(task_records),
            "prompt_tokens": total_p,
            "completion_tokens": total_c,
            "cached_prompt_tokens": total_cached,
            "total_tokens": total,
            "budget_limit": budget.max_tokens if budget else None,
            "budget_remaining": budget.remaining_tokens if budget else None,
        }

    def get_model_summary(self, model_name: str) -> Dict[str, Any]:
        records = [r for r in self._records if r.model_name == model_name]
        return {
            "model_name": model_name,
            "total_requests": len(records),
            "prompt_tokens": sum(r.prompt_tokens for r in records),
            "completion_tokens": sum(r.completion_tokens for r in records),
            "cached_prompt_tokens": sum(r.cached_prompt_tokens for r in records),
            "total_tokens": sum(r.total_tokens for r in records),
        }

    def get_global_summary(self) -> Dict[str, Any]:
        return {
            "total_requests": len(self._records),
            "total_prompt_tokens": sum(r.prompt_tokens for r in self._records),
            "total_completion_tokens": sum(r.completion_tokens for r in self._records),
            "total_cached_prompt_tokens": sum(r.cached_prompt_tokens for r in self._records),
            "grand_total_tokens": sum(r.total_tokens for r in self._records),
        }
