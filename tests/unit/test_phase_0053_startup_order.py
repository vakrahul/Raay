"""PHASE 0053: normative startup order is policy-first and dispatcher-last."""
from src.orchestrator.main import describe_startup_order


def test_startup_order_contract():
    order = describe_startup_order()
    assert order[0] == "config"
    assert order.index("security_policy") < order.index("provider_registry")
    assert order.index("routes") < order.index("task_dispatcher")
    assert len(order) == len(set(order)) == 8
