"""Phase Z: Performance report — runs last, scrapes Prometheus and generates report.

This test phase scrapes /metrics at the end of the test session and generates
a human-readable performance report at tests/claude/PERFORMANCE.md.

Named 'z' so it runs after all other phases alphabetically.
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.claude.live.helpers import (
    docker_psql,
    get_db_counts,
    log_issue,
)

pytestmark = pytest.mark.live

REPORT_PATH = Path(__file__).resolve().parent.parent / "PERFORMANCE.md"


# ---------------------------------------------------------------------------
# Prometheus parsing
# ---------------------------------------------------------------------------

def _parse_prometheus(text: str) -> dict:
    """Parse Prometheus text exposition format into a structured dict."""
    metrics = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Parse: metric_name{labels} value
        match = re.match(r'^(\w+)(\{[^}]*\})?\s+([\d.eE+\-]+|NaN|Inf|\+Inf)$', line)
        if not match:
            continue
        name = match.group(1)
        labels_str = match.group(2) or ""
        value = float(match.group(3)) if match.group(3) not in ("NaN", "Inf", "+Inf") else float("inf")

        # Parse labels
        labels = {}
        if labels_str:
            for lm in re.finditer(r'(\w+)="([^"]*)"', labels_str):
                labels[lm.group(1)] = lm.group(2)

        key = (name, tuple(sorted(labels.items())))
        metrics[key] = value

    return metrics


def _get_histogram_stats(metrics: dict, metric_name: str, label_filter: dict = None) -> dict:
    """Extract histogram stats (count, sum, avg, percentile estimates) from parsed metrics."""
    # Find all bucket entries for this metric
    buckets = []
    count = 0
    total = 0

    for (name, labels_tuple), value in metrics.items():
        labels = dict(labels_tuple)
        if label_filter:
            if not all(labels.get(k) == v for k, v in label_filter.items()):
                continue

        if name == f"{metric_name}_count":
            count = value
        elif name == f"{metric_name}_sum":
            total = value
        elif name == f"{metric_name}_bucket":
            le = labels.get("le", "+Inf")
            if le != "+Inf":
                buckets.append((float(le), value))

    buckets.sort(key=lambda x: x[0])
    avg = total / count if count > 0 else 0

    # Estimate percentiles from buckets
    def _estimate_percentile(pct):
        target = count * pct
        for bound, cumulative in buckets:
            if cumulative >= target:
                return bound
        return float("inf")

    return {
        "count": int(count),
        "sum": round(total, 2),
        "avg": round(avg, 3),
        "p50": _estimate_percentile(0.5),
        "p90": _estimate_percentile(0.9),
        "p99": _estimate_percentile(0.99),
    }


def _get_counter(metrics: dict, metric_name: str, label_filter: dict = None) -> float:
    """Get a counter value from parsed metrics."""
    for (name, labels_tuple), value in metrics.items():
        if name != metric_name:
            continue
        labels = dict(labels_tuple)
        if label_filter:
            if not all(labels.get(k) == v for k, v in label_filter.items()):
                continue
        return value
    return 0


def _get_gauge(metrics: dict, metric_name: str) -> float:
    """Get a gauge value from parsed metrics."""
    for (name, _), value in metrics.items():
        if name == metric_name:
            return value
    return -1


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

class TestPerformanceReport:
    """Generate a performance report from Prometheus metrics."""

    def test_generate_performance_report(self, http_client):
        """Scrape /metrics and generate PERFORMANCE.md."""
        resp = http_client.get("/metrics", timeout=10)
        assert resp.status_code == 200, f"/metrics returned {resp.status_code}"

        metrics = _parse_prometheus(resp.text)
        db_counts = get_db_counts()

        # -- MCP Tool Latencies --
        tool_names = set()
        for (name, labels_tuple), _ in metrics.items():
            labels = dict(labels_tuple)
            if name == "context_broker_mcp_request_duration_seconds_count" and "tool" in labels:
                tool_names.add(labels["tool"])

        tool_stats = {}
        for tool in sorted(tool_names):
            stats = _get_histogram_stats(
                metrics,
                "context_broker_mcp_request_duration_seconds",
                {"tool": tool},
            )
            success = _get_counter(
                metrics, "context_broker_mcp_requests_total",
                {"tool": tool, "status": "success"},
            )
            errors = _get_counter(
                metrics, "context_broker_mcp_requests_total",
                {"tool": tool, "status": "error"},
            )
            if stats["count"] > 0:
                tool_stats[tool] = {**stats, "success": int(success), "errors": int(errors)}

        # -- Chat Latencies --
        chat_stats = _get_histogram_stats(metrics, "context_broker_chat_request_duration_seconds")

        # -- Background Job Stats --
        job_types = ["embed_batch", "extract_memory", "assemble_context"]
        job_stats = {}
        for jt in job_types:
            stats = _get_histogram_stats(
                metrics, "context_broker_job_duration_seconds", {"job_type": jt},
            )
            success = _get_counter(
                metrics, "context_broker_jobs_completed_total",
                {"job_type": "embed_message" if jt == "embed_batch" else jt, "status": "success"},
            )
            errors = _get_counter(
                metrics, "context_broker_jobs_completed_total",
                {"job_type": "embed_message" if jt == "embed_batch" else jt, "status": "error"},
            )
            job_stats[jt] = {**stats, "success": int(success), "errors": int(errors)}

        # -- Assembly by Build Type --
        build_types = ["sliding-window", "tiered-summary", "enriched"]
        assembly_stats = {}
        for bt in build_types:
            stats = _get_histogram_stats(
                metrics, "context_broker_context_assembly_duration_seconds",
                {"build_type": bt},
            )
            if stats["count"] > 0:
                assembly_stats[bt] = stats

        # -- Queue Depths --
        queue_depths = {
            "embedding": _get_gauge(metrics, "context_broker_embedding_queue_depth"),
            "assembly": _get_gauge(metrics, "context_broker_assembly_queue_depth"),
            "extraction": _get_gauge(metrics, "context_broker_extraction_queue_depth"),
        }

        # -- Generate Report --
        lines = [
            "# Performance Report",
            "",
            f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
            f"**Source:** Prometheus /metrics endpoint",
            "",
            "---",
            "",
            "## Database State",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total messages | {db_counts['total']:,} |",
            f"| Embedded messages | {db_counts['embedded']:,} |",
            f"| Summaries | {db_counts['summaries']:,} |",
            f"| Extracted messages | {db_counts['extracted']:,} |",
            "",
            "## Queue Depths (current)",
            "",
            f"| Queue | Depth |",
            f"|-------|-------|",
        ]
        for q, depth in queue_depths.items():
            status = "empty" if depth == 0 else f"**{int(depth):,}**" if depth > 0 else "unknown"
            lines.append(f"| {q} | {status} |")

        lines.extend([
            "",
            "## MCP Tool Latencies",
            "",
            "| Tool | Calls | Errors | Avg (s) | p50 (s) | p90 (s) | p99 (s) | Total (s) |",
            "|------|-------|--------|---------|---------|---------|---------|-----------|",
        ])
        for tool, stats in sorted(tool_stats.items(), key=lambda x: -x[1]["sum"]):
            lines.append(
                f"| {tool} | {stats['count']:,} | {stats['errors']} | "
                f"{stats['avg']:.3f} | {stats['p50']:.2f} | {stats['p90']:.2f} | "
                f"{stats['p99']:.2f} | {stats['sum']:.1f} |"
            )

        lines.extend([
            "",
            "## Chat (Imperator) Latencies",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total calls | {chat_stats['count']} |",
            f"| Total time | {chat_stats['sum']:.1f}s |",
            f"| Average | {chat_stats['avg']:.1f}s |",
            f"| p50 | {chat_stats['p50']:.1f}s |",
            f"| p90 | {chat_stats['p90']:.1f}s |",
            f"| p99 | {chat_stats['p99']:.1f}s |",
            "",
            "## Background Job Performance",
            "",
            "| Job Type | Completed | Errors | Avg (s) | p50 (s) | p90 (s) | Total (s) |",
            "|----------|-----------|--------|---------|---------|---------|-----------|",
        ])
        for jt, stats in job_stats.items():
            lines.append(
                f"| {jt} | {stats['success']:,} | {stats['errors']} | "
                f"{stats['avg']:.2f} | {stats['p50']:.1f} | {stats['p90']:.1f} | "
                f"{stats['sum']:.1f} |"
            )

        if assembly_stats:
            lines.extend([
                "",
                "## Context Assembly by Build Type",
                "",
                "| Build Type | Assemblies | Avg (s) | p50 (s) | p90 (s) | Total (s) |",
                "|------------|-----------|---------|---------|---------|-----------|",
            ])
            for bt, stats in assembly_stats.items():
                lines.append(
                    f"| {bt} | {stats['count']} | {stats['avg']:.3f} | "
                    f"{stats['p50']:.1f} | {stats['p90']:.1f} | {stats['sum']:.1f} |"
                )

        # -- Key Findings --
        lines.extend([
            "",
            "## Key Findings",
            "",
        ])

        # Flag slow tools
        for tool, stats in tool_stats.items():
            if stats["avg"] > 5.0:
                lines.append(f"- **SLOW**: `{tool}` averages {stats['avg']:.1f}s per call")
                log_issue(
                    "performance_report",
                    "warning",
                    "performance",
                    f"{tool} averages {stats['avg']:.1f}s per call",
                    "<5s",
                    f"{stats['avg']:.1f}s",
                )

        # Flag high error rates
        for jt, stats in job_stats.items():
            total = stats["success"] + stats["errors"]
            if total > 0 and stats["errors"] / total > 0.1:
                pct = stats["errors"] / total * 100
                lines.append(f"- **HIGH ERROR RATE**: `{jt}` has {pct:.0f}% error rate ({stats['errors']}/{total})")
                log_issue(
                    "performance_report",
                    "warning",
                    "performance",
                    f"{jt} has {pct:.0f}% error rate",
                    "<10%",
                    f"{pct:.0f}%",
                )

        # Flag non-empty queues
        for q, depth in queue_depths.items():
            if depth > 0:
                lines.append(f"- **QUEUE NOT DRAINED**: `{q}` queue has {int(depth):,} pending items")

        # Chat latency
        if chat_stats["avg"] > 10:
            lines.append(f"- **SLOW CHAT**: Imperator averages {chat_stats['avg']:.1f}s per response")

        if not any(line.startswith("- ") for line in lines[lines.index("## Key Findings") + 2:]):
            lines.append("- No performance issues detected")

        lines.append("")

        report = "\n".join(lines)
        REPORT_PATH.write_text(report, encoding="utf-8")

        # Test passes — report is informational
        assert REPORT_PATH.exists(), "Performance report was not generated"
