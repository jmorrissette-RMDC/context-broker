"""
Context Broker AE — Package registration entry point.

Called by the bootstrap kernel's stategraph_registry.scan() when this
package is discovered via entry_points(group="context-broker.ae").

Returns an AERegistration dict with build type registrations and
flow builders that the kernel processes to populate its registries.
"""


def register() -> dict:
    """Register the Context Broker AE's infrastructure StateGraphs.

    Returns a dict with:
    - build_types: dict of (assembly_builder, retrieval_builder) pairs
    - flows: dict of flow_name -> builder callable
    """
    # Build types
    from context_broker_ae.build_types.passthrough import (
        build_passthrough_assembly,
        build_passthrough_retrieval,
    )
    from context_broker_ae.build_types.standard_tiered import (
        build_standard_tiered_assembly,
        build_standard_tiered_retrieval,
    )
    from context_broker_ae.build_types.knowledge_enriched import (
        build_knowledge_enriched_retrieval,
    )

    # Flow builders
    from context_broker_ae.message_pipeline import build_message_pipeline
    from context_broker_ae.embed_pipeline import build_embed_pipeline
    from context_broker_ae.memory_extraction import build_memory_extraction
    from context_broker_ae.search_flow import (
        build_conversation_search_flow,
        build_message_search_flow,
    )
    from context_broker_ae.conversation_ops_flow import (
        build_create_conversation_flow,
        build_create_context_window_flow,
        build_get_context_flow,
        build_get_history_flow,
        build_search_context_windows_flow,
    )
    from context_broker_ae.memory_search_flow import (
        build_memory_search_flow,
        build_memory_context_flow,
    )
    from context_broker_ae.memory_admin_flow import (
        build_mem_add_flow,
        build_mem_delete_flow,
        build_mem_list_flow,
    )
    from context_broker_ae.health_flow import build_health_check_flow
    from context_broker_ae.metrics_flow import build_metrics_flow

    return {
        "build_types": {
            "passthrough": (
                build_passthrough_assembly,
                build_passthrough_retrieval,
            ),
            "standard-tiered": (
                build_standard_tiered_assembly,
                build_standard_tiered_retrieval,
            ),
            "knowledge-enriched": (
                # Reuses standard-tiered assembly; only retrieval differs
                build_standard_tiered_assembly,
                build_knowledge_enriched_retrieval,
            ),
        },
        "flows": {
            "message_pipeline": build_message_pipeline,
            "embed_pipeline": build_embed_pipeline,
            "memory_extraction": build_memory_extraction,
            "conversation_search": build_conversation_search_flow,
            "message_search": build_message_search_flow,
            "create_conversation": build_create_conversation_flow,
            "create_context_window": build_create_context_window_flow,
            "get_context": build_get_context_flow,
            "get_history": build_get_history_flow,
            "search_context_windows": build_search_context_windows_flow,
            "memory_search": build_memory_search_flow,
            "memory_context": build_memory_context_flow,
            "mem_add": build_mem_add_flow,
            "mem_delete": build_mem_delete_flow,
            "mem_list": build_mem_list_flow,
            "health_check": build_health_check_flow,
            "metrics": build_metrics_flow,
        },
    }
