"""
Unit tests for the metrics collection StateGraph flow.
"""

import pytest

from app.flows.metrics_flow import MetricsState, collect_metrics_node, build_metrics_flow


@pytest.mark.asyncio
async def test_collect_metrics_node_produces_output():
    """collect_metrics_node returns non-empty Prometheus metrics output."""
    state: MetricsState = {
        "action": "collect",
        "metrics_output": "",
        "error": None,
    }

    result = await collect_metrics_node(state)

    assert result["error"] is None
    assert len(result["metrics_output"]) > 0
    # Prometheus exposition format starts with # HELP or # TYPE
    assert "#" in result["metrics_output"] or "context_broker" in result["metrics_output"]


@pytest.mark.asyncio
async def test_metrics_flow_compiles_and_runs():
    """build_metrics_flow produces a runnable StateGraph."""
    flow = build_metrics_flow()

    result = await flow.ainvoke(
        {
            "action": "collect",
            "metrics_output": "",
            "error": None,
        }
    )

    assert result["error"] is None
    assert isinstance(result["metrics_output"], str)


@pytest.mark.asyncio
async def test_collect_metrics_node_state_immutability():
    """collect_metrics_node does not modify the input state in-place."""
    import copy

    state: MetricsState = {
        "action": "collect",
        "metrics_output": "",
        "error": None,
    }
    original = copy.deepcopy(state)

    await collect_metrics_node(state)

    # Original state must be unchanged
    assert state["action"] == original["action"]
    assert state["metrics_output"] == original["metrics_output"]
