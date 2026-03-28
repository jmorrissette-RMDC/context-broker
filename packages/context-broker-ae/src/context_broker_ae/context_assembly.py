"""
Context Assembly — backward-compatibility shim (ARCH-18).

The assembly logic has moved to app.flows.build_types.standard_tiered.
This module re-exports the original symbols so existing imports and
tests continue to work.

build_context_assembly() returns the standard-tiered assembly graph
for backward compatibility.
"""

# Re-export from the new location
from context_broker_ae.build_types.standard_tiered import (  # noqa: F401
    StandardTieredAssemblyState as ContextAssemblyState,
    acquire_assembly_lock,
    calculate_tier_boundaries,
    consolidate_archival_summary,
    finalize_assembly,
    load_messages,
    load_window_config,
    release_assembly_lock,
    summarize_message_chunks,
    build_standard_tiered_assembly as build_context_assembly,
)
