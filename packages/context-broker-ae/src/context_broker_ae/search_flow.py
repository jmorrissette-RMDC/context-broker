"""
Search Flows — LangGraph StateGraph flows for conversation and message search.

Implements hybrid search (vector + BM25 + reranking) via Reciprocal Rank Fusion.
Handles conv_search and conv_search_messages tools.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
import openai
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.config import get_embeddings_model, get_tuning
from app.database import get_pg_pool

_log = logging.getLogger("context_broker.flows.search")


async def _rerank_via_api(
    query: str, candidates: list[dict], config: dict
) -> list[dict]:
    """Rerank candidates by calling a /rerank endpoint.

    Works with Infinity (local), Together, Cohere, Jina, Voyage, or any
    provider exposing the /rerank standard. The base_url should include
    any path prefix the provider requires (e.g., https://api.together.xyz/v1
    for Together, http://context-broker-infinity:7997 for Infinity).
    """
    reranker_config = config.get("reranker", {})
    base_url = reranker_config.get("base_url", "").rstrip("/")
    model = reranker_config.get("model", "")
    top_n = reranker_config.get("top_n", 10)

    documents = [c.get("content") or "" for c in candidates]

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{base_url}/rerank",
            json={
                "model": model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
            },
        )
        response.raise_for_status()
        data = response.json()

    # Normalize response — different providers use different keys
    results = data.get("results") or data.get("choices") or data.get("data") or []

    # Work on shallow copies to avoid mutating the input state
    scored = [dict(c) for c in candidates]

    for item in results:
        idx = item.get("index", 0)
        score = item.get("relevance_score", 0)
        if idx < len(scored):
            scored[idx]["rerank_score"] = score

    reranked = sorted(scored, key=lambda x: x.get("rerank_score", 0), reverse=True)
    return reranked[:top_n]


# ============================================================
# Conversation Search Flow
# ============================================================


class ConversationSearchState(TypedDict):
    """State for conversation search."""

    query: Optional[str]
    limit: int
    offset: int
    date_from: Optional[str]
    date_to: Optional[str]
    flow_id: Optional[str]
    user_id: Optional[str]
    sender: Optional[str]
    config: dict

    query_embedding: Optional[list[float]]
    results: list[dict]
    warning: Optional[str]
    error: Optional[str]


async def embed_conversation_query(state: ConversationSearchState) -> dict:
    """Generate embedding for the search query if provided."""
    if not state.get("query"):
        return {"query_embedding": None}

    config = state["config"]

    try:
        embeddings_model = get_embeddings_model(config)
        embedding = await embeddings_model.aembed_query(state["query"])
        return {"query_embedding": embedding}
    except (openai.APIError, httpx.HTTPError, ValueError) as exc:
        _log.warning(
            "Conversation search: embedding failed, falling back to text: %s", exc
        )
        return {
            "query_embedding": None,
            "warning": "embedding unavailable, results are text-only",
        }


async def search_conversations_db(state: ConversationSearchState) -> dict:
    """Search conversations using vector similarity or structured filters."""
    pool = get_pg_pool()
    query_embedding = state.get("query_embedding")
    limit = state["limit"]
    offset = state["offset"]
    date_from = state.get("date_from")
    date_to = state.get("date_to")
    filter_flow_id = state.get("flow_id")
    filter_user_id = state.get("user_id")
    filter_sender = state.get("sender")

    # M-21: Validate date strings early to avoid unhandled ValueError.
    # R6-m11: All timestamps in the system are stored and compared in UTC.
    # Naive datetime inputs are assumed UTC. This is consistent with PostgreSQL
    # column types (timestamptz) and Redis TTLs.
    parsed_date_from = None
    parsed_date_to = None
    try:
        if date_from:
            parsed_date_from = datetime.fromisoformat(date_from)
            # R5-M19: Assume UTC if no timezone provided
            if parsed_date_from.tzinfo is None:
                parsed_date_from = parsed_date_from.replace(tzinfo=timezone.utc)
        if date_to:
            parsed_date_to = datetime.fromisoformat(date_to)
            if parsed_date_to.tzinfo is None:
                parsed_date_to = parsed_date_to.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        return {"error": f"Invalid date format: {exc}", "results": []}

    def _build_conv_filters(
        start_idx: int, table_prefix: str = ""
    ) -> tuple[str, list, int]:
        """Build dynamic WHERE clause fragments for conversation filters.

        CB-R3-07: Build filter list and args together, compute indices at the end.
        Returns (sql_fragment, args_list, next_idx).
        """
        prefix = f"{table_prefix}." if table_prefix else ""
        filters: list[str] = []
        args: list = []
        if filter_flow_id:
            filters.append(f"{prefix}flow_id = ${{}}")
            args.append(filter_flow_id)
        if filter_user_id:
            filters.append(f"{prefix}user_id = ${{}}")
            args.append(filter_user_id)
        if filter_sender:
            # R6-M18: sender lives on messages table, not conversations, so a
            # correlated EXISTS subquery is required. For performance, the
            # idx_messages_conversation_sender index covers this query.
            filters.append(
                f"EXISTS (SELECT 1 FROM conversation_messages sm WHERE sm.conversation_id = {prefix}id AND sm.sender = ${{}})"
            )
            args.append(filter_sender)
        if parsed_date_from:
            filters.append(f"{prefix}created_at >= ${{}}::timestamptz")
            args.append(parsed_date_from)
        if parsed_date_to:
            filters.append(f"{prefix}created_at <= ${{}}::timestamptz")
            args.append(parsed_date_to)
        # Enumerate and format parameter indices
        clauses = ""
        for i, f in enumerate(filters):
            clauses += " AND " + f.format(start_idx + i)
        next_idx = start_idx + len(filters)
        return clauses, args, next_idx

    if query_embedding is not None:
        vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
        # Base params: $1=vec, $2=limit, $3=offset; extra filters start at $4+
        extra_where, extra_args, _ = _build_conv_filters(4, table_prefix="c")

        rows = await pool.fetch(
            f"""
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   c.total_messages, c.estimated_token_count,
                   MIN(cm.embedding <=> $1::vector) AS relevance_score
            FROM conversations c
            JOIN conversation_messages cm ON cm.conversation_id = c.id
            WHERE cm.embedding IS NOT NULL{extra_where}
            GROUP BY c.id, c.title, c.created_at, c.updated_at,
                     c.total_messages, c.estimated_token_count
            ORDER BY relevance_score ASC
            LIMIT $2 OFFSET $3
            """,
            vec_str,
            limit,
            offset,
            *extra_args,
        )
    else:
        # Base params: $1=limit, $2=offset; extra filters start at $3+
        extra_where, extra_args, _ = _build_conv_filters(3)

        where_clause = "WHERE 1=1" + extra_where if extra_where else ""

        rows = await pool.fetch(
            f"""
            SELECT id, title, created_at, updated_at, total_messages, estimated_token_count
            FROM conversations
            {where_clause}
            ORDER BY updated_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
            *extra_args,
        )

    results = []
    for row in rows:
        r = dict(row)
        r["id"] = str(r["id"])
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
        if r.get("updated_at"):
            r["updated_at"] = r["updated_at"].isoformat()
        results.append(r)

    return {"results": results}


def build_conversation_search_flow() -> StateGraph:
    """Build and compile the conversation search StateGraph."""
    workflow = StateGraph(ConversationSearchState)

    workflow.add_node("embed_conversation_query", embed_conversation_query)
    workflow.add_node("search_conversations_db", search_conversations_db)

    workflow.set_entry_point("embed_conversation_query")
    workflow.add_edge("embed_conversation_query", "search_conversations_db")
    workflow.add_edge("search_conversations_db", END)

    return workflow.compile()


# ============================================================
# Message Search Flow (Hybrid: vector + BM25 + reranking)
# ============================================================


class MessageSearchState(TypedDict):
    """State for message hybrid search."""

    query: str
    conversation_id: Optional[str]
    sender: Optional[str]
    role: Optional[str]
    date_from: Optional[str]
    date_to: Optional[str]
    limit: int
    config: dict

    query_embedding: Optional[list[float]]
    candidates: list[dict]
    reranked_results: list[dict]
    warning: Optional[str]
    error: Optional[str]


async def embed_message_query(state: MessageSearchState) -> dict:
    """Generate embedding for the message search query."""
    config = state["config"]

    try:
        embeddings_model = get_embeddings_model(config)
        embedding = await embeddings_model.aembed_query(state["query"])
        return {"query_embedding": embedding}
    except (openai.APIError, httpx.HTTPError, ValueError) as exc:
        _log.warning("Message search: embedding failed, falling back to BM25: %s", exc)
        return {
            "query_embedding": None,
            "warning": "embedding unavailable, results are text-only",
        }


async def hybrid_search_messages(state: MessageSearchState) -> dict:
    """Perform hybrid search using vector ANN + BM25 combined via RRF.

    M-20: All structured filters (sender, role, date_from, date_to) are
    pushed into the SQL WHERE clauses of both CTEs so that filtering happens
    before top-K selection, not after. This prevents valid results from being
    excluded by post-query filtering.

    M-21: Date strings are validated early; invalid formats return an error.

    Reciprocal Rank Fusion (RRF) combines rankings from both retrieval methods.
    """
    pool = get_pg_pool()
    query = state["query"]
    query_embedding = state.get("query_embedding")
    conversation_id = state.get("conversation_id")
    limit = state["limit"]

    rrf_k = int(get_tuning(state["config"], "rrf_constant", 60))
    candidate_limit = int(get_tuning(state["config"], "search_candidate_limit", 100))

    # M-21: Validate date strings early
    filter_sender = state.get("sender")
    filter_role = state.get("role")
    filter_date_from = state.get("date_from")
    filter_date_to = state.get("date_to")

    parsed_date_from = None
    parsed_date_to = None
    try:
        if filter_date_from:
            parsed_date_from = datetime.fromisoformat(filter_date_from)
            # R5-M19: Assume UTC if no timezone provided
            if parsed_date_from.tzinfo is None:
                parsed_date_from = parsed_date_from.replace(tzinfo=timezone.utc)
        if filter_date_to:
            parsed_date_to = datetime.fromisoformat(filter_date_to)
            if parsed_date_to.tzinfo is None:
                parsed_date_to = parsed_date_to.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        return {"candidates": [], "error": f"Invalid date format: {exc}"}

    # TA-06: Default to excluding assistant/tool messages from search results.
    # Assistant messages echo user queries and pollute results with near-identical
    # RRF scores. Tool messages are error outputs. Only include them if the caller
    # explicitly requests a specific role.
    if not filter_role:
        filter_role = "user"

    # TA-06: Exclude short command-like messages (e.g., "Search for X") that
    # are stored as user messages but contain no substantive content.
    min_content_length = 50

    # M-20: Build dynamic WHERE clause fragments with parameterized args.
    # CB-R3-07: Build filter list and args together, compute indices at the end.
    # This eliminates manual index tracking throughout the function.
    def _build_extra_filters(start_idx: int) -> tuple[str, list, int]:
        """Return (sql_fragment, args_list, next_idx) for the structured filters."""
        filters: list[str] = []
        args: list = []
        if conversation_id:
            filters.append("conversation_id = ${}::uuid")
            args.append(uuid.UUID(conversation_id))
        if filter_sender:
            filters.append("sender = ${}")
            args.append(filter_sender)
        if filter_role:
            filters.append("role = ${}")
            args.append(filter_role)
        if min_content_length > 0:
            filters.append(f"length(content) > {min_content_length}")
            # No parameter needed — literal integer in SQL
        if parsed_date_from:
            filters.append("created_at >= ${}::timestamptz")
            args.append(parsed_date_from)
        if parsed_date_to:
            filters.append("created_at <= ${}::timestamptz")
            args.append(parsed_date_to)
        # Enumerate and format parameter indices
        clauses = ""
        for i, f in enumerate(filters):
            clauses += " AND " + f.format(start_idx + i)
        next_idx = start_idx + len(filters)
        return clauses, args, next_idx

    if query_embedding is not None:
        vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        # Base params for vector+BM25: $1=vec, $2=query_text
        # Then extra filters, then candidate_limit, rrf_k, result_limit
        extra_where, extra_args, next_idx = _build_extra_filters(3)
        candidate_limit_idx = next_idx
        rrf_k_idx = next_idx + 1
        result_limit_idx = next_idx + 2

        sql = f"""
            WITH vector_ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector) AS rank
                FROM conversation_messages
                WHERE embedding IS NOT NULL{extra_where}
                LIMIT ${candidate_limit_idx}
            ),
            bm25_ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           ORDER BY ts_rank(content_tsv, plainto_tsquery('english', $2)) DESC
                       ) AS rank
                FROM conversation_messages
                WHERE content_tsv @@ plainto_tsquery('english', $2){extra_where}
                LIMIT ${candidate_limit_idx}
            ),
            rrf AS (
                SELECT COALESCE(v.id, b.id) AS id,
                       COALESCE(1.0 / (${rrf_k_idx} + v.rank), 0) +
                       COALESCE(1.0 / (${rrf_k_idx} + b.rank), 0) AS rrf_score
                FROM vector_ranked v
                FULL OUTER JOIN bm25_ranked b ON v.id = b.id
            )
            SELECT m.id, m.conversation_id, m.role, m.sender, m.recipient,
                   m.content, m.sequence_number, m.created_at, m.token_count,
                   r.rrf_score AS score
            FROM conversation_messages m
            JOIN rrf r ON m.id = r.id
            ORDER BY score DESC
            LIMIT ${result_limit_idx}
        """

        rows = await pool.fetch(
            sql,
            vec_str,
            query,
            *extra_args,
            candidate_limit,
            rrf_k,
            limit * 5,  # over-fetch for reranking
        )
    else:
        # BM25 only fallback
        # Base param: $1=query_text
        extra_where, extra_args, next_idx = _build_extra_filters(2)
        result_limit_idx = next_idx

        sql = f"""
            SELECT id, conversation_id, role, sender, recipient,
                   content, sequence_number, created_at, token_count,
                   ts_rank(content_tsv, plainto_tsquery('english', $1)) AS score
            FROM conversation_messages
            WHERE content_tsv @@ plainto_tsquery('english', $1){extra_where}
            ORDER BY score DESC
            LIMIT ${result_limit_idx}
        """

        rows = await pool.fetch(
            sql,
            query,
            *extra_args,
            limit * 5,
        )

    candidates = []
    for row in rows:
        r = dict(row)
        r["id"] = str(r["id"])
        r["conversation_id"] = str(r["conversation_id"])
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
        candidates.append(r)

    # F-10: Apply recency bias to RRF scores
    recency_decay_days = int(get_tuning(state["config"], "recency_decay_days", 90))
    recency_max_penalty = float(get_tuning(state["config"], "recency_max_penalty", 0.2))

    if candidates and recency_decay_days > 0 and recency_max_penalty > 0:
        now = datetime.now(timezone.utc)
        for c in candidates:
            created_str = c.get("created_at", "")
            if created_str:
                try:
                    created = datetime.fromisoformat(created_str)
                    # Clamp to 0 to guard against clock skew inflating scores
                    age_days = max(0, (now - created).total_seconds() / 86400)
                    # Linear penalty: 0 for new messages, up to recency_max_penalty for old
                    penalty = min(
                        recency_max_penalty,
                        (age_days / recency_decay_days) * recency_max_penalty,
                    )
                    c["score"] = c.get("score", 0) * (1.0 - penalty)
                except (ValueError, TypeError):
                    pass
        # Re-sort by adjusted score
        candidates.sort(key=lambda x: x.get("score", 0), reverse=True)

    return {"candidates": candidates}


async def rerank_results(state: MessageSearchState) -> dict:
    """Apply reranking to the hybrid search candidates.

    Uses the configured reranker provider. Falls back to RRF scores if
    reranker is unavailable (graceful degradation).
    """
    candidates = state["candidates"]
    query = state["query"]
    limit = state["limit"]
    config = state["config"]

    reranker_config = config.get("reranker", {})
    reranker_provider = reranker_config.get("provider", "none")

    if reranker_provider == "none" or not candidates:
        return {"reranked_results": candidates[:limit]}

    if reranker_provider == "api":
        try:
            reranked = await _rerank_via_api(query, candidates, config)
            return {"reranked_results": reranked[:limit]}
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            _log.warning("API reranking failed (degraded mode): %s", exc)
            return {"reranked_results": candidates[:limit]}

    # Unknown provider — return top candidates by RRF score
    _log.warning("Unknown reranker provider %r, skipping reranking", reranker_provider)
    return {"reranked_results": candidates[:limit]}


def route_after_embed_message(state: MessageSearchState) -> str:
    """Route: always proceed to hybrid search (embedding failure is non-fatal)."""
    return "hybrid_search_messages"


def build_message_search_flow() -> StateGraph:
    """Build and compile the message search StateGraph."""
    workflow = StateGraph(MessageSearchState)

    workflow.add_node("embed_message_query", embed_message_query)
    workflow.add_node("hybrid_search_messages", hybrid_search_messages)
    workflow.add_node("rerank_results", rerank_results)

    workflow.set_entry_point("embed_message_query")
    workflow.add_edge("embed_message_query", "hybrid_search_messages")
    workflow.add_edge("hybrid_search_messages", "rerank_results")
    workflow.add_edge("rerank_results", END)

    return workflow.compile()
