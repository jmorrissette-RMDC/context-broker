"""
Phase 4b: Performance analysis via logs and Sonnet CLI.

Queries pipeline logs, collects metrics, and asks Sonnet CLI to
identify bottlenecks.

Usage:
    python tests/integration/analyze_performance.py
"""

import json
import subprocess

import httpx

from config import CB_HEALTH_URL, SONNET_MODEL, TEST_DATA_DIR

SSH_TARGET = "aristotle9@192.168.1.110"


def sonnet_evaluate(prompt: str) -> str:
    """Call Claude Sonnet via CLI for analysis."""
    result = subprocess.run(
        ["claude", "--model", SONNET_MODEL, "--print", "-p", prompt],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return f"Sonnet CLI error: {result.stderr[:200]}"
    return result.stdout.strip()


def collect_pipeline_logs() -> str:
    """Get pipeline-related logs from the container."""
    result = subprocess.run(
        [
            "ssh",
            SSH_TARGET,
            "docker logs context-broker-langgraph 2>&1 | grep -E 'duration_ms|Batch embedding|Assembly|Extraction|embed_message|assembly_job' | tail -50",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout


def collect_metrics() -> str:
    """Scrape Prometheus metrics endpoint."""
    with httpx.Client() as client:
        resp = client.get(CB_HEALTH_URL.replace("/health", "/metrics"), timeout=10)
        return resp.text


def main():
    print("=== Performance Analysis ===\n")

    # Collect data
    print("Collecting pipeline logs...")
    logs = collect_pipeline_logs()
    print(f"  {len(logs.splitlines())} log lines")

    print("Collecting metrics...")
    metrics = collect_metrics()
    metrics_lines = [
        line for line in metrics.splitlines() if line.startswith("context_broker_")
    ]
    print(f"  {len(metrics_lines)} metric lines")

    # DB stats
    print("Collecting DB stats...")
    db_stats = subprocess.run(
        [
            "ssh",
            SSH_TARGET,
            "docker exec context-broker-postgres psql -U context_broker -d context_broker -t -c "
            "\"SELECT 'messages', COUNT(*) FROM conversation_messages "
            "UNION ALL SELECT 'embedded', COUNT(*) FROM conversation_messages WHERE embedding IS NOT NULL "
            "UNION ALL SELECT 'summaries', COUNT(*) FROM conversation_summaries "
            "UNION ALL SELECT 'windows', COUNT(*) FROM context_windows\"",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout
    print(f"  DB: {db_stats.strip()}")

    # Ask Sonnet to analyze
    print("\nAsking Sonnet CLI for analysis...")
    analysis_prompt = f"""You are analyzing the performance of a Context Broker pipeline.

PIPELINE LOGS (last 50 entries):
{logs}

PROMETHEUS METRICS (context_broker_* only):
{chr(10).join(metrics_lines[:30])}

DATABASE STATS:
{db_stats}

Analyze:
1. What is the average embedding batch processing time?
2. What is the average assembly duration per conversation?
3. What is the average extraction duration?
4. Are there any bottlenecks, errors, or anomalies?
5. What is the end-to-end throughput (messages per second through the full pipeline)?

Provide a concise performance report with specific numbers."""

    analysis = sonnet_evaluate(analysis_prompt)
    print(f"\n{analysis}")

    # Save
    report = {
        "pipeline_logs_count": len(logs.splitlines()),
        "metrics_count": len(metrics_lines),
        "db_stats": db_stats.strip(),
        "analysis": analysis,
    }
    report_path = TEST_DATA_DIR / "performance-report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport saved to {report_path}")


if __name__ == "__main__":
    main()
