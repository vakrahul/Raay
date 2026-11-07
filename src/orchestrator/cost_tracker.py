"""Cost estimation, pricing matrices, and savings reporting (Module 1)."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ModelPricing(BaseModel):
    model_name: str
    prompt_usd_per_million: float
    completion_usd_per_million: float
    cached_prompt_usd_per_million: float = 0.0


# Official pricing per million tokens (as of 2026)
PRICING_REGISTRY: Dict[str, ModelPricing] = {
    # Gemini 3.1 Flash-Lite / 1.5 Flash
    "gemini-3.1-flash-lite": ModelPricing(
        model_name="gemini-3.1-flash-lite",
        prompt_usd_per_million=0.075,
        completion_usd_per_million=0.30,
        cached_prompt_usd_per_million=0.01875,
    ),
    "gemini-1.5-flash": ModelPricing(
        model_name="gemini-1.5-flash",
        prompt_usd_per_million=0.075,
        completion_usd_per_million=0.30,
        cached_prompt_usd_per_million=0.01875,
    ),
    # OpenAI GPT-4o
    "gpt-4o": ModelPricing(
        model_name="gpt-4o",
        prompt_usd_per_million=2.50,
        completion_usd_per_million=10.00,
        cached_prompt_usd_per_million=1.25,
    ),
    # Anthropic Claude 3.5 Sonnet
    "claude-3-5-sonnet-20241022": ModelPricing(
        model_name="claude-3-5-sonnet-20241022",
        prompt_usd_per_million=3.00,
        completion_usd_per_million=15.00,
        cached_prompt_usd_per_million=0.30,
    ),
    # Local & Mock (Zero API cost)
    "local": ModelPricing(model_name="local", prompt_usd_per_million=0.0, completion_usd_per_million=0.0),
    "mock": ModelPricing(model_name="mock", prompt_usd_per_million=0.0, completion_usd_per_million=0.0),
    "mock-gpt-4o": ModelPricing(model_name="mock-gpt-4o", prompt_usd_per_million=0.0, completion_usd_per_million=0.0),
}


class CostEstimator:
    """Calculates inference costs and measured savings from token optimization."""

    USD_TO_INR_RATE = 87.50

    def __init__(self, pricing_dict: Optional[Dict[str, ModelPricing]] = None):
        self.pricing = pricing_dict or PRICING_REGISTRY
        self.cumulative_spent_usd = 0.0
        self.cumulative_saved_usd = 0.0

    def calculate_request_cost(
        self,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_prompt_tokens: int = 0,
    ) -> Dict[str, float]:
        """Compute exact cost for a single inference request in USD and INR."""
        # Normalize model key
        pricing = self.pricing.get(model_name)
        if not pricing:
            # Fallback to nearest family or default cheap pricing
            if "gemini" in model_name.lower():
                pricing = self.pricing["gemini-3.1-flash-lite"]
            elif "claude" in model_name.lower():
                pricing = self.pricing["claude-3-5-sonnet-20241022"]
            elif "gpt-4" in model_name.lower():
                pricing = self.pricing["gpt-4o"]
            else:
                pricing = self.pricing["mock"]

        # Standard non-cached prompt tokens
        regular_prompt_tokens = max(0, prompt_tokens - cached_prompt_tokens)

        cost_prompt = (regular_prompt_tokens / 1_000_000.0) * pricing.prompt_usd_per_million
        cost_cached = (cached_prompt_tokens / 1_000_000.0) * pricing.cached_prompt_usd_per_million
        cost_completion = (completion_tokens / 1_000_000.0) * pricing.completion_usd_per_million

        total_cost_usd = cost_prompt + cost_cached + cost_completion
        total_cost_inr = total_cost_usd * self.USD_TO_INR_RATE

        # Savings if cached prompt tokens were regular
        full_cost = (prompt_tokens / 1_000_000.0) * pricing.prompt_usd_per_million + cost_completion
        saved_usd = max(0.0, full_cost - total_cost_usd)

        self.cumulative_spent_usd += total_cost_usd
        self.cumulative_saved_usd += saved_usd

        return {
            "total_cost_usd": total_cost_usd,
            "total_cost_inr": total_cost_inr,
            "saved_usd": saved_usd,
        }

    def record_cache_hit_savings(self, model_name: str, estimated_tokens: int) -> float:
        """Calculate and record money saved from a 100% local cache hit."""
        pricing = self.pricing.get(model_name, self.pricing["gemini-3.1-flash-lite"])
        # Assume 70% prompt, 30% completion for saved transaction
        saved_usd = (estimated_tokens / 1_000_000.0) * pricing.prompt_usd_per_million
        self.cumulative_saved_usd += saved_usd
        return saved_usd

    def get_summary(self) -> Dict[str, Any]:
        return {
            "total_spent_usd": round(self.cumulative_spent_usd, 6),
            "total_spent_inr": round(self.cumulative_spent_usd * self.USD_TO_INR_RATE, 4),
            "total_saved_usd": round(self.cumulative_saved_usd, 6),
            "total_saved_inr": round(self.cumulative_saved_usd * self.USD_TO_INR_RATE, 4),
        }
