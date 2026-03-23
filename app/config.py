"""
Configuration management for the Context Broker.

Reads /config/config.yml on each call to load_config() so that
hot-reloadable settings (inference providers, build types) take
effect immediately without restart.

Infrastructure settings (database connections) are read once at
startup and cached.
"""

import asyncio
import hashlib
import json
import logging
import os
import threading
from functools import lru_cache
from typing import Any

import yaml

_log = logging.getLogger("context_broker.config")

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/config/config.yml")

# Cached config with mtime check to avoid repeated file reads (M-11).
# os.stat() is near-instant and avoids synchronous file I/O on every call.
_config_cache: dict[str, Any] | None = None
_config_mtime: float = 0.0
_config_content_hash: str = ""

# G5-04: Lock for compound clear-and-set operations on caches.
# Individual dict ops are atomic under CPython's GIL, but the
# clear-LLM-cache + clear-embeddings-cache sequence in load_config
# must be atomic to prevent a concurrent reader from seeing a
# half-cleared state.
_cache_lock = threading.Lock()


def _read_and_parse_config() -> tuple[dict[str, Any], str]:
    """Read config.yml from disk and return (parsed_dict, raw_text).

    Separated from load_config() so that async_load_config() can
    offload only this blocking portion to run_in_executor.
    """
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = f.read()
        config = yaml.safe_load(raw)
        if not isinstance(config, dict):
            raise ValueError("config.yml must be a YAML mapping at the top level")
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Configuration file not found at {CONFIG_PATH}. "
            "Mount /config/config.yml into the container."
        ) from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Failed to parse {CONFIG_PATH}: {exc}") from exc
    return config, raw


def _apply_config(config: dict[str, Any], raw: str, current_mtime: float) -> dict[str, Any]:
    """Update global cache state after a successful config read.

    Shared by both load_config() and async_load_config().
    R5-M24: All global state updates are performed inside _cache_lock
    to prevent concurrent readers from seeing a half-updated state.
    """
    global _config_cache, _config_mtime, _config_content_hash

    new_hash = hashlib.sha256(raw.encode()).hexdigest()
    with _cache_lock:
        if new_hash != _config_content_hash and _config_content_hash != "":
            _log.info("Config file content changed — clearing LLM and embeddings caches")
            _llm_cache.clear()
            _embeddings_cache.clear()

        _config_cache = config
        _config_mtime = current_mtime
        _config_content_hash = new_hash
    return config


def load_config() -> dict[str, Any]:
    """Load and return the full configuration from /config/config.yml.

    Uses mtime-based caching: only re-reads the file when it changes.
    On config change, clears the LLM and embeddings caches (M-03)
    so hot-reloaded provider settings take effect immediately.

    G5-06: This function performs blocking file I/O (os.stat + open/read).
    The mtime cache means the file is only re-read when it actually changes
    on disk, which is rare in production. The os.stat() fast-path check is
    near-instant for local files. Async callers (route handlers, flow nodes)
    should use async_load_config() instead, which offloads the file read to
    run_in_executor when a re-read is triggered.

    Raises RuntimeError if the file cannot be read or parsed.
    """
    global _config_cache, _config_mtime, _config_content_hash

    try:
        current_mtime = os.stat(CONFIG_PATH).st_mtime
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Configuration file not found at {CONFIG_PATH}. "
            "Mount /config/config.yml into the container."
        ) from exc

    # CB-R3-09: Float equality on mtime is a fast-path optimisation only.
    # If mtime changes we fall through and re-read, but actual cache
    # invalidation is gated on the SHA-256 content hash below, so
    # platform-level mtime precision differences are harmless.
    if _config_cache is not None and current_mtime == _config_mtime:
        return _config_cache

    config, raw = _read_and_parse_config()
    return _apply_config(config, raw, current_mtime)


async def async_load_config() -> dict[str, Any]:
    """Async wrapper for load_config().

    Uses the same mtime-based cache as load_config(). The os.stat()
    fast-path check is synchronous (near-instant for local files).
    Only when a re-read is actually needed does it offload the file
    read + YAML parse to run_in_executor to avoid blocking the event loop.

    Route handlers and flow nodes should prefer this over load_config().
    """
    global _config_cache, _config_mtime, _config_content_hash

    try:
        current_mtime = os.stat(CONFIG_PATH).st_mtime
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Configuration file not found at {CONFIG_PATH}. "
            "Mount /config/config.yml into the container."
        ) from exc

    if _config_cache is not None and current_mtime == _config_mtime:
        return _config_cache

    loop = asyncio.get_running_loop()
    config, raw = await loop.run_in_executor(None, _read_and_parse_config)
    return _apply_config(config, raw, current_mtime)


@lru_cache(maxsize=1)
def load_startup_config() -> dict[str, Any]:
    """Load configuration once at startup for infrastructure settings.

    Cached — changes require container restart.
    """
    return load_config()


def get_api_key(provider_config: dict[str, Any]) -> str:
    """Resolve an API key from the environment variable named in api_key_env.

    Returns empty string if api_key_env is not set (e.g., local Ollama).
    """
    env_var_name = provider_config.get("api_key_env", "")
    if not env_var_name:
        return ""
    api_key = os.environ.get(env_var_name, "")
    if not api_key:
        _log.warning(
            "API key environment variable '%s' is not set or empty", env_var_name
        )
    return api_key


def get_build_type_config(config: dict[str, Any], build_type_name: str) -> dict[str, Any]:
    """Return the configuration for a named build type.

    Raises ValueError if the build type is not defined.
    """
    build_types = config.get("build_types", {})
    if build_type_name not in build_types:
        raise ValueError(
            f"Build type '{build_type_name}' not found in config.yml. "
            f"Available: {list(build_types.keys())}"
        )
    bt_config = build_types[build_type_name]

    # Validate that ALL tier percentages sum to <= 1.0
    pct_keys = ["tier1_pct", "tier2_pct", "tier3_pct", "semantic_retrieval_pct", "knowledge_graph_pct"]
    total_pct = sum(bt_config.get(k, 0) or 0 for k in pct_keys)
    if total_pct > 1.0:
        raise ValueError(
            f"Build type '{build_type_name}' tier percentages sum to {total_pct:.2f}, "
            f"which exceeds 1.0. Keys checked: {pct_keys}"
        )

    return bt_config


def get_tuning(config: dict, key: str, default: Any) -> Any:
    """Return a tuning parameter from config, with fallback to default."""
    return config.get("tuning", {}).get(key, default)


def get_log_level(config: dict[str, Any]) -> str:
    """Return the configured log level, defaulting to INFO."""
    return config.get("log_level", "INFO").upper()


def verbose_log(config: dict, logger: Any, message: str, *args: Any) -> None:
    """Log a message only if verbose_logging is enabled in tuning config.

    REQ-001 section 4.8: Verbose pipeline logging for node entry/exit with timing.
    Accepts printf-style args for lazy formatting.
    """
    if get_tuning(config, "verbose_logging", False):
        logger.info(message, *args)


def verbose_log_auto(logger: Any, message: str, *args: Any) -> None:
    """Log a verbose message by reading config on each call.

    For flows that don't carry config in their state.
    Falls back to no-op if config cannot be loaded.
    """
    try:
        cfg = load_config()
        if get_tuning(cfg, "verbose_logging", False):
            logger.info(message, *args)
    except (RuntimeError, OSError, ValueError, TypeError):
        # R6-m6: Broadened to catch bad YAML structure (ValueError/TypeError)
        # in addition to file-level errors. Verbose logging must never crash.
        pass


# ============================================================
# Cached LLM / Embedding client factories (M-09)
# ============================================================

# G5-04: Individual dict operations (__getitem__, __setitem__, __contains__)
# are atomic under CPython's GIL, so these caches are safe for concurrent
# async reads/writes without an explicit asyncio.Lock. Compound operations
# (clear-and-set in load_config) are protected by _cache_lock above.
_llm_cache: dict[str, Any] = {}
_embeddings_cache: dict[str, Any] = {}

# R2-F07: Simple bounded-cache eviction threshold.
_MAX_CACHE_ENTRIES = 10


def get_chat_model(config: dict) -> Any:
    """Return a cached ChatOpenAI instance keyed by (base_url, model).

    Avoids re-creating the client on every request.
    R5-M12: Uses _cache_lock around the full check-and-set to prevent
    two concurrent calls from both missing the cache and creating
    duplicate clients.
    """
    from langchain_openai import ChatOpenAI

    llm_config = config.get("llm", {})
    api_key = get_api_key(llm_config)
    # G5-05: Include api_key hash so credential rotation doesn't reuse a stale client.
    # m3: Use stable SHA-256 hash instead of Python's hash() which is
    # randomized per process (PYTHONHASHSEED).
    cache_key = (
        f"{llm_config.get('base_url')}:{llm_config.get('model')}:{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"
    )
    with _cache_lock:
        if cache_key not in _llm_cache:
            # R5-m9: Evict oldest entry instead of clearing entire cache.
            if len(_llm_cache) >= _MAX_CACHE_ENTRIES:
                oldest_key = next(iter(_llm_cache))
                del _llm_cache[oldest_key]
            _llm_cache[cache_key] = ChatOpenAI(
                base_url=llm_config.get("base_url", "https://api.openai.com/v1"),
                model=llm_config.get("model", "gpt-4o-mini"),
                api_key=api_key or "not-needed",
                timeout=1800,
            )
        return _llm_cache[cache_key]


def get_embeddings_model(config: dict) -> Any:
    """Return a cached OpenAIEmbeddings instance keyed by (base_url, model).

    Avoids re-creating the client on every request.
    R5-M12: Uses _cache_lock around the full check-and-set to prevent
    two concurrent calls from both missing the cache and creating
    duplicate clients.
    """
    from langchain_openai import OpenAIEmbeddings

    embeddings_config = config.get("embeddings", {})
    api_key = get_api_key(embeddings_config)
    # G5-05: Include api_key hash so credential rotation doesn't reuse a stale client.
    # m3: Use stable SHA-256 hash instead of Python's hash() which is
    # randomized per process (PYTHONHASHSEED).
    cache_key = (
        f"{embeddings_config.get('base_url')}:{embeddings_config.get('model')}:{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"
    )
    with _cache_lock:
        if cache_key not in _embeddings_cache:
            # R5-m9: Evict oldest entry instead of clearing entire cache.
            if len(_embeddings_cache) >= _MAX_CACHE_ENTRIES:
                oldest_key = next(iter(_embeddings_cache))
                del _embeddings_cache[oldest_key]
            _embeddings_cache[cache_key] = OpenAIEmbeddings(
                model=embeddings_config.get("model", "text-embedding-3-small"),
                openai_api_base=embeddings_config.get("base_url", "https://api.openai.com/v1"),
                openai_api_key=api_key or "not-needed",
            )
        return _embeddings_cache[cache_key]
