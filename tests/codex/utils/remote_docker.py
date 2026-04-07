import os
import subprocess


def _ssh_host() -> str:
    return os.environ.get("CB_DOCKER_SSH", "aristotle9@192.168.1.110")


def run_ssh(remote_cmd: str, timeout: float = 60.0) -> str:
    result = subprocess.run(
        ["ssh", _ssh_host(), remote_cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise AssertionError(
            "SSH command failed:\n"
            f"Command: {remote_cmd}\n"
            f"Exit: {result.returncode}\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}"
        )
    return result.stdout.strip()


def docker_exec(container: str, command: str, timeout: float = 60.0) -> str:
    remote_cmd = f"docker exec {container} {command}"
    return run_ssh(remote_cmd, timeout=timeout)


def psql_query(query: str, timeout: float = 60.0) -> str:
    """Run a psql query against the context broker database.

    Tries direct subprocess psql first (works inside container or when
    psql is available locally). Falls back to SSH + docker exec.
    """
    import shutil

    # Try direct psql first (works inside container on the same Docker network)
    if shutil.which("psql"):
        try:
            result = subprocess.run(
                [
                    "psql", "-h", "context-broker-postgres",
                    "-U", "context_broker", "-d", "context_broker",
                    "-t", "-A", "-c", query,
                ],
                capture_output=True, text=True, timeout=timeout,
                env={**os.environ, "PGPASSWORD": os.environ.get("POSTGRES_PASSWORD", "contextbroker123")},
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Fall back to SSH + docker exec
    escaped = query.replace('"', '\\"')
    remote_cmd = (
        "docker exec context-broker-postgres "
        "psql -U context_broker -d context_broker -t -A "
        f"-c \"{escaped}\""
    )
    return run_ssh(remote_cmd, timeout=timeout)
