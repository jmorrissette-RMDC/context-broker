import pytest

from app import budget
from app.token_budget import resolve_token_budget


def test_snap_budget():
    assert budget.snap_budget(1) == budget.BUDGET_BUCKETS[0]
    assert budget.snap_budget(4096) == 4096
    assert budget.snap_budget(5000) == 8192
    assert budget.snap_budget(9999999) == budget.BUDGET_BUCKETS[-1]


@pytest.mark.asyncio
async def test_resolve_token_budget_caller_override():
    config = {"llm": {}}
    build_type = {"max_context_tokens": "auto", "fallback_tokens": 12345}
    resolved = await resolve_token_budget(config, build_type, caller_override=777)
    assert resolved == 777


@pytest.mark.asyncio
async def test_resolve_token_budget_fallback_without_provider():
    config = {"llm": {}}
    build_type = {"max_context_tokens": "auto", "fallback_tokens": 4321}
    resolved = await resolve_token_budget(config, build_type)
    assert resolved == 4321
