"""System tools — infrastructure inspection and utilities.

Always available to the Imperator.
"""

import logging
import math

from langchain_core.tools import tool

_log = logging.getLogger("context_broker.tools.system")

# Allowlisted commands — read-only system inspection only
_ALLOWED_COMMANDS = {
    "docker ps",
    "docker stats --no-stream",
    "df -h",
    "uptime",
    "free -h",
    "cat /proc/loadavg",
    "hostname",
    "whoami",
    "id",
    "env",
    "pip list",
    "python --version",
}

# Commands that are allowed with any arguments
_ALLOWED_PREFIXES = [
    "ping -c ",
    "docker logs ",
    "docker inspect ",
    "curl -s ",
    "dig ",
    "nslookup ",
]


def _is_command_allowed(cmd: str) -> bool:
    """Check if a command is in the allowlist."""
    cmd_stripped = cmd.strip()
    if cmd_stripped in _ALLOWED_COMMANDS:
        return True
    return any(cmd_stripped.startswith(prefix) for prefix in _ALLOWED_PREFIXES)


@tool
async def run_command(command: str) -> str:
    """Execute an allowlisted shell command for infrastructure inspection.

    Only read-only commands are permitted: docker ps, df, uptime, ping, etc.
    No write operations, no arbitrary execution.

    Args:
        command: Shell command to execute (must be in the allowlist).
    """
    if not _is_command_allowed(command):
        allowed = "\n".join(
            [f"  {c}" for c in sorted(_ALLOWED_COMMANDS)]
            + [f"  {p}..." for p in _ALLOWED_PREFIXES]
        )
        return f"Command not allowed. Permitted commands:\n{allowed}"

    try:
        import asyncio

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace")
        errors = stderr.decode("utf-8", errors="replace")
        result = output[:5000]
        if errors:
            result += f"\n--- stderr ---\n{errors[:1000]}"
        if not result.strip():
            result = "(no output)"
        return result
    except asyncio.TimeoutError:
        return "Command timed out after 30 seconds."
    except (OSError, RuntimeError) as exc:
        return f"Command error: {exc}"


@tool
async def calculate(expression: str) -> str:
    """Evaluate a mathematical expression safely.

    Supports basic arithmetic, powers, sqrt, log, abs, round, min, max.
    Does NOT execute arbitrary Python — only math operations.

    Args:
        expression: Math expression (e.g., "1024 * 85 / 100", "sqrt(144)").
    """
    # Safe math namespace — no builtins, no imports
    safe_names = {
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sqrt": math.sqrt,
        "log": math.log,
        "log2": math.log2,
        "log10": math.log10,
        "ceil": math.ceil,
        "floor": math.floor,
        "pi": math.pi,
        "e": math.e,
        "pow": pow,
        "int": int,
        "float": float,
    }
    try:
        # Reject anything that looks like code injection
        if any(kw in expression for kw in ["import", "__", "exec", "eval", "open"]):
            return "Expression rejected — contains unsafe keywords."
        result = eval(expression, {"__builtins__": {}}, safe_names)  # noqa: S307
        return str(result)
    except (SyntaxError, NameError, TypeError, ValueError, ZeroDivisionError) as exc:
        return f"Calculation error: {exc}"


def get_tools() -> list:
    """Return all system tools."""
    return [run_command, calculate]
