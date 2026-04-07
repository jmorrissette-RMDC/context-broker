"""Unit tests for token budget bucket snapping (D-04)."""

from app.budget import BUDGET_BUCKETS, snap_budget


class TestSnapBudget:
    """Verify budget snapping to buckets."""

    def test_exact_match(self):
        """Exact bucket value returns itself."""
        assert snap_budget(8192) == 8192
        assert snap_budget(204800) == 204800

    def test_snap_up(self):
        """Values between buckets snap to the next higher bucket."""
        assert snap_budget(5000) == 8192
        assert snap_budget(8193) == 16384
        assert snap_budget(100000) == 131072
        assert snap_budget(130000) == 131072

    def test_snap_200k(self):
        """The 200K bucket catches common model sizes."""
        assert snap_budget(150000) == 204800
        assert snap_budget(200000) == 204800
        assert snap_budget(204801) == 262144

    def test_minimum(self):
        """Small values snap to the smallest bucket."""
        assert snap_budget(1) == 4096
        assert snap_budget(1000) == 4096
        assert snap_budget(4096) == 4096

    def test_exceeds_max(self):
        """Values exceeding the largest bucket return the largest bucket."""
        assert snap_budget(3000000) == 2097152
        assert snap_budget(999999999) == 2097152

    def test_buckets_are_sorted(self):
        """Bucket list is sorted ascending."""
        for i in range(len(BUDGET_BUCKETS) - 1):
            assert BUDGET_BUCKETS[i] < BUDGET_BUCKETS[i + 1]
