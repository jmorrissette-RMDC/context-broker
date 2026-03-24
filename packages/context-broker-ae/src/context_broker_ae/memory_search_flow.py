"""
Memory Search Flows — LangGraph StateGraph flows for mem_search and mem_get_context.

Queries the Mem0 knowledge graph for extracted facts and relationships.
"""

import asyncio
import logging
from typing import Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from context_broker_ae.memory_scoring import filter_and_rank_memories

_log = logging.getLogger("context_broker.flows.memory_search")


class MemorySearchState(TypedDict):
    """State for memory search flow."""

    query: str
    user_id: str
    limit: int
    config: dict

    memories: list[dict]
    relations: list[dict]
    degraded: bool
    error: Optional[str]


async def search_memory_graph(state: MemorySearchState) -> dict:
    """Search the Mem0 knowledge graph for relevant memories.

    Gracefully degrades if Neo4j or Mem0 is unavailable.
    """
    config = state["config"]

    try:
        from context_broker_ae.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            return {
                "memories": [],
                "relations": [],
                "degraded": True,
                "error": "Mem0 client not available",
            }

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            lambda: mem0.search(
                state["query"],
                user_id=state["user_id"],
                limit=state["limit"],
            ),
        )

        if isinstance(results, dict):
            memories = results.get("results", [])
            relations = results.get("relations", [])
        else:
            memories = results or []
            relations = []

        # M-22: Apply half-life decay scoring and filter stale memories
        memories = filter_and_rank_memories(memories, config)

        return {
            "memories": memories,
            "relations": relations,
            "degraded": False,
        }

    except (
        ConnectionError,
        RuntimeError,
        ValueError,
        ImportError,
        OSError,
        Exception,
    ) as exc:  # EX-CB-001: broad catch for Mem0
        _log.warning("Memory search failed (degraded mode): %s", exc)
        return {
            "memories": [],
            "relations": [],
            "degraded": True,
            "error": str(exc),
        }


def build_memory_search_flow() -> StateGraph:
    """Build and compile the memory search StateGraph."""
    workflow = StateGraph(MemorySearchState)
    workflow.add_node("search_memory_graph", search_memory_graph)
    workflow.set_entry_point("search_memory_graph")
    workflow.add_edge("search_memory_graph", END)
    return workflow.compile()


# ============================================================
# Memory Context Flow
# ============================================================


class MemoryContextState(TypedDict):
    """State for memory context retrieval flow."""

    query: str
    user_id: str
    limit: int
    config: dict

    memories: list[dict]
    context_text: str
    degraded: bool  # R6-M7: Needed so dispatch can distinguish degraded vs hard error
    error: Optional[str]


async def retrieve_memory_context(state: MemoryContextState) -> dict:
    """Retrieve memories and format them for prompt injection."""
    config = state["config"]

    try:
        from context_broker_ae.memory.mem0_client import get_mem0_client

        mem0 = await get_mem0_client(config)
        if mem0 is None:
            return {
                "memories": [],
                "context_text": "",
                "degraded": True,
                "error": "Mem0 client not available",
            }

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            lambda: mem0.search(
                state["query"],
                user_id=state["user_id"],
                limit=state["limit"],
            ),
        )

        if isinstance(results, dict):
            memories = results.get("results", [])
        else:
            memories = results or []

        # M-22: Apply half-life decay scoring and filter stale memories
        memories = filter_and_rank_memories(memories, config)

        if memories:
            lines = ["Relevant knowledge about this context:"]
            for mem in memories:
                fact = mem.get("memory") or mem.get("content") or str(mem)
                lines.append(f"- {fact}")
            context_text = "\n".join(lines)
        else:
            context_text = ""

        return {"memories": memories, "context_text": context_text, "degraded": False}

    except (
        ConnectionError,
        RuntimeError,
        ValueError,
        ImportError,
        OSError,
        Exception,
    ) as exc:  # EX-CB-001: broad catch for Mem0
        _log.warning("Memory context retrieval failed (degraded mode): %s", exc)
        return {"memories": [], "context_text": "", "degraded": True, "error": str(exc)}


def build_memory_context_flow() -> StateGraph:
    """Build and compile the memory context StateGraph."""
    workflow = StateGraph(MemoryContextState)
    workflow.add_node("retrieve_memory_context", retrieve_memory_context)
    workflow.set_entry_point("retrieve_memory_context")
    workflow.add_edge("retrieve_memory_context", END)
    return workflow.compile()
