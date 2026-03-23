"""
Context Retrieval — backward-compatibility shim (ARCH-18).

The retrieval logic has moved to:
  - app.flows.build_types.standard_tiered (standard-tiered)
  - app.flows.build_types.knowledge_enriched (knowledge-enriched)

This module re-exports the original symbols so existing imports and
tests continue to work.

build_retrieval_flow() returns the knowledge-enriched retrieval graph
for backward compatibility (it was the original monolithic graph that
handled all build types including semantic/KG).
"""

# Re-export from the knowledge-enriched build type (superset of all features)
from app.flows.build_types.knowledge_enriched import (  # noqa: F401
    KnowledgeEnrichedRetrievalState as RetrievalState,
    ke_load_window as load_window,
    ke_wait_for_assembly as wait_for_assembly,
    ke_load_summaries as load_summaries,
    ke_load_recent_messages as load_recent_messages,
    ke_inject_semantic_retrieval as inject_semantic_retrieval,
    ke_inject_knowledge_graph as inject_knowledge_graph,
    ke_assemble_context as assemble_context_text,
    build_knowledge_enriched_retrieval as build_retrieval_flow,
    _estimate_tokens,
)
