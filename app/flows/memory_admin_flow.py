"""
Memory Admin Flows — LangGraph StateGraph flows for mem_add, mem_list, mem_delete.

M-18: These flows wrap Mem0 calls in StateGraphs to comply with the
LangGraph mandate. Previously these operations were called directly
via run_in_executor in tool_dispatch.
"""

import asyncio
import logging
from typing import Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

_log = logging.getLogger("context_broker.flows.memory_admin")


# ============================================================
# mem_add flow
# ============================================================


class MemAddState(TypedDict):
    """State for the mem_add flow."""

    content: str
    user_id: str
    config: dict

    result: Optional[dict]
    degraded: bool  # R6-M7: Needed so dispatch can distinguish degraded vs hard error
    error: Optional[str]


async def add_memory(state: MemAddState) -> dict:
    """Add a memory to the Mem0 knowledge graph."""
    config = state["config"]

    try:
        from app.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            return {"error": "Mem0 client not available", "degraded": True}

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: mem0.add(state["content"], user_id=state["user_id"]),
        )

        return {"result": result, "degraded": False}

    except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:
        # G5-18: Broad exception handling for Mem0/Neo4j failures.
        _log.warning("mem_add failed: %s", exc)
        return {"error": str(exc), "degraded": True}


def build_mem_add_flow() -> StateGraph:
    """Build and compile the mem_add StateGraph."""
    workflow = StateGraph(MemAddState)
    workflow.add_node("add_memory", add_memory)
    workflow.set_entry_point("add_memory")
    workflow.add_edge("add_memory", END)
    return workflow.compile()


# ============================================================
# mem_list flow
# ============================================================


class MemListState(TypedDict):
    """State for the mem_list flow."""

    user_id: str
    limit: int
    config: dict

    memories: list[dict]
    degraded: bool  # R6-M7: Needed so dispatch can distinguish degraded vs hard error
    error: Optional[str]


async def list_memories(state: MemListState) -> dict:
    """List all memories for a user from the Mem0 knowledge graph."""
    config = state["config"]

    try:
        from app.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            return {"error": "Mem0 client not available", "degraded": True}

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: mem0.get_all(user_id=state["user_id"], limit=state["limit"]),
        )

        if isinstance(result, list):
            memories = result
        elif isinstance(result, dict):
            memories = result.get("results", [])
        else:
            memories = []

        return {"memories": memories, "degraded": False}

    except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:
        # G5-18: Broad exception handling for Mem0/Neo4j failures.
        _log.warning("mem_list failed: %s", exc)
        return {"memories": [], "error": str(exc), "degraded": True}


def build_mem_list_flow() -> StateGraph:
    """Build and compile the mem_list StateGraph."""
    workflow = StateGraph(MemListState)
    workflow.add_node("list_memories", list_memories)
    workflow.set_entry_point("list_memories")
    workflow.add_edge("list_memories", END)
    return workflow.compile()


# ============================================================
# mem_delete flow
# ============================================================


class MemDeleteState(TypedDict):
    """State for the mem_delete flow."""

    memory_id: str
    config: dict

    deleted: bool
    degraded: bool  # R6-M7: Needed so dispatch can distinguish degraded vs hard error
    error: Optional[str]


async def delete_memory(state: MemDeleteState) -> dict:
    """Delete a memory from the Mem0 knowledge graph."""
    config = state["config"]

    try:
        from app.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            return {"error": "Mem0 client not available", "degraded": True}

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: mem0.delete(state["memory_id"]),
        )

        return {"deleted": True, "degraded": False}

    except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:
        # G5-18: Broad exception handling for Mem0/Neo4j failures.
        _log.warning("mem_delete failed: %s", exc)
        return {"deleted": False, "error": str(exc), "degraded": True}


def build_mem_delete_flow() -> StateGraph:
    """Build and compile the mem_delete StateGraph."""
    workflow = StateGraph(MemDeleteState)
    workflow.add_node("delete_memory", delete_memory)
    workflow.set_entry_point("delete_memory")
    workflow.add_edge("delete_memory", END)
    return workflow.compile()
