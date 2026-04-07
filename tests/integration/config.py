"""
Integration test configuration.

Shared settings for all integration test scripts.
"""

from pathlib import Path

# Target Context Broker instance
CB_BASE_URL = "http://192.168.1.110:8080"
CB_MCP_URL = f"{CB_BASE_URL}/mcp"
CB_CHAT_URL = f"{CB_BASE_URL}/v1/chat/completions"
CB_HEALTH_URL = f"{CB_BASE_URL}/health"

# Test dataset location
TEST_DATA_DIR = Path(r"Z:\test-data\conversational-memory")
PHASE1_DIR = TEST_DATA_DIR / "phase1-bulk-load"
PHASE2_DIR = TEST_DATA_DIR / "phase2-agent-conversation"
PHASE3_DIR = TEST_DATA_DIR / "phase3-synthetic"

# Timeouts
PIPELINE_TIMEOUT_SECONDS = 3600  # 60 min hard timeout for bulk pipeline
PIPELINE_POLL_INTERVAL_SECONDS = 30  # Poll every 30s during pipeline wait
PIPELINE_STALL_SECONDS = 120  # Fail if no progress for 2 min
EARLY_VALIDATION_MESSAGES = 100  # Check pipeline after this many messages
MCP_CALL_TIMEOUT_SECONDS = 30  # Timeout per MCP tool call
CHAT_CALL_TIMEOUT_SECONDS = 60  # Timeout per Imperator chat turn

# Sonnet CLI for quality evaluation
SONNET_MODEL = "sonnet"  # Claude Code CLI model flag

# Report output
REPORT_DIR = TEST_DATA_DIR / "reports"
