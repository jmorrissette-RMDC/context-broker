"""
Deadband tier budget allocation utilities.

Replaces the old dynamic tier scaling (short/medium/long conversation
scaling) with a deadband model where:
  - Tier 1 (live) fills between a floor and a ceiling
  - Tier 2 (chunks) has min/max chunk count constraints
  - Tier 3 (archival) has a small fixed percentage
  - Compaction triggers when tier 1 exceeds its ceiling
"""


def extract_deadband_config(build_type_config: dict) -> dict:
    """Extract deadband compaction parameters from build type config."""
    return {
        "tier1_floor_pct": build_type_config.get("tier1_floor_pct", 0.20),
        "tier2_chunk_pct": build_type_config.get("tier2_chunk_pct", 0.02),
        "tier2_min_chunks": build_type_config.get("tier2_min_chunks", 3),
        "tier2_max_chunks": build_type_config.get("tier2_max_chunks", 6),
        "tier3_pct": build_type_config.get("tier3_pct", 0.02),
        "tier3_header_pct": build_type_config.get("tier3_header_pct", 0.0025),
        "effective_utilization": build_type_config.get("effective_utilization", 0.85),
    }


def calculate_tier1_ceiling(max_budget: int, db_config: dict) -> int:
    """Calculate the tier 1 (live) ceiling in tokens.

    Ceiling = (max_budget * effective_utilization) - tier2_max - tier3.
    This is the maximum tier 1 can hold before compaction triggers.
    Never drops below the floor.
    """
    util = db_config["effective_utilization"]
    tier3_tokens = int(max_budget * db_config["tier3_pct"])
    tier2_max_tokens = int(
        max_budget * db_config["tier2_chunk_pct"] * db_config["tier2_max_chunks"]
    )
    ceiling = int(max_budget * util) - tier2_max_tokens - tier3_tokens
    floor = int(max_budget * db_config["tier1_floor_pct"])
    return max(ceiling, floor)


def should_trigger_compaction(
    tier1_tokens: int,
    max_budget: int,
    db_config: dict,
) -> bool:
    """Check if tier 1 has exceeded its ceiling and compaction should trigger."""
    ceiling = calculate_tier1_ceiling(max_budget, db_config)
    return tier1_tokens > ceiling
