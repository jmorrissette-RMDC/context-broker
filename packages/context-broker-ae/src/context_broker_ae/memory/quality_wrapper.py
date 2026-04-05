"""
CEA Quality Wrapper -- wraps a near-stock Mem0 instance with quality metadata,
feedback tracking, enriched search, and expiration management.

All CEA quality logic lives here. Mem0 handles vector storage (pgvector),
graph extraction (Neo4j), dedup/update logic, and entity normalization.
The wrapper intercepts before add and after search.

NOTE: _global_vector_search and _global_graph_search query Mem0's internal
tables (mem0_memories) and Neo4j directly. This couples to Mem0 1.0.8's
schema. Acceptable for a forked version but fragile across Mem0 upgrades.

See PRD-context-engineering-architecture.md REQ-CEA-Q03.
See HLD-context-engineering-architecture.md S2 Quality Wrapper.
"""

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

_log = logging.getLogger("context_broker.cea.quality_wrapper")


def _timestamp_bucket(dt: datetime, bucket_seconds: int = 60) -> str:
    """Truncate a datetime to a bucket for event dedup."""
    epoch = int(dt.timestamp())
    bucket = (epoch // bucket_seconds) * bucket_seconds
    return str(bucket)


def _fact_dedup_key(content: str, conversation_id: str, user_id: str, utterance: str) -> str:
    """Deterministic dedup key for facts (REQ-CEA-A03)."""
    raw = f"{content}|{conversation_id}|{user_id}|{utterance}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_dedup_key(target_id: str, event_type: str, agent_id: str) -> str:
    """Deterministic dedup key for feedback events (REQ-CEA-A03).

    Uses a timestamp bucket so the same agent can submit the same event type
    for the same target at different times, but not twice for the same occurrence.
    """
    bucket = _timestamp_bucket(datetime.now(timezone.utc))
    raw = f"{target_id}|{event_type}|{agent_id}|{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_temporal(expires_at_raw: Optional[str], extraction_date: datetime) -> Optional[datetime]:
    """Resolve relative temporal references to absolute UTC datetimes (REQ-CEA-I04).

    Handles:
    - ISO 8601 strings (passed through)
    - Relative references like "in 3 days", "next week", "2 months"
    - None (no expiration)

    NOTE: "in N months" uses N*30 days, "in N years" uses N*365 days.
    These are approximations acceptable for expiration dates.
    """
    if not expires_at_raw:
        return None

    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(expires_at_raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    match = re.match(r"in\s+(\d+)\s+(day|week|month|year)s?", expires_at_raw.lower().strip())
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "day":
            return extraction_date + timedelta(days=amount)
        elif unit == "week":
            return extraction_date + timedelta(weeks=amount)
        elif unit == "month":
            return extraction_date + timedelta(days=amount * 30)
        elif unit == "year":
            return extraction_date + timedelta(days=amount * 365)

    _log.warning("Could not parse expires_at value: %s", expires_at_raw)
    return None


class QualityWrapper:
    """Wraps a Mem0 Memory instance with CEA quality metadata and feedback.

    All quality logic (metadata storage, feedback event log, expiration,
    quality gate, search enrichment) lives here, not inside Mem0.
    """

    def __init__(self, mem0_client, db_pool: asyncpg.Pool, config: dict):
        self.mem0 = mem0_client
        self.pool = db_pool
        self.config = config
        self._last_cleanup = 0.0

    # ------------------------------------------------------------------
    # Write side
    # ------------------------------------------------------------------

    def _apply_rejection_rules(self, content: str) -> bool:
        """Return True if the content should be rejected (not stored)."""
        cea_config = self.config.get("cea", {})
        rules = cea_config.get("rejection_rules", {})

        min_length = rules.get("min_fact_length", 10)
        if len(content.strip()) < min_length:
            _log.debug("Rejected fact (too short: %d < %d): %s", len(content.strip()), min_length, content[:50])
            return True

        patterns = rules.get("patterns", [])
        for pattern in patterns:
            if re.search(pattern, content):
                _log.debug("Rejected fact (pattern match): %s", content[:50])
                return True

        return False

    async def add(
        self,
        content: str,
        user_id: str,
        *,
        conversation_id: str,
        metadata: Optional[dict] = None,
        skip_graph: bool = False,
        dedup_utterance: str = "",
    ) -> dict:
        """Add a fact through Mem0 with idempotent dedup (REQ-CEA-A03).

        Returns {"memory_id": str, "relation_ids": list[str]} on success,
        or {"memory_id": None, "relation_ids": []} if rejected or duplicate.
        """
        _empty = {"memory_id": None, "relation_ids": []}
        if self._apply_rejection_rules(content):
            return _empty

        # Idempotency: check if this exact fact already exists by its natural key.
        # The composite unique index on (user_id, conversation_id, original_utterance)
        # in cea_quality_metadata enforces this at the DB level too, but checking
        # here avoids the Mem0 add() call for known duplicates.
        import uuid as _uuid
        try:
            conv_uuid = _uuid.UUID(conversation_id) if conversation_id else None
        except ValueError:
            conv_uuid = None

        if conv_uuid and dedup_utterance:
            existing = await self.pool.fetchval(
                """
                SELECT target_id FROM cea_quality_metadata
                WHERE user_id = $1 AND conversation_id = $2 AND original_utterance = $3
                """,
                user_id, conv_uuid, dedup_utterance,
            )
            if existing:
                _log.debug("Duplicate fact detected (natural key match): %s", content[:50])
                return _empty

        await self._maybe_cleanup()

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.mem0.add(
                messages=content,
                user_id=user_id,
                _skip_graph=skip_graph,
                metadata=metadata or {},
            ),
        )

        # Extract the memory ID from Mem0's response
        memory_id = None
        if isinstance(result, dict):
            results = result.get("results", [])
            if results:
                memory_id = results[0].get("id")
        elif isinstance(result, list) and result:
            memory_id = result[0].get("id")

        if not memory_id:
            _log.warning("Mem0 add returned no memory ID for content: %s", content[:50])
            return _empty

        # Capture graph relation IDs from Mem0's graph extraction (REQ-CEA-S02).
        # MemoryGraph.add() now returns elementId(r) for each created/merged relation.
        relation_ids = []
        if isinstance(result, dict) and result.get("relations"):
            graph_result = result["relations"]
            if isinstance(graph_result, dict):
                for entity in graph_result.get("added_entities", []):
                    if isinstance(entity, dict) and entity.get("relation_id"):
                        relation_ids.append(entity["relation_id"])
            elif isinstance(graph_result, list):
                for item in graph_result:
                    if isinstance(item, dict) and item.get("relation_id"):
                        relation_ids.append(item["relation_id"])

        if relation_ids:
            _log.debug("Graph relations created: %s", relation_ids)

        return {"memory_id": memory_id, "relation_ids": relation_ids}

    async def write_metadata(
        self,
        target_type: str,
        target_id: str,
        *,
        durability: float,
        confidence: float,
        source_type: str,
        original_utterance: str,
        extraction_model: str,
        expires_at: Optional[datetime],
        user_id: str,
        conversation_id: str,
    ) -> None:
        """Write quality metadata to the Postgres cea_quality_metadata table."""
        import uuid as _uuid
        try:
            conv_uuid = _uuid.UUID(conversation_id)
        except (ValueError, AttributeError):
            _log.warning("Invalid conversation_id for metadata: %s", conversation_id)
            return

        try:
            await self.pool.execute(
                """
                INSERT INTO cea_quality_metadata
                    (target_type, target_id, durability, confidence, source_type,
                     original_utterance, extraction_model, expires_at, user_id, conversation_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (target_type, target_id) DO NOTHING
                """,
                target_type,
                target_id,
                durability,
                confidence,
                source_type,
                original_utterance,
                extraction_model,
                expires_at,
                user_id,
                conv_uuid,
            )
        except asyncpg.UniqueViolationError:
            # Natural-key dedup index collision under concurrent extraction — safe to ignore
            _log.debug("Metadata natural-key collision (concurrent extraction): %s/%s", target_type, target_id)

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        limit: int = 10,
    ) -> dict:
        """Search Mem0 and return enriched results with quality metadata.

        Returns {"vector_facts": [...], "graph_relations": [...]}, each
        enriched with metadata and usefulness aggregates.
        The server does NOT apply ranking -- CEAc ranks client-side.
        """
        if user_id:
            loop = asyncio.get_running_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: self.mem0.search(query=query, user_id=user_id, limit=limit),
            )
        else:
            # Global search: bypass Mem0's user_id guard (fix #2 from implementation review)
            raw = await self._global_vector_search(query, limit)

        vector_facts = raw.get("results", []) if isinstance(raw, dict) else raw
        graph_relations = raw.get("relations", []) if isinstance(raw, dict) else []

        # Global search: also search graph (fix #5)
        if not user_id and not graph_relations:
            graph_relations = await self._global_graph_search(query, limit)

        fact_ids = [f.get("id") for f in vector_facts if f.get("id")]
        relation_ids = [r.get("relation_id") for r in graph_relations if r.get("relation_id")]

        all_ids_flat = [("fact", fid) for fid in fact_ids] + [("relation", rid) for rid in relation_ids]

        metadata_map = await self._get_metadata_batch(all_ids_flat)
        usefulness_map = await self._get_usefulness_batch(all_ids_flat)

        now = datetime.now(timezone.utc)
        enriched_facts = []
        for fact in vector_facts:
            fid = fact.get("id")
            meta = metadata_map.get(("fact", fid), {})
            if meta.get("expires_at") and meta["expires_at"] < now:
                continue
            fact["_metadata"] = meta
            fact["_usefulness"] = usefulness_map.get(("fact", fid), {})
            enriched_facts.append(fact)

        enriched_relations = []
        for rel in graph_relations:
            rid = rel.get("relation_id")
            meta = metadata_map.get(("relation", rid), {})
            if meta.get("expires_at") and meta["expires_at"] < now:
                continue
            rel["_metadata"] = meta
            rel["_usefulness"] = usefulness_map.get(("relation", rid), {})
            enriched_relations.append(rel)

        return {"vector_facts": enriched_facts, "graph_relations": enriched_relations}

    async def list_facts(
        self,
        *,
        user_id: Optional[str] = None,
        limit: int = 100,
    ) -> list:
        """List facts via Mem0, enriched with metadata.

        When user_id is None, lists all facts globally via direct pgvector query.
        """
        if user_id:
            loop = asyncio.get_running_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: self.mem0.get_all(user_id=user_id, limit=limit),
            )
        else:
            # Global list: query pgvector directly (Mem0 requires user_id)
            rows = await self.pool.fetch(
                """
                SELECT id, (payload->>'data') AS memory,
                       payload->>'user_id' AS user_id,
                       payload->>'created_at' AS created_at
                FROM mem0_memories
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
            raw = {"results": [
                {"id": str(r["id"]), "memory": r["memory"],
                 "user_id": r["user_id"], "created_at": r["created_at"]}
                for r in rows
            ]}

        facts = raw.get("results", []) if isinstance(raw, dict) else raw
        fact_ids = [("fact", f.get("id")) for f in facts if f.get("id")]
        metadata_map = await self._get_metadata_batch(fact_ids)

        for fact in facts:
            fid = fact.get("id")
            fact["_metadata"] = metadata_map.get(("fact", fid), {})

        return facts

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    async def record_feedback(
        self,
        target_type: str,
        target_id: str,
        event_type: str,
        agent_id: str,
        context: Optional[dict] = None,
    ) -> bool:
        """Append a feedback event. Idempotent (REQ-CEA-A03).

        Returns True if inserted, False if duplicate or error.
        """
        dedup = _event_dedup_key(target_id, event_type, agent_id)
        try:
            result = await self.pool.execute(
                """
                INSERT INTO cea_feedback_events
                    (target_type, target_id, event_type, agent_id, context, dedup_key)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                ON CONFLICT (dedup_key) DO NOTHING
                """,
                target_type,
                target_id,
                event_type,
                agent_id,
                __import__("json").dumps(context) if context else None,
                dedup,
            )
            # Check if row was actually inserted (fix #25)
            return result == "INSERT 0 1"
        except asyncpg.PostgresError as exc:
            _log.error("Failed to record feedback event: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Expiration cleanup
    # ------------------------------------------------------------------

    async def cleanup_expired(self) -> int:
        """Delete expired facts from Mem0 based on metadata table."""
        now = datetime.now(timezone.utc)
        rows = await self.pool.fetch(
            """
            SELECT target_type, target_id FROM cea_quality_metadata
            WHERE expires_at IS NOT NULL AND expires_at < $1
            """,
            now,
        )
        if not rows:
            return 0

        count = 0
        for row in rows:
            if row["target_type"] == "fact":
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        lambda mid=row["target_id"]: self.mem0.delete(mid),
                    )
                    count += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _log.warning("Failed to delete expired fact %s: %s", row["target_id"], exc)

            await self.pool.execute(
                "DELETE FROM cea_quality_metadata WHERE target_type = $1 AND target_id = $2",
                row["target_type"],
                row["target_id"],
            )

        if count:
            _log.info("Cleaned up %d expired facts", count)
        return count

    async def _maybe_cleanup(self) -> None:
        """Run cleanup if the configured interval has elapsed."""
        interval = self.config.get("cea", {}).get("expiration_cleanup_interval", 3600)
        now = time.monotonic()
        if now - self._last_cleanup >= interval:
            self._last_cleanup = now
            await self.cleanup_expired()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_metadata_batch(self, id_pairs: list[tuple[str, str]]) -> dict:
        """Fetch metadata for (target_type, target_id) pairs.

        Returns dict keyed by (target_type, target_id).
        Fix #16: filters by both target_type AND target_id.
        """
        if not id_pairs:
            return {}

        types = [p[0] for p in id_pairs]
        ids = [p[1] for p in id_pairs]

        rows = await self.pool.fetch(
            """
            SELECT target_type, target_id, durability, confidence, source_type,
                   original_utterance, extraction_model, expires_at, extracted_at,
                   user_id, conversation_id
            FROM cea_quality_metadata
            WHERE (target_type, target_id) IN (
                SELECT unnest($1::text[]), unnest($2::text[])
            )
            """,
            types,
            ids,
        )

        result = {}
        for row in rows:
            key = (row["target_type"], row["target_id"])
            result[key] = dict(row)
        return result

    async def _get_usefulness_batch(self, id_pairs: list[tuple[str, str]]) -> dict:
        """Compute usefulness aggregates from the feedback event log.

        Filters by both target_type and target_id to prevent cross-contamination
        between facts and relations that share the same ID string.
        """
        if not id_pairs:
            return {}

        types = [p[0] for p in id_pairs]
        ids = [p[1] for p in id_pairs]

        rows = await self.pool.fetch(
            """
            SELECT target_type, target_id, event_type, COUNT(*) as cnt
            FROM cea_feedback_events
            WHERE (target_type, target_id) IN (
                SELECT unnest($1::text[]), unnest($2::text[])
            )
            GROUP BY target_type, target_id, event_type
            """,
            types,
            ids,
        )

        result = {}
        for row in rows:
            key = (row["target_type"], row["target_id"])
            if key not in result:
                result[key] = {"total_events": 0}
            result[key][row["event_type"]] = row["cnt"]
            result[key]["total_events"] += row["cnt"]
        return result

    async def _global_vector_search(self, query: str, limit: int) -> dict:
        """Search pgvector directly without Mem0's user_id guard.

        Used for global scope where Mem0.search() requires user_id.
        Couples to Mem0 1.0.8 internal schema (mem0_memories table).
        """
        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(
            None,
            lambda: self.mem0.embedding_model.embed(query, "search"),
        )

        rows = await self.pool.fetch(
            """
            SELECT id, (payload->>'data') AS memory,
                   1 - (embedding <=> $1::vector) AS score,
                   payload->>'hash' AS hash,
                   payload->>'created_at' AS created_at,
                   payload->>'updated_at' AS updated_at,
                   payload->>'user_id' AS user_id
            FROM mem0_memories
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            str(embedding),
            limit,
        )

        results = []
        for row in rows:
            results.append({
                "id": str(row["id"]),
                "memory": row["memory"],
                "score": row["score"],
                "hash": row["hash"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "user_id": row["user_id"],
            })

        return {"results": results, "relations": []}

    async def _global_graph_search(self, query: str, limit: int) -> list:
        """Search Neo4j graph directly for global scope (fix #5).

        Bypasses Mem0's user_id requirement on graph search.
        Returns list of relation dicts with elementId fields.
        """
        if not hasattr(self.mem0, "graph") or not self.mem0.enable_graph:
            return []

        try:
            loop = asyncio.get_running_loop()
            embedding = await loop.run_in_executor(
                None,
                lambda: self.mem0.embedding_model.embed(query, "search"),
            )

            # Direct Neo4j query without user_id filter
            cypher = """
            MATCH (n:Entity)
            WHERE n.embedding IS NOT NULL
            WITH n, round(2 * vector.similarity.cosine(n.embedding, $embedding) - 1, 4) AS similarity
            WHERE similarity >= $threshold
            CALL {
                WITH n
                MATCH (n)-[r]->(m:Entity)
                WHERE r.valid IS NULL OR r.valid = true
                RETURN n.name AS source, elementId(n) AS source_id,
                       type(r) AS relationship, elementId(r) AS relation_id,
                       m.name AS destination, elementId(m) AS destination_id
                UNION
                WITH n
                MATCH (n)<-[r]-(m:Entity)
                WHERE r.valid IS NULL OR r.valid = true
                RETURN m.name AS source, elementId(m) AS source_id,
                       type(r) AS relationship, elementId(r) AS relation_id,
                       n.name AS destination, elementId(n) AS destination_id
            }
            WITH DISTINCT source, source_id, relationship, relation_id, destination, destination_id, similarity
            RETURN source, source_id, relationship, relation_id, destination, destination_id, similarity
            ORDER BY similarity DESC
            LIMIT $limit
            """

            graph_db = self.mem0.graph.graph
            results = await loop.run_in_executor(
                None,
                lambda: graph_db.query(
                    cypher,
                    params={
                        "embedding": embedding,
                        "threshold": getattr(self.mem0.graph, "threshold", 0.7),
                        "limit": limit,
                    },
                ),
            )
            return results or []
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning("Global graph search failed: %s", exc)
            return []


# ------------------------------------------------------------------
# Singleton factory
# ------------------------------------------------------------------

_wrapper_instance: Optional[QualityWrapper] = None
_wrapper_lock: Optional[asyncio.Lock] = None


def reset_quality_wrapper() -> None:
    """Invalidate the QualityWrapper singleton so next call creates a fresh one.

    Called when the underlying Mem0 client or DB pool is recreated to
    prevent the wrapper from holding stale references.
    """
    global _wrapper_instance
    _wrapper_instance = None
    _log.info("QualityWrapper invalidated — will recreate on next call")


async def get_quality_wrapper(config: Optional[dict] = None) -> QualityWrapper:
    """Get or create the singleton QualityWrapper instance."""
    global _wrapper_instance, _wrapper_lock

    if _wrapper_lock is None:
        _wrapper_lock = asyncio.Lock()

    async with _wrapper_lock:
        if _wrapper_instance is not None:
            return _wrapper_instance

        from context_broker_ae.memory.mem0_client import get_mem0_client
        from app.database import get_pg_pool

        if config is None:
            from app.config import load_merged_config
            config = load_merged_config()

        mem0 = await get_mem0_client(config)
        pool = get_pg_pool()

        _wrapper_instance = QualityWrapper(mem0, pool, config)
        _log.info("QualityWrapper initialized")
        return _wrapper_instance
