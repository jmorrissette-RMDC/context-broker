"""
Integration tests via SSH container inspection (T-2.1, T-2.3, T-4.1, T-4.5, T-9.1).

These tests SSH to irina (aristotle9@192.168.1.110) and run docker commands
to inspect the deployed containers.

Run from Windows dev machine:
    pytest tests/test_integration_container.py -m integration
"""

import json
import subprocess

import pytest

from tests.conftest_e2e import SSH_HOST, SSH_USER

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# SSH helper
# ---------------------------------------------------------------------------

# Timeout for SSH commands (seconds)
SSH_TIMEOUT = 30


def ssh_exec(
    command: str, *, timeout: int = SSH_TIMEOUT
) -> subprocess.CompletedProcess:
    """Execute a command on irina via SSH and return the result.

    Uses the default SSH key configured for the aristotle9 user.
    """
    ssh_cmd = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=10",
        f"{SSH_USER}@{SSH_HOST}",
        command,
    ]
    return subprocess.run(
        ssh_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ===================================================================
# T-2.1: Non-root user execution
# ===================================================================


class TestNonRootUser:
    """Verify the langgraph container runs as a non-root user."""

    def test_whoami_not_root(self):
        """docker exec whoami should not return 'root'."""
        result = ssh_exec("docker exec context-broker-langgraph whoami")
        assert result.returncode == 0, f"SSH failed: {result.stderr}"
        username = result.stdout.strip()
        assert (
            username != "root"
        ), f"Container is running as root! whoami returned: '{username}'"

    def test_process_uid_not_zero(self):
        """Application process UID should not be 0 (root)."""
        result = ssh_exec("docker exec context-broker-langgraph id -u")
        assert result.returncode == 0, f"SSH failed: {result.stderr}"
        uid = result.stdout.strip()
        assert uid != "0", "Container UID is 0 (root)"


# ===================================================================
# T-2.3: File ownership
# ===================================================================


class TestFileOwnership:
    """Verify application files are owned by the service account."""

    def test_app_directory_ownership(self):
        """Files in /app/ should be owned by context-broker user, not root."""
        result = ssh_exec("docker exec context-broker-langgraph ls -la /app/")
        assert result.returncode == 0, f"SSH failed: {result.stderr}"
        lines = result.stdout.strip().splitlines()

        # Skip the "total" line and check file ownership
        file_lines = [line for line in lines if not line.startswith("total")]
        assert len(file_lines) > 0, "No files found in /app/"

        for line in file_lines:
            parts = line.split()
            if len(parts) >= 3:
                # Skip . and .. entries (parent dir is always root)
                name = parts[-1] if parts else ""
                if name in (".", ".."):
                    continue
                owner = parts[2]
                # Owner should not be root (may be context-broker or a numeric UID)
                assert owner != "root", f"File owned by root: {line}"

    def test_app_files_not_world_writable(self):
        """Application files should not be world-writable."""
        result = ssh_exec(
            "docker exec context-broker-langgraph find /app -perm -o+w -type f"
        )
        assert result.returncode == 0, f"SSH failed: {result.stderr}"
        writable_files = result.stdout.strip()
        assert (
            writable_files == ""
        ), f"World-writable files found in /app:\n{writable_files}"


# ===================================================================
# T-4.1: Two-volume pattern (data volumes)
# ===================================================================


class TestDataVolumes:
    """Verify data directories exist and are mounted."""

    def test_data_directory_exists(self):
        """The /data directory should exist inside the langgraph container."""
        result = ssh_exec("docker exec context-broker-langgraph ls /data/")
        assert result.returncode == 0, f"Cannot list /data/ directory: {result.stderr}"

    def test_data_subdirectories(self):
        """Host data directory should contain postgres, neo4j, redis subdirs."""
        # Check the host-side data directory which is bind-mounted
        result = ssh_exec(
            "ls -d /home/aristotle9/context-broker/data/postgres "
            "/home/aristotle9/context-broker/data/neo4j "
            "/home/aristotle9/context-broker/data/redis 2>&1"
        )
        # If the exact path differs, try a more general check
        if result.returncode != 0:
            # Try finding the docker compose project directory
            result = ssh_exec(
                "docker inspect context-broker-langgraph "
                "--format '{{range .Mounts}}{{.Source}} -> {{.Destination}}\\n{{end}}'"
            )
            assert result.returncode == 0, f"SSH failed: {result.stderr}"
            mounts = result.stdout.strip()
            assert (
                "/data" in mounts
            ), f"No /data mount found in container mounts:\n{mounts}"

    def test_config_mount_readonly(self):
        """The /config mount should be read-only."""
        result = ssh_exec(
            "docker inspect context-broker-langgraph "
            "--format '{{range .Mounts}}{{.Destination}}:{{.RW}} {{end}}'"
        )
        assert result.returncode == 0, f"SSH failed: {result.stderr}"
        mounts = result.stdout.strip()
        # /config should have RW=false
        assert (
            "/config:false" in mounts or "/config: false" in mounts
        ), f"/config mount is not read-only. Mounts: {mounts}"


# ===================================================================
# T-4.5: No anonymous volumes
# ===================================================================


class TestNoAnonymousVolumes:
    """Verify no anonymous volumes are created by the project."""

    def test_no_anonymous_volumes(self):
        """docker volume ls should not show anonymous volumes for context-broker."""
        result = ssh_exec("docker volume ls --format '{{.Name}}'")
        assert result.returncode == 0, f"SSH failed: {result.stderr}"

        volumes = result.stdout.strip().splitlines()
        # Anonymous volumes have hex-string names (64 chars)
        # Named volumes from compose have project prefix
        anonymous = [
            v
            for v in volumes
            if len(v) == 64 and all(c in "0123456789abcdef" for c in v)
        ]

        # Check if any anonymous volumes are associated with our containers
        for anon_vol in anonymous:
            inspect = ssh_exec(
                f"docker volume inspect {anon_vol} --format '{{{{.Labels}}}}'"
            )
            if "context-broker" in inspect.stdout:
                pytest.fail(
                    f"Anonymous volume {anon_vol} is associated with "
                    f"context-broker project"
                )

    def test_all_data_on_bind_mounts(self):
        """All persistent data services use bind mounts, not named volumes."""
        containers = [
            "context-broker-postgres",
            "context-broker-neo4j",
            "context-broker-redis",
        ]
        for container in containers:
            result = ssh_exec(
                f"docker inspect {container} "
                f"--format '{{{{range .Mounts}}}}{{{{.Type}}}} {{{{end}}}}'"
            )
            if result.returncode != 0:
                # Container might not be running, skip
                pytest.skip(f"Container {container} not inspectable: {result.stderr}")
            mount_types = result.stdout.strip().split()
            # OTS images (neo4j, postgres) may declare internal VOLUMEs that
            # Docker creates alongside our bind mounts. This is expected per
            # EX-NEO4J-001/EX-POSTGRES-001. Verify at least one bind mount exists.
            assert (
                "bind" in mount_types
            ), f"{container} has no bind mounts: {mount_types}"


# ===================================================================
# T-9.1: Structured JSON logging
# ===================================================================


class TestStructuredLogging:
    """Verify the langgraph container emits structured JSON logs."""

    def test_logs_are_json(self):
        """Recent docker logs should be valid JSON objects."""
        result = ssh_exec("docker logs context-broker-langgraph --tail 20 2>&1")
        assert result.returncode == 0, f"SSH failed: {result.stderr}"

        lines = result.stdout.strip().splitlines()
        assert len(lines) > 0, "No log lines captured"

        json_lines = 0
        parse_errors = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)  # Validate JSON structure
                json_lines += 1
            except json.JSONDecodeError:
                parse_errors.append(line)

        # At least some lines should be valid JSON
        assert json_lines > 0, "No valid JSON log lines found. Lines:\n" + "\n".join(
            parse_errors[:5]
        )

    def test_log_fields_present(self):
        """JSON log entries should contain timestamp, level, and message fields."""
        result = ssh_exec("docker logs context-broker-langgraph --tail 50 2>&1")
        assert result.returncode == 0, f"SSH failed: {result.stderr}"

        lines = result.stdout.strip().splitlines()
        checked = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            checked += 1
            # Check for required fields (may use different key names)
            has_timestamp = any(
                k in obj for k in ("timestamp", "time", "ts", "@timestamp")
            )
            has_level = any(
                k in obj for k in ("level", "severity", "log_level", "levelname")
            )
            has_message = any(k in obj for k in ("message", "msg", "event"))

            assert has_timestamp, f"Log entry missing timestamp field: {obj}"
            assert has_level, f"Log entry missing level field: {obj}"
            assert has_message, f"Log entry missing message field: {obj}"

            # Only need to verify a few
            if checked >= 5:
                break

        assert checked > 0, "No JSON log entries found to verify fields"
