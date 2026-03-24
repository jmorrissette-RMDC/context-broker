"""
Context Broker TE — Package registration entry point.

Called by the bootstrap kernel's stategraph_registry.scan() when this
package is discovered via entry_points(group="context-broker.te").

Returns a TERegistration dict with the Imperator flow builder and
identity/purpose declarations.
"""


def register() -> dict:
    """Register the Context Broker TE's cognitive StateGraphs.

    Returns a dict with:
    - identity: What the Imperator is
    - purpose: What the Imperator is for
    - imperator_builder: callable that builds the compiled Imperator StateGraph
    - tools_required: MCP tools the Imperator needs from the AE
    """
    from context_broker_te.imperator_flow import build_imperator_flow

    return {
        "identity": "Context Broker Imperator",
        "purpose": "Context engineering and conversational memory management",
        "imperator_builder": build_imperator_flow,
        "tools_required": [
            "get_context",
            "store_message",
            "search_messages",
            "search_knowledge",
        ],
    }
