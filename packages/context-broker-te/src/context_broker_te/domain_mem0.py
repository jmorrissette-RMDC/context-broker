"""Domain-specific Mem0 client for the Imperator's knowledge graph.

Separate from the AE's conversation Mem0 instance. Uses the same
Neo4j container but a different pgvector collection name
("domain_memories" vs "mem0_memories") to keep domain knowledge
isolated from conversation knowledge.

TE-owned: this client is used by the Imperator's operational tools.
"""

import asyncio
import logging
import os
from typing import Optional

_log = logging.getLogger("context_broker.te.domain_mem0")

_domain_mem0_instance = None
_domain_mem0_lock = asyncio.Lock()


def _build_domain_mem0(config: dict) -> object:
    """Build a Mem0 Memory instance for domain knowledge."""
    # Apply patches (same as AE client — dedup protection)
    try:
        from context_broker_ae.memory.mem0_client import _apply_mem0_patches

        _apply_mem0_patches()
    except ImportError:
        pass

    from mem0 import Memory
    from mem0.configs.base import (
        EmbedderConfig,
        GraphStoreConfig,
        LlmConfig,
        MemoryConfig,
        VectorStoreConfig,
    )

    from app.config import get_api_key

    # Use the same LLM and embeddings config as the AE
    llm_config = config.get("extraction", {}) or config.get("llm", {})
    embeddings_config = config.get("embeddings", {})

    llm_api_key = get_api_key(llm_config)
    embeddings_api_key = get_api_key(embeddings_config)

    postgres_password = os.environ.get("POSTGRES_PASSWORD", "")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "")

    # embedding_dims from config
    embedding_dims = embeddings_config.get("embedding_dims")
    if embedding_dims is None:
        embedding_dims = config.get("embedding_dims")
    if embedding_dims is None:
        raise ValueError("embedding_dims is required for domain Mem0 client")

    neo4j_host = os.environ.get("NEO4J_HOST", "context-broker-neo4j")
    neo4j_port = os.environ.get("NEO4J_PORT", "7687")

    mem_config = MemoryConfig(
        version="v1.1",
        llm=LlmConfig(
            provider="openai",
            config={
                "api_key": llm_api_key or "",
                "model": llm_config.get("model", "gpt-4o-mini"),
                "openai_base_url": llm_config.get(
                    "base_url", "https://api.openai.com/v1"
                ),
            },
        ),
        embedder=EmbedderConfig(
            provider="openai",
            config={
                "api_key": embeddings_api_key or "",
                "model": embeddings_config.get("model", "text-embedding-3-small"),
                "openai_base_url": embeddings_config.get(
                    "base_url", "https://api.openai.com/v1"
                ),
            },
        ),
        vector_store=VectorStoreConfig(
            provider="pgvector",
            config={
                "host": os.environ.get("POSTGRES_HOST", "context-broker-postgres"),
                "port": int(os.environ.get("POSTGRES_PORT", "5432")),
                "dbname": os.environ.get("POSTGRES_DB", "context_broker"),
                "user": os.environ.get("POSTGRES_USER", "context_broker"),
                "password": postgres_password,
                "collection_name": "domain_memories",
                "embedding_model_dims": int(embedding_dims),
                "diskann": False,
            },
        ),
        graph_store=GraphStoreConfig(
            provider="neo4j",
            config={
                "url": f"bolt://{neo4j_host}:{neo4j_port}",
                "username": "neo4j",
                "password": neo4j_password or "neo4j",
            },
        ),
    )

    return Memory(config=mem_config)


async def get_domain_mem0(config: dict) -> Optional[object]:
    """Get or create the domain Mem0 singleton."""
    global _domain_mem0_instance

    async with _domain_mem0_lock:
        if _domain_mem0_instance is None:
            try:
                loop = asyncio.get_running_loop()
                _domain_mem0_instance = await loop.run_in_executor(
                    None, _build_domain_mem0, config
                )
                _log.info("Domain Mem0 client initialized")
            except (ImportError, ValueError, OSError, RuntimeError) as exc:
                _log.error("Failed to initialize domain Mem0: %s", exc)
                return None
        return _domain_mem0_instance


def reset_domain_mem0():
    """Reset the domain Mem0 singleton."""
    global _domain_mem0_instance
    _domain_mem0_instance = None
