"""
Dynamic tier scaling (F-05).

Adjusts tier percentages based on conversation length. Short conversations
use more tier 3 (verbatim) budget. Long conversations shift budget toward
tier 1/tier 2 (compressed) layers to fit more history.

The config values are starting points; this function adjusts them.
"""

import logging

_log = logging.getLogger("context_broker.flows.build_types.tier_scaling")

# Thresholds for conversation length categories
_SHORT_CONVERSATION = 50
_LONG_CONVERSATION = 500


def scale_tier_percentages(
    build_type_config: dict,
    message_count: int,
) -> dict:
    """Return a copy of build_type_config with tier percentages adjusted for message count.

    F-05: Dynamic tier scaling.

    - Short conversations (< 50 messages): boost tier3 by shifting from tier1/tier2.
      Rationale: there's little to summarize, so maximize verbatim budget.
    - Medium conversations (50-500 messages): use config as-is.
    - Long conversations (> 500 messages): boost tier1/tier2 by shifting from tier3.
      Rationale: more history to compress, summaries are more valuable.

    The adjustment is a simple linear interpolation within each range.
    Non-tier percentage keys (knowledge_graph_pct, semantic_retrieval_pct) are
    left unchanged — only tier1/2/3 are rebalanced.

    Args:
        build_type_config: The build type config dict (must contain tier*_pct keys).
        message_count: Total number of messages in the conversation.

    Returns:
        A new dict with adjusted tier percentages.
    """
    config = dict(build_type_config)

    tier1_pct = config.get("tier1_pct", 0)
    tier2_pct = config.get("tier2_pct", 0)
    tier3_pct = config.get("tier3_pct", 0)

    # Only adjust if all three tiers are present
    if not (tier1_pct or tier2_pct or tier3_pct):
        return config

    tier_total = tier1_pct + tier2_pct + tier3_pct

    if message_count < _SHORT_CONVERSATION:
        # Short conversations: boost tier3 at the expense of tier1/tier2.
        # At 0 messages, shift 80% of tier1+tier2 budget to tier3.
        # Linear ramp from 80% shift at 0 messages to 0% shift at 50 messages.
        ratio = 1.0 - (message_count / _SHORT_CONVERSATION)
        shift_factor = 0.8 * ratio  # max 80% of tier1+tier2 shifts to tier3

        shift_amount = (tier1_pct + tier2_pct) * shift_factor
        # Distribute the reduction proportionally between tier1 and tier2
        if tier1_pct + tier2_pct > 0:
            t1_share = tier1_pct / (tier1_pct + tier2_pct)
            t2_share = tier2_pct / (tier1_pct + tier2_pct)
        else:
            t1_share = 0.5
            t2_share = 0.5

        config["tier1_pct"] = round(tier1_pct - shift_amount * t1_share, 4)
        config["tier2_pct"] = round(tier2_pct - shift_amount * t2_share, 4)
        config["tier3_pct"] = round(tier3_pct + shift_amount, 4)

        _log.debug(
            "F-05: Short conversation (%d msgs) — tier scaling: t1=%.2f%% t2=%.2f%% t3=%.2f%%",
            message_count,
            config["tier1_pct"] * 100,
            config["tier2_pct"] * 100,
            config["tier3_pct"] * 100,
        )

    elif message_count > _LONG_CONVERSATION:
        # Long conversations: boost tier1/tier2 at the expense of tier3.
        # Linear ramp from 0% shift at 500 messages to 30% shift at 2000 messages.
        excess = min(message_count - _LONG_CONVERSATION, 1500)
        ratio = excess / 1500
        shift_factor = 0.3 * ratio  # max 30% of tier3 shifts to tier1+tier2

        shift_amount = tier3_pct * shift_factor
        # Distribute the gain: 40% to tier1, 60% to tier2
        config["tier1_pct"] = round(tier1_pct + shift_amount * 0.4, 4)
        config["tier2_pct"] = round(tier2_pct + shift_amount * 0.6, 4)
        config["tier3_pct"] = round(tier3_pct - shift_amount, 4)

        _log.debug(
            "F-05: Long conversation (%d msgs) — tier scaling: t1=%.2f%% t2=%.2f%% t3=%.2f%%",
            message_count,
            config["tier1_pct"] * 100,
            config["tier2_pct"] * 100,
            config["tier3_pct"] * 100,
        )

    # Ensure no negative values (defensive)
    for key in ("tier1_pct", "tier2_pct", "tier3_pct"):
        if config.get(key, 0) < 0:
            config[key] = 0.0

    # R5-m7: Renormalize tier percentages so they sum to exactly the original
    # tier_total, avoiding floating-point drift after adjustments.
    new_tier_sum = config["tier1_pct"] + config["tier2_pct"] + config["tier3_pct"]
    if new_tier_sum > 0 and abs(new_tier_sum - tier_total) > 1e-9:
        factor = tier_total / new_tier_sum
        config["tier1_pct"] = round(config["tier1_pct"] * factor, 6)
        config["tier2_pct"] = round(config["tier2_pct"] * factor, 6)
        config["tier3_pct"] = round(config["tier3_pct"] * factor, 6)

    return config
