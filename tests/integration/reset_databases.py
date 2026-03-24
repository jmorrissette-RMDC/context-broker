"""
Reset databases for integration testing.

Clears all conversation data, embeddings, summaries, knowledge graph,
and queue state. Run before each integration test to ensure clean state.

Usage:
    python tests/integration/reset_databases.py
"""

import logging
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# SSH target — databases are on the internal Docker network,
# not exposed to the host. All resets run via SSH + docker exec.
SSH_TARGET = "aristotle9@192.168.1.110"
CB_DIR = "/mnt/storage/projects/portfolio/ContextBroker"


def ssh_cmd(cmd: str) -> str:
    """Run a command on irina via SSH and return output."""
    result = subprocess.run(
        ["ssh", SSH_TARGET, cmd],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 and result.stderr.strip():
        log.warning(f"  stderr: {result.stderr.strip()}")
    return result.stdout.strip()


def reset_postgres():
    """Truncate all conversation/memory tables via docker exec psql."""
    tables = [
        "conversation_messages",
        "conversation_summaries",
        "context_windows",
        "conversations",
        "mem0_memories",
        "system_logs",
        "stategraph_packages",
    ]
    for table in tables:
        output = ssh_cmd(
            f'docker exec context-broker-postgres psql -U context_broker -d context_broker '
            f'-t -c "SELECT COUNT(*) FROM {table}" 2>/dev/null || echo "0"'
        )
        count = output.strip()
        ssh_cmd(
            f'docker exec context-broker-postgres psql -U context_broker -d context_broker '
            f'-c "TRUNCATE {table} CASCADE" 2>/dev/null || true'
        )
        log.info(f"  Truncated {table} ({count} rows)")


def reset_redis():
    """Flush all Redis queues via docker exec."""
    ssh_cmd("docker exec context-broker-redis redis-cli FLUSHDB")
    log.info("  Redis flushed")


def reset_neo4j():
    """Clear Neo4j knowledge graph via docker exec cypher-shell."""
    output = ssh_cmd(
        'docker exec context-broker-neo4j cypher-shell --non-interactive '
        '"MATCH (n) DETACH DELETE n" 2>/dev/null || echo "neo4j reset skipped"'
    )
    log.info(f"  Neo4j: {output or 'cleared'}")


def reset_imperator_state():
    """Remove the Imperator's persistent state file so it creates a new conversation."""
    ssh_cmd(
        "rm -f /mnt/storage/projects/portfolio/ContextBroker/data/imperator_state.json"
    )
    log.info("  Imperator state file removed")


def main():
    log.info("=== Resetting databases for integration test ===")

    log.info("Postgres:")
    reset_postgres()

    log.info("Redis:")
    reset_redis()

    log.info("Neo4j:")
    reset_neo4j()

    log.info("Imperator state:")
    reset_imperator_state()

    log.info("\nIMPORTANT: Restart the langgraph container for Imperator to create a new conversation:")
    log.info("  docker compose restart context-broker-langgraph")

    log.info("=== Reset complete ===")


if __name__ == "__main__":
    main()
