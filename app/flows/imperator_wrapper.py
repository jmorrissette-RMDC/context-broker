"""
Imperator flow wrapper — kernel-side metrics and lifecycle management.

Looks up the TE's Imperator from the stategraph_registry and invokes
it with metrics recording. This is the kernel's interface to the TE.

Per REQ-001 §6.4: metrics produced inside the flow layer, not route handlers.
"""

import logging
import time
from typing import AsyncGenerator

from app.metrics_registry import CHAT_REQUESTS, CHAT_REQUEST_DURATION

_log = logging.getLogger("context_broker.flows.imperator_wrapper")

# Lazy singleton — rebuilt after install_stategraph()
_imperator_flow = None


def _get_flow():
    """Get the compiled Imperator flow from the TE registry."""
    global _imperator_flow
    if _imperator_flow is None:
        from app.stategraph_registry import get_imperator_builder

        builder = get_imperator_builder()
        if builder is None:
            raise RuntimeError(
                "No TE package registered. Install a TE package with "
                "install_stategraph or ensure one is installed at startup."
            )
        _imperator_flow = builder()
    return _imperator_flow


def invalidate():
    """Clear the cached flow. Called after install_stategraph()."""
    global _imperator_flow
    _imperator_flow = None
    _log.info("Imperator flow cache invalidated")


async def invoke_with_metrics(initial_state: dict, config: dict | None = None) -> dict:
    """Invoke the Imperator flow and record chat metrics.

    Per REQ-001 §6.4: metrics produced inside the flow layer, not route handlers.
    """
    start_time = time.monotonic()
    status = "error"
    try:
        thread_id = str(
            initial_state.get("context_window_id") or "imperator-default"
        )
        result = await _get_flow().ainvoke(
            initial_state,
            config={"configurable": {"thread_id": thread_id}},
        )
        status = "success"
        return result
    finally:
        duration = time.monotonic() - start_time
        CHAT_REQUESTS.labels(status=status).inc()
        CHAT_REQUEST_DURATION.observe(duration)


async def astream_events_with_metrics(
    initial_state: dict,
    config: dict | None = None,
) -> AsyncGenerator:
    """Stream Imperator events and record chat metrics.

    Per REQ-001 §6.4: metrics produced inside the flow layer, not route handlers.
    """
    start_time = time.monotonic()
    status = "error"
    try:
        thread_id = str(
            initial_state.get("context_window_id") or "chat-stream-default"
        )
        async for event in _get_flow().astream_events(
            initial_state,
            version="v2",
            config={"configurable": {"thread_id": thread_id}},
        ):
            yield event
        status = "success"
    finally:
        duration = time.monotonic() - start_time
        CHAT_REQUESTS.labels(status=status).inc()
        CHAT_REQUEST_DURATION.observe(duration)
