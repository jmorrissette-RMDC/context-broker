"""
Health check endpoint.

Tests all backing service connections and returns aggregated status.
The LangGraph container performs the actual dependency checks —
nginx proxies the response without performing checks itself.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import async_load_config
from app.flows.health_flow import build_health_check_flow

_log = logging.getLogger("context_broker.routes.health")

router = APIRouter()

# R6-m1: Lazy-initialized flow singleton — compiled on first use instead of
# at import time, so module import doesn't trigger graph compilation.
_health_flow = None


def _get_health_flow():
    global _health_flow
    if _health_flow is None:
        _health_flow = build_health_check_flow()
    return _health_flow


@router.get("/health")
async def health_check(request: Request) -> JSONResponse:
    """Check connectivity to all backing services.

    Returns 200 if all critical services are healthy.
    Returns 503 if any critical service is unhealthy.
    """
    config = await async_load_config()

    result = await _get_health_flow().ainvoke(
        {
            "config": config,
            "postgres_ok": False,
            "redis_ok": False,
            "neo4j_ok": False,
            "all_healthy": False,
            "status_detail": None,
            "http_status": 503,
        }
    )

    return JSONResponse(
        status_code=result["http_status"],
        content=result["status_detail"],
    )
