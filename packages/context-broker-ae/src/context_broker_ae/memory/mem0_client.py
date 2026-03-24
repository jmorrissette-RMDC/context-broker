"""
Mem0 client initialization for the Context Broker.

Provides a lazy singleton Mem0 Memory instance configured with
pgvector (for vector storage) and Neo4j (for knowledge graph).

All credentials come from environment variables loaded via env_file.
"""

import asyncio
import hashlib
import json
import logging
import os
from typing import Optional

_log = logging.getLogger("context_broker.memory.mem0_client")

_mem0_instance = None
_mem0_config_hash: Optional[str] = None
_mem0_lock = asyncio.Lock()


def _compute_config_hash(config: dict) -> str:
    """Compute a hash of config sections relevant to Mem0 initialization.

    Used to detect config changes that require recreating the Mem0 instance.
    m19: Includes graph_store and vector_store config so that changes to
    database connections (e.g., Neo4j host, Postgres credentials) also
    trigger Mem0 client recreation.
    """
    relevant = {
        "llm": config.get("llm", {}),
        "embeddings": config.get("embeddings", {}),
        "graph_store": config.get("graph_store", {}),
        "vector_store": config.get("vector_store", {}),
    }
    config_str = json.dumps(relevant, sort_keys=True, default=str)
    return hashlib.sha256(config_str.encode()).hexdigest()


async def get_mem0_client(config: dict) -> Optional[object]:
    """Return the Mem0 Memory instance (lazy singleton, async-safe).

    M-12: Uses asyncio.Lock instead of threading.Lock to avoid blocking
    the event loop. All callers must await this function.

    Returns None if Mem0 initialization fails (graceful degradation).
    Each call reattempts initialization if no instance exists yet,
    per REQ-001 §7.2 (independent startup).

    Automatically recreates the instance if the LLM or embeddings
    config has changed since the last initialization.
    """
    global _mem0_instance, _mem0_config_hash

    current_hash = _compute_config_hash(config)

    if _mem0_instance is not None and _mem0_config_hash == current_hash:
        return _mem0_instance

    async with _mem0_lock:
        # Double-check after acquiring lock
        if _mem0_instance is not None and _mem0_config_hash == current_hash:
            return _mem0_instance

        if _mem0_instance is not None and _mem0_config_hash != current_hash:
            _log.info("Mem0 config changed — recreating client")

        try:
            # CB-R3-08: _build_mem0_instance is synchronous (imports, network
            # probes to Neo4j/Postgres) and would block the event loop if called
            # directly under the async lock. Run it in a thread pool executor.
            loop = asyncio.get_running_loop()
            _mem0_instance = await loop.run_in_executor(
                None, _build_mem0_instance, config
            )
            _mem0_config_hash = current_hash
            _log.info("Mem0 client initialized successfully")
            return _mem0_instance
        except (ImportError, ConnectionError, ValueError) as exc:
            _log.warning(
                "Mem0 initialization failed (knowledge graph unavailable): %s", exc
            )
            _mem0_instance = None
            _mem0_config_hash = None
            return None


def _build_mem0_instance(config: dict) -> object:
    """Build and configure the Mem0 Memory instance.

    Uses pgvector for vector storage and Neo4j for knowledge graph.
    LLM and embeddings are configured from config.yml.
    """
    from mem0 import Memory
    from mem0.configs.base import (
        EmbedderConfig,
        GraphStoreConfig,
        LlmConfig,
        MemoryConfig,
        VectorStoreConfig,
    )

    from app.config import get_api_key

    # Extraction LLM config — AE config top-level "extraction" section.
    # Falls back to "llm" for backward compat with legacy single-config.
    llm_config = config.get("extraction", {}) or config.get("llm", {})
    embeddings_config = config.get("embeddings", {})

    llm_api_key = get_api_key(llm_config)
    embeddings_api_key = get_api_key(embeddings_config)

    postgres_password = os.environ.get("POSTGRES_PASSWORD", "")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "")

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
                "collection_name": "mem0_memories",
                "embedding_model_dims": _get_embedding_dims(config, embeddings_config),
                "diskann": False,
            },
        ),
        graph_store=GraphStoreConfig(
            provider="neo4j",
            config=_neo4j_config(neo4j_password),
        ),
    )

    return Memory(config=mem_config)


def _neo4j_config(password: str) -> dict:
    """Build Neo4j connection config.

    Mem0's GraphStoreConfig requires url, username, and password fields
    even when Neo4j runs with NEO4J_AUTH=none. When password is empty,
    we pass "neo4j"/"neo4j" as dummy credentials — Neo4j ignores them
    when auth is disabled.
    """
    url = f"bolt://{os.environ.get('NEO4J_HOST', 'context-broker-neo4j')}:{os.environ.get('NEO4J_PORT', '7687')}"
    return {
        "url": url,
        "username": "neo4j",
        "password": password or "neo4j",
    }


def _get_embedding_dims(config: dict, embeddings_config: dict) -> int:
    """Return the embedding dimensions for the configured model.

    Checks for an explicit ``embedding_dims`` value in the top-level config
    first (settable in config.yml).  Falls back to a built-in lookup table
    keyed by model name, defaulting to 1536.
    """
    # Prefer explicit config override
    configured_dims = config.get("embedding_dims")
    if configured_dims is not None:
        return int(configured_dims)

    model = embeddings_config.get("model", "text-embedding-3-small")
    # Known dimension mappings (fallback)
    dims_map = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
        # M-19: Ollama / nomic models
        "nomic-embed-text": 768,
        "nomic-embed-text:latest": 768,
    }
    return dims_map.get(model, 1536)
