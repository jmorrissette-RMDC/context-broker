"""
Build types package (ARCH-18).

Importing this package triggers registration of all build types
in the build_type_registry. Each build type module calls
register_build_type() at import time.
"""

# R6-m3: Import all build types to trigger registration side effects.
# This is the intended pattern: each module calls register_build_type()
# at module scope, registering its (assembly_builder, retrieval_builder)
# pair in the global registry. The arq_worker imports this package once
# at startup to ensure all build types are available.
import app.flows.build_types.passthrough  # noqa: F401
import app.flows.build_types.standard_tiered  # noqa: F401
import app.flows.build_types.knowledge_enriched  # noqa: F401
