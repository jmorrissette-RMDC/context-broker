"""
Health Check — LangGraph StateGraph flow.

Checks connectivity to backing services (PostgreSQL, Neo4j)
and returns aggregated health status. Invoked by the /health route.
"""

import logging
from typing import Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.database import check_postgres_health, check_neo4j_health

_log = logging.getLogger("context_broker.flows.health")


class HealthCheckState(TypedDict):
    """State for the health check flow."""

    config: dict

    postgres_ok: bool
    neo4j_ok: bool
    all_healthy: bool
    status_detail: Optional[dict]
    http_status: int


async def check_dependencies(state: HealthCheckState) -> dict:
    """Check connectivity to all backing services."""
    config = state["config"]

    postgres_ok = await check_postgres_health()
    neo4j_ok = await check_neo4j_health(config)

    all_healthy = postgres_ok

    if not all_healthy:
        status_label = "unhealthy"
        http_status = 503
    elif not neo4j_ok:
        status_label = "degraded"
        http_status = 200
    else:
        status_label = "healthy"
        http_status = 200

    status_detail = {
        "status": status_label,
        "database": "ok" if postgres_ok else "error",
        "neo4j": "ok" if neo4j_ok else "degraded",
    }

    if not all_healthy:
        _log.warning("Health check: unhealthy — %s", status_detail)
    elif not neo4j_ok:
        _log.warning("Health check: degraded — neo4j unavailable")

    return {
        "postgres_ok": postgres_ok,
        "neo4j_ok": neo4j_ok,
        "all_healthy": all_healthy,
        "status_detail": status_detail,
        "http_status": http_status,
    }


def build_health_check_flow() -> StateGraph:
    """Build and compile the health check StateGraph."""
    workflow = StateGraph(HealthCheckState)
    workflow.add_node("check_dependencies", check_dependencies)
    workflow.set_entry_point("check_dependencies")
    workflow.add_edge("check_dependencies", END)
    return workflow.compile()
