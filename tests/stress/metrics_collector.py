"""Collect compaction metrics from the Context Broker.

Queries the database directly (via SSH + docker exec + psql) and the
/metrics Prometheus endpoint to track compaction progress.
"""

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field

import httpx

_log = logging.getLogger("stress.metrics")


@dataclass
class CompactionState:
    """Snapshot of compaction state for a context window."""

    window_id: str
    compaction_cycles: int = 0  # Deactivated tier 3 (archival) records = completed cycles
    active_tier3_count: int = 0
    active_tier2_count: int = 0
    tier3_tokens: int = 0
    tier2_tokens: int = 0
    total_messages: int = 0
    total_summaries: int = 0
    last_assembled_at: str = ""
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """Collects compaction and performance metrics."""

    def __init__(
        self,
        base_url: str,
        ssh_target: str,
        remote_project_dir: str,
        compose_project: str = "claude-test",
        compose_file: str = "docker-compose.claude-test.yml",
    ):
        self.base_url = base_url
        self.ssh_target = ssh_target
        self.remote_project_dir = remote_project_dir
        self.compose_project = compose_project
        self.compose_file = compose_file
        self._client = httpx.Client(timeout=30.0)

    def _psql(self, query: str) -> str:
        """Run a psql query against the test Postgres container."""
        cmd = (
            f"cd {self.remote_project_dir} && "
            f"docker compose -p {self.compose_project} -f {self.compose_file} "
            f'exec -T context-broker-postgres '
            f'psql -U context_broker -d context_broker -t -A -c "{query}"'
        )
        if self.ssh_target:
            args = ["ssh", self.ssh_target, cmd]
        else:
            args = ["bash", "-c", cmd]
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        return result.stdout.strip()

    def get_compaction_state(self, window_id: str) -> CompactionState:
        """Query current compaction state for a context window."""
        state = CompactionState(window_id=window_id)

        # Count completed compaction cycles (deactivated tier 3 archival records)
        raw = self._psql(
            f"SELECT COUNT(*) FROM conversation_summaries "
            f"WHERE context_window_id = '{window_id}' "
            f"AND tier = 3 AND is_active = FALSE"
        )
        state.compaction_cycles = int(raw) if raw.isdigit() else 0

        # Active tier counts and tokens
        raw = self._psql(
            f"SELECT tier, COUNT(*), COALESCE(SUM(summary_token_count), 0) "
            f"FROM conversation_summaries "
            f"WHERE context_window_id = '{window_id}' AND is_active = TRUE "
            f"GROUP BY tier ORDER BY tier"
        )
        for line in raw.splitlines():
            parts = line.split("|")
            if len(parts) >= 3:
                tier = int(parts[0])
                count = int(parts[1])
                tokens = int(parts[2])
                if tier == 3:
                    state.active_tier3_count = count
                    state.tier3_tokens = tokens
                elif tier == 2:
                    state.active_tier2_count = count
                    state.tier2_tokens = tokens

        # Total summaries
        raw = self._psql(
            f"SELECT COUNT(*) FROM conversation_summaries "
            f"WHERE context_window_id = '{window_id}'"
        )
        state.total_summaries = int(raw) if raw.isdigit() else 0

        # Last assembled
        raw = self._psql(
            f"SELECT last_assembled_at FROM context_windows "
            f"WHERE id = '{window_id}'"
        )
        state.last_assembled_at = raw

        return state

    def get_conversation_token_count(self, conversation_id: str) -> int:
        """Get estimated token count for a conversation."""
        raw = self._psql(
            f"SELECT estimated_token_count FROM conversations "
            f"WHERE id = '{conversation_id}'"
        )
        return int(raw) if raw.isdigit() else 0

    def get_conversation_message_count(self, conversation_id: str) -> int:
        """Get total message count for a conversation."""
        raw = self._psql(
            f"SELECT total_messages FROM conversations "
            f"WHERE id = '{conversation_id}'"
        )
        return int(raw) if raw.isdigit() else 0

    def get_prometheus_metrics(self) -> dict:
        """Fetch and parse Prometheus metrics."""
        try:
            resp = self._client.get(f"{self.base_url}/metrics", timeout=10)
            text = resp.text
            metrics = {}
            for line in text.splitlines():
                if line.startswith("#"):
                    continue
                match = re.match(r'^(\S+?)(?:\{[^}]*\})?\s+([\d.eE+-]+)', line)
                if match:
                    metrics[match.group(1)] = float(match.group(2))
            return metrics
        except (httpx.HTTPError, ValueError) as exc:
            _log.warning("Failed to fetch metrics: %s", exc)
            return {}

    def get_container_rss_mb(self) -> float:
        """Get langgraph container RSS in MB (memory leak detection)."""
        raw = self._psql(
            # This isn't psql, but we reuse the SSH runner
            ""
        )
        # Use docker stats instead
        cmd = (
            f"docker stats --no-stream --format '{{{{.MemUsage}}}}' "
            f"{self.compose_project}-context-broker-langgraph-1 2>/dev/null || "
            f"docker stats --no-stream --format '{{{{.MemUsage}}}}' "
            f"context-broker-langgraph 2>/dev/null"
        )
        if self.ssh_target:
            args = ["ssh", self.ssh_target, cmd]
        else:
            args = ["bash", "-c", cmd]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=10)
            # Format: "123.4MiB / 16GiB"
            mem_str = result.stdout.strip().split("/")[0].strip()
            if "GiB" in mem_str:
                return float(mem_str.replace("GiB", "")) * 1024
            elif "MiB" in mem_str:
                return float(mem_str.replace("MiB", ""))
            return 0.0
        except Exception:
            return 0.0

    def close(self):
        self._client.close()
