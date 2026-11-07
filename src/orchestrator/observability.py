"""System observability, telemetry reporting, and metrics dashboard (Module 10)."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.orchestrator.cost_tracker import CostEstimator
from src.orchestrator.intelligent_router import IntelligentRouter
from src.orchestrator.token_tracker import TokenTracker
from src.security.audit_logger import redact_secrets


class SystemTelemetryDashboard:
    """Consolidates cross-subsystem telemetry into an actionable observability dashboard."""

    def __init__(
        self,
        token_tracker: TokenTracker,
        cost_estimator: CostEstimator,
        router: IntelligentRouter,
    ):
        self.tracker = token_tracker
        self.cost_estimator = cost_estimator
        self.router = router
        self.tool_call_counts: Dict[str, int] = {}
        self.task_latencies_ms: List[float] = []

    def record_tool_call(self, tool_name: str) -> None:
        self.tool_call_counts[tool_name] = self.tool_call_counts.get(tool_name, 0) + 1

    def record_task_duration(self, duration_ms: float) -> None:
        self.task_latencies_ms.append(duration_ms)

    def generate_json_report(self) -> Dict[str, Any]:
        global_usage = self.tracker.get_global_summary()
        cost_summary = self.cost_estimator.get_summary()

        router_stats = {
            name: m.model_dump() for name, m in self.router.metrics.items()
        }

        avg_latency = (
            sum(self.task_latencies_ms) / len(self.task_latencies_ms)
            if self.task_latencies_ms
            else 0.0
        )

        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "token_accounting": global_usage,
            "financial_summary": cost_summary,
            "router_metrics": router_stats,
            "tool_invocations": self.tool_call_counts,
            "performance": {
                "total_tasks_monitored": len(self.task_latencies_ms),
                "avg_task_latency_ms": round(avg_latency, 2),
            },
        }
        return redact_secrets(data)

    def generate_markdown_dashboard(self) -> str:
        report = self.generate_json_report()
        tokens = report["token_accounting"]
        fin = report["financial_summary"]
        tools = report["tool_invocations"]
        perf = report["performance"]

        lines = [
            "# WinAI-OE Telemetry & Observability Dashboard",
            f"**Generated:** {report['timestamp']}\n",
            "## 1. Token Accounting",
            f"* **Total Requests:** {tokens['total_requests']}",
            f"* **Prompt Tokens:** {tokens['total_prompt_tokens']:,}",
            f"* **Completion Tokens:** {tokens['total_completion_tokens']:,}",
            f"* **Cached Tokens (Discounts):** {tokens['total_cached_prompt_tokens']:,}",
            f"* **Grand Total Consumed:** {tokens['grand_total_tokens']:,}\n",
            "## 2. Financial Metrics (USD / INR)",
            f"* **Total Spent:** ${fin['total_spent_usd']:.4f} (₹{fin['total_spent_inr']:.2f})",
            f"* **Total Saved via Caching/Optimization:** ${fin['total_saved_usd']:.4f} (₹{fin['total_saved_inr']:.2f})\n",
            "## 3. Tool Invocations",
        ]

        if tools:
            for tool, count in tools.items():
                lines.append(f"* `{tool}`: {count} calls")
        else:
            lines.append("*No tools recorded yet.*")

        lines.extend([
            "\n## 4. Performance & Reliability",
            f"* **Tasks Monitored:** {perf['total_tasks_monitored']}",
            f"* **Average Task Latency:** {perf['avg_task_latency_ms']:.2f} ms",
        ])

        return "\n".join(lines)
