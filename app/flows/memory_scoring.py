"""
Memory confidence scoring with half-life decay (M-22).

Applied at retrieval time to weight memories by freshness and category.
Memories that haven't been accessed or reinforced decay over time.

Since Mem0 manages its own storage (Neo4j + pgvector), we cannot modify
its internal scoring. This module applies decay at the retrieval layer,
after Mem0 returns results but before they are used downstream.
"""

import math
from datetime import datetime, timezone

# Half-life in days by category
DEFAULT_HALF_LIVES = {
    "ephemeral": 3,  # Short-lived facts (moods, preferences, temp state)
    "contextual": 14,  # Session/project context
    "factual": 60,  # Learned facts about entities
    "historical": 365,  # Historical events, permanent-ish
    "default": 30,  # When category unknown
}


def score_memory(memory: dict, config: dict) -> float:
    """Score a memory based on age and category using half-life decay.

    Returns a score between 0.0 and 1.0.
    """
    half_lives = config.get("tuning", {}).get("memory_half_lives", DEFAULT_HALF_LIVES)

    category = memory.get("category", "default")
    # R5-M20: Clamp to minimum 1 to prevent ZeroDivisionError from bad config
    half_life_days = max(1, half_lives.get(category, half_lives.get("default", 30)))

    # Calculate age in days
    created = memory.get("created_at")
    if isinstance(created, str):
        created = datetime.fromisoformat(created)
    if created is None:
        return 0.5  # Unknown age, neutral score

    now = datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_days = max(0, (now - created).total_seconds() / 86400)

    # Exponential decay: score = 0.5 ^ (age / half_life)
    score = math.pow(0.5, age_days / half_life_days)

    # Boost if recently accessed
    last_accessed = memory.get("last_accessed")
    if last_accessed:
        if isinstance(last_accessed, str):
            last_accessed = datetime.fromisoformat(last_accessed)
        if last_accessed.tzinfo is None:
            last_accessed = last_accessed.replace(tzinfo=timezone.utc)
        access_age = max(0, (now - last_accessed).total_seconds() / 86400)
        if access_age < 7:
            score = min(1.0, score * 1.3)  # 30% boost for recently accessed

    return score


def filter_and_rank_memories(
    memories: list[dict], config: dict, min_score: float = 0.1
) -> list[dict]:
    """Score, filter, and rank memories by confidence.

    Memories below min_score are filtered out.
    Remaining memories are sorted by score descending.
    """
    scored = []
    for mem in memories:
        score = score_memory(mem, config)
        if score >= min_score:
            # R5-m13: Create a copy to avoid mutating the input list's dicts
            scored_mem = {**mem, "confidence_score": score}
            scored.append(scored_mem)

    scored.sort(key=lambda m: m.get("confidence_score", 0), reverse=True)
    return scored
