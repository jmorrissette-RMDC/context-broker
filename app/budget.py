"""
Token budget bucket management.

Snaps requested budgets to standard buckets for efficient window sharing.
Multiple agents with similar budgets share pre-built summaries instead of
each triggering separate assembly work.
"""

# Standard buckets — doubling progression with 200K for common model sizes
BUDGET_BUCKETS = [
    4096,  # 4K
    8192,  # 8K
    16384,  # 16K
    32768,  # 32K
    65536,  # 64K
    131072,  # 128K
    204800,  # 200K (Anthropic Sonnet/Opus pre-1M)
    262144,  # 256K
    524288,  # 512K
    1048576,  # 1M
    2097152,  # 2M
]

# Default effective utilization — models degrade past ~85% context fill.
# Build types apply this to calculate tier allocations.
EFFECTIVE_UTILIZATION_DEFAULT = 0.85


def snap_budget(requested: int) -> int:
    """Snap a requested token budget to the nearest bucket (always up).

    Returns the smallest bucket that is >= the requested value.
    If the requested value exceeds all buckets, returns the largest bucket.
    """
    for bucket in BUDGET_BUCKETS:
        if bucket >= requested:
            return bucket
    return BUDGET_BUCKETS[-1]
