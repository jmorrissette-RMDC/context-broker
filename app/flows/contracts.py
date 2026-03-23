"""
Standard input/output contracts for build type graphs (ARCH-18).

Defines the TypedDict contracts that all assembly and retrieval graphs
must accept as input and produce as output. This decouples callers
(arq_worker, tool_dispatch) from specific build type implementations.
"""

from typing import Optional

from typing_extensions import TypedDict


class AssemblyInput(TypedDict):
    """Standard input contract for assembly graphs."""

    context_window_id: str
    conversation_id: str
    config: dict


class AssemblyOutput(TypedDict, total=False):
    """Standard output contract for assembly graphs.

    Assembly graphs store summaries directly in the DB and update
    last_assembled_at. The output carries status information.
    """

    error: Optional[str]


class RetrievalInput(TypedDict):
    """Standard input contract for retrieval graphs."""

    context_window_id: str
    config: dict


class RetrievalOutput(TypedDict, total=False):
    """Standard output contract for retrieval graphs."""

    context_messages: list[dict]
    context_tiers: dict
    total_tokens_used: int
    warnings: list[str]
    error: Optional[str]
