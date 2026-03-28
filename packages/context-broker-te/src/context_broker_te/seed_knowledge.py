"""Seed knowledge articles for the Imperator.

Populates domain_information with operational articles on first startup.
Called once — checks if the table is empty before seeding.
"""

import logging

import asyncpg

_log = logging.getLogger("context_broker.te.seed_knowledge")

SEED_ARTICLES = [
    {
        "source": "seed",
        "content": (
            "CHANGING INFERENCE MODELS: I can switch my own LLM or any pipeline "
            "model using the change_inference tool. Call it with just a slot name "
            "(imperator, summarization, extraction, embeddings) to see available "
            "models from the catalog. To switch, provide the provider and model. "
            "Embedding changes are destructive — they wipe all vectors and re-embed. "
            "Other slots hot-reload without restart. The catalog lives at "
            "/config/inference-models.yml."
        ),
    },
    {
        "source": "seed",
        "content": (
            "MANAGING SCHEDULES: I can create recurring tasks using the scheduling "
            "tools: list_schedules, create_schedule, enable_schedule, disable_schedule. "
            "Schedules are stored in the database with cron-style timing. Each schedule "
            "has a name, cron expression, and action. The scheduler worker checks for "
            "due schedules on each poll cycle using optimistic locking to prevent "
            "duplicate firing."
        ),
    },
    {
        "source": "seed",
        "content": (
            "MONITORING HEALTH: I can check system health via the pipeline_status "
            "tool, which shows pending embedding, extraction, and assembly jobs. "
            "I can also user_prompt container logs using log_query (SQL-based filtering "
            "by container, level, time range, keyword) or search_logs (semantic "
            "search if log vectorization is enabled). The /health endpoint shows "
            "backing service connectivity (postgres, neo4j)."
        ),
    },
    {
        "source": "seed",
        "content": (
            "SEARCHING CONVERSATIONS: I can search across all conversations using "
            "conv_search (which runs hybrid vector + BM25 search with optional "
            "reranking) or mem_search (which searches the Neo4j knowledge graph "
            "for extracted facts and relationships). For browsing, I can list "
            "conversations with conv_list_conversations and optionally filter by "
            "participant name."
        ),
    },
    {
        "source": "seed",
        "content": (
            "DOMAIN KNOWLEDGE MANAGEMENT: I maintain a private store of domain "
            "information using store_domain_info and search_domain_info. This is "
            "my long-term semantic memory about operational procedures, user "
            "preferences, and deployment-specific facts. I decide what to store — "
            "there is no automatic extraction. When I learn something important "
            "that should persist across conversations, I store it here."
        ),
    },
    {
        "source": "seed",
        "content": (
            "CONTEXT INTROSPECTION: I can inspect how my context window is assembled "
            "using context_introspection. This shows the tier breakdown (archival, "
            "chunk summaries, recent verbatim), token usage per tier, build type, "
            "and effective utilization. Useful for understanding what I can and "
            "cannot recall from conversation history."
        ),
    },
    {
        "source": "seed",
        "content": (
            "SYSTEM PROMPT MANAGEMENT: I can read my current system prompt with "
            "read_system_prompt and update it with update_system_prompt. The system "
            "prompt defines my Identity, Purpose, and behavioral instructions. "
            "Changes take effect on my next invocation (hot-reload). The prompt "
            "file lives at /config/prompts/ and is referenced by te.yml."
        ),
    },
    {
        "source": "seed",
        "content": (
            "WEB RESEARCH: I can search the web using web_search (DuckDuckGo) and "
            "read web pages using web_read (extracts clean text from HTML). I can "
            "also download files to /data/downloads/ using file_write. Web reading "
            "uses the system SSL certificate store for HTTPS. If crawl4ai is not "
            "available, a basic HTML stripping fallback is used."
        ),
    },
    {
        "source": "seed",
        "content": (
            "NOTIFICATIONS: I can send notifications via the send_notification tool, "
            "which POSTs to a configured webhook URL. This works with ntfy.sh, "
            "Slack, Discord, or any service that accepts webhook POSTs. The webhook "
            "URL is configured in the TE config. If no webhook is configured, the "
            "tool reports that notifications are not available."
        ),
    },
    {
        "source": "seed",
        "content": (
            "FILESYSTEM ACCESS: I can read files with file_read, list directories "
            "with file_list, and search file contents with file_search. Access is "
            "sandboxed to /app, /config, and /data directories. I can write files "
            "only to /data/downloads/. This lets me inspect my own configuration, "
            "code, and data without needing admin tools."
        ),
    },
    {
        "source": "seed",
        "content": (
            "SYSTEM COMMANDS: I can run a limited set of allowlisted shell commands "
            "using run_command: df, uptime, free, ps, top, cat, ls, wc, du, date, "
            "hostname, whoami, pip list, pip show, python --version. These are "
            "read-only diagnostic commands. I also have calculate for safe math "
            "evaluation."
        ),
    },
    {
        "source": "seed",
        "content": (
            "OUT-OF-BOX CONFIGURATION: The Context Broker works without any API "
            "keys when Ollama and Infinity containers are present. Default models: "
            "qwen2.5:7b on Ollama for imperator, summarization, and extraction; "
            "nomic-ai/nomic-embed-text-v1.5 on Infinity for embeddings; "
            "mixedbread-ai/mxbai-rerank-xsmall-v1 on Infinity for reranking. "
            "To use cloud providers, add API keys to /config/credentials/.env "
            "and update config.yml/te.yml."
        ),
    },
    {
        "source": "seed",
        "content": (
            "TROUBLESHOOTING: Common issues and resolutions. "
            "1) Extraction stuck: check pipeline_status — if pending extraction is "
            "not decreasing, the LLM may be too slow or returning invalid JSON. "
            "Switch extraction model to one with verified JSON mode. "
            "2) Embeddings not processing: check that Infinity/embedding provider "
            "is reachable. Run pipeline_status to see pending count. "
            "3) Assembly not triggering: assembly requires enough new tokens "
            "(trigger_threshold_percent of window budget) since last assembly. "
            "4) Context seems stale: check context_introspection to see tier "
            "breakdown and last assembly time."
        ),
    },
    {
        "source": "seed",
        "content": (
            "ADMIN TOOLS: When admin_tools is enabled in te.yml, I gain additional "
            "capabilities: config_read (view config with redacted secrets), "
            "config_write (modify AE config values with hot-reload), db_query "
            "(read-only SQL against the database), verbose_toggle (toggle pipeline "
            "verbose logging), change_inference (switch inference models per slot), "
            "and migrate_embeddings (destructive embedding model migration). "
            "I cannot modify my own TE config — that is the architect's domain."
        ),
    },
]


async def seed_domain_knowledge() -> int:
    """Seed domain_information with operational articles if empty.

    Returns the number of articles seeded (0 if table already has content).
    """
    from app.database import get_pg_pool

    pool = get_pg_pool()

    # Check if table exists and has content
    try:
        count = await pool.fetchval("SELECT COUNT(*) FROM domain_information")
        if count > 0:
            _log.info(
                "Domain information already has %d entries — skipping seed", count
            )
            return 0
    except (asyncpg.PostgresError, OSError) as exc:
        _log.info("domain_information table not ready — skipping seed: %s", exc)
        return 0

    # Seed the articles (without embeddings — the store_domain_info tool
    # handles embedding, but for seed we insert directly and let the
    # embedding worker pick them up if vectorization is enabled)
    from app.config import async_load_config, get_embeddings_model

    config = await async_load_config()
    seeded = 0

    for article in SEED_ARTICLES:
        try:
            # Generate embedding
            embeddings_model = get_embeddings_model(config)
            vectors = await embeddings_model.aembed_documents([article["content"]])
            vec_str = "[" + ",".join(str(v) for v in vectors[0]) + "]"

            await pool.execute(
                """
                INSERT INTO domain_information (content, source, embedding)
                VALUES ($1, $2, $3::vector)
                """,
                article["content"],
                article["source"],
                vec_str,
            )
            seeded += 1
        except (asyncpg.PostgresError, ValueError, RuntimeError, OSError) as exc:
            _log.warning("Failed to seed article: %s", exc)
            # Try without embedding as fallback
            try:
                await pool.execute(
                    "INSERT INTO domain_information (content, source) VALUES ($1, $2)",
                    article["content"],
                    article["source"],
                )
                seeded += 1
            except (asyncpg.PostgresError, OSError) as exc2:
                _log.error("Failed to seed article even without embedding: %s", exc2)

    _log.info("Seeded %d domain knowledge articles", seeded)
    return seeded
