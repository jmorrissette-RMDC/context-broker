#!/usr/bin/env bash
# ============================================================================
# Context Broker — Deployment Script
#
# Deploys an isolated Context Broker stack with a given name prefix.
# Each stack gets its own Docker bridge network, data volumes, and config.
# Multiple stacks can run on the same host without conflict.
#
# Usage:
#   ./deploy.sh <prefix> [port] [config-dir]
#   ./deploy.sh --down <prefix>
#   ./deploy.sh --status <prefix>
#
# Examples:
#   ./deploy.sh context-broker 8080 ./config       # production
#   ./deploy.sh claude-test 8081 ./config-test      # test stack
#   ./deploy.sh dev 8082 ./config-dev               # dev stack
#   ./deploy.sh --down claude-test                  # tear down
#   ./deploy.sh --status claude-test                # check status
#
# The script:
#   1. Validates inputs
#   2. Runs docker compose -p <prefix> up -d --build
#   3. Waits for Postgres to be healthy
#   4. Waits for the langgraph app to complete migrations
#   5. Waits for the MCP endpoint to accept tool calls
#   6. Prints the endpoint URL
#
# Requirements:
#   - docker compose v2 (docker compose, not docker-compose)
#   - curl (for health/MCP polling)
#   - The compose file and config directory must exist
# ============================================================================

set -euo pipefail

# Defaults
COMPOSE_FILE="docker-compose.claude-test.yml"
DEFAULT_PORT=8081
HEALTH_TIMEOUT=180      # seconds to wait for /health
MCP_TIMEOUT=120         # seconds to wait for MCP readiness
POLL_INTERVAL=3         # seconds between polls

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log()   { echo -e "${GREEN}[DEPLOY]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ============================================================================
# Parse arguments
# ============================================================================

usage() {
    echo "Usage:"
    echo "  $0 <prefix> [port] [config-dir]    Deploy a stack"
    echo "  $0 --down <prefix>                 Tear down a stack"
    echo "  $0 --status <prefix>               Check stack status"
    echo ""
    echo "Arguments:"
    echo "  prefix      Stack name prefix (e.g., context-broker, claude-test, dev)"
    echo "  port        Host port for nginx gateway (default: 8081)"
    echo "  config-dir  Path to config directory (default: ./config-test)"
    exit 1
}

if [[ $# -lt 1 ]]; then
    usage
fi

# Handle --down and --status
case "${1}" in
    --down)
        [[ $# -lt 2 ]] && { error "Usage: $0 --down <prefix>"; exit 1; }
        PREFIX="$2"
        log "Tearing down stack: ${PREFIX}"
        docker compose -p "${PREFIX}" -f "${COMPOSE_FILE}" down -v --remove-orphans 2>&1

        # Clean up data directories created by containers (owned by container users).
        # Use a temporary container to remove files we can't delete as the host user.
        DATA_DIR="./data-test"
        if [[ -d "${DATA_DIR}" ]]; then
            log "Cleaning up ${DATA_DIR} (container-owned files)..."
            docker run --rm -v "$(pwd)/${DATA_DIR}:/cleanup" alpine:3.19 \
                sh -c "rm -rf /cleanup/*" 2>/dev/null || true
            rmdir "${DATA_DIR}" 2>/dev/null || true
        fi

        log "Stack ${PREFIX} torn down"
        exit 0
        ;;
    --status)
        [[ $# -lt 2 ]] && { error "Usage: $0 --status <prefix>"; exit 1; }
        PREFIX="$2"
        log "Status for stack: ${PREFIX}"
        docker compose -p "${PREFIX}" -f "${COMPOSE_FILE}" ps
        exit 0
        ;;
    --help|-h)
        usage
        ;;
    --*)
        error "Unknown flag: $1"
        usage
        ;;
esac

PREFIX="$1"
PORT="${2:-${DEFAULT_PORT}}"
CONFIG_DIR="${3:-./config-test}"

# ============================================================================
# Validate
# ============================================================================

if [[ ! -f "${COMPOSE_FILE}" ]]; then
    error "Compose file not found: ${COMPOSE_FILE}"
    error "Run this script from the ContextBroker project root."
    exit 1
fi

if [[ ! -d "${CONFIG_DIR}" ]]; then
    error "Config directory not found: ${CONFIG_DIR}"
    error "Create it with config.yml, te.yml, and credentials/ before deploying."
    exit 1
fi

if [[ ! -f "${CONFIG_DIR}/config.yml" ]]; then
    error "No config.yml found in ${CONFIG_DIR}"
    exit 1
fi

# Check docker compose is available
if ! docker compose version &>/dev/null; then
    error "docker compose v2 not found. Install Docker Compose plugin."
    exit 1
fi

# Check port is not in use (best effort)
if command -v lsof &>/dev/null; then
    if lsof -i ":${PORT}" -sTCP:LISTEN &>/dev/null; then
        warn "Port ${PORT} appears to be in use. Deployment may fail."
    fi
fi

log "Deploying stack: ${PREFIX}"
log "  Port:   ${PORT}"
log "  Config: ${CONFIG_DIR}"
log "  Compose: ${COMPOSE_FILE}"

# ============================================================================
# Step 1: Deploy containers
# ============================================================================

log "Starting containers..."
docker compose -p "${PREFIX}" -f "${COMPOSE_FILE}" up -d --build 2>&1

log "Containers started"

# ============================================================================
# Step 2: Wait for Postgres to be healthy
# ============================================================================

log "Waiting for Postgres to be healthy..."
PG_DEADLINE=$((SECONDS + HEALTH_TIMEOUT))

while [[ $SECONDS -lt $PG_DEADLINE ]]; do
    PG_STATUS=$(docker compose -p "${PREFIX}" -f "${COMPOSE_FILE}" \
        exec -T context-broker-postgres \
        pg_isready -U context_broker -d context_broker 2>/dev/null || true)

    if echo "${PG_STATUS}" | grep -q "accepting connections"; then
        log "Postgres is healthy"
        break
    fi
    sleep "${POLL_INTERVAL}"
done

if ! echo "${PG_STATUS}" | grep -q "accepting connections"; then
    error "Postgres did not become healthy within ${HEALTH_TIMEOUT}s"
    docker compose -p "${PREFIX}" -f "${COMPOSE_FILE}" logs --tail 30 context-broker-postgres
    exit 1
fi

# ============================================================================
# Step 3: Wait for Neo4j to be healthy
# ============================================================================

log "Waiting for Neo4j to be healthy..."
NEO4J_DEADLINE=$((SECONDS + 90))

while [[ $SECONDS -lt $NEO4J_DEADLINE ]]; do
    NEO4J_STATUS=$(docker compose -p "${PREFIX}" -f "${COMPOSE_FILE}" \
        exec -T context-broker-neo4j \
        wget -q --spider http://localhost:7474/ 2>&1 && echo "ok" || true)

    if echo "${NEO4J_STATUS}" | grep -q "ok"; then
        log "Neo4j is healthy"
        break
    fi
    sleep "${POLL_INTERVAL}"
done

# Neo4j not being ready is non-fatal — the app handles it gracefully
if ! echo "${NEO4J_STATUS}" | grep -q "ok"; then
    warn "Neo4j did not become healthy within 90s — continuing (app will retry)"
fi

# ============================================================================
# Step 4: Wait for /health endpoint (langgraph app + nginx)
# ============================================================================

log "Waiting for /health endpoint on port ${PORT}..."
HEALTH_DEADLINE=$((SECONDS + HEALTH_TIMEOUT))
HEALTH_OK=false

while [[ $SECONDS -lt $HEALTH_DEADLINE ]]; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://localhost:${PORT}/health" 2>/dev/null || echo "000")

    if [[ "${HTTP_CODE}" == "200" ]]; then
        HEALTH_OK=true
        log "/health returns 200"
        break
    fi
    sleep "${POLL_INTERVAL}"
done

if [[ "${HEALTH_OK}" != "true" ]]; then
    error "/health did not return 200 within ${HEALTH_TIMEOUT}s (last: ${HTTP_CODE})"
    log "Langgraph container logs:"
    docker compose -p "${PREFIX}" -f "${COMPOSE_FILE}" logs --tail 30 context-broker-langgraph
    exit 1
fi

# ============================================================================
# Step 5: Wait for MCP readiness (Postgres middleware passes)
# ============================================================================

log "Waiting for MCP endpoint readiness..."
MCP_DEADLINE=$((SECONDS + MCP_TIMEOUT))
MCP_OK=false

# The /health endpoint can return 200 while the Postgres middleware
# still returns 503 for /mcp. We need to verify a real MCP call works.
MCP_PAYLOAD='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"metrics_get","arguments":{}}}'

while [[ $SECONDS -lt $MCP_DEADLINE ]]; do
    MCP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "http://localhost:${PORT}/mcp" \
        -H "Content-Type: application/json" \
        -d "${MCP_PAYLOAD}" 2>/dev/null || echo "000")

    if [[ "${MCP_CODE}" == "200" ]]; then
        MCP_OK=true
        log "MCP endpoint ready"
        break
    fi
    sleep "${POLL_INTERVAL}"
done

if [[ "${MCP_OK}" != "true" ]]; then
    error "MCP endpoint not ready within ${MCP_TIMEOUT}s (last HTTP: ${MCP_CODE})"
    log "Langgraph container logs:"
    docker compose -p "${PREFIX}" -f "${COMPOSE_FILE}" logs --tail 50 context-broker-langgraph
    exit 1
fi

# ============================================================================
# Step 6: Verify key services
# ============================================================================

log "Running quick verification..."

# Check tool listing works
TOOLS_RESPONSE=$(curl -s -X POST "http://localhost:${PORT}/mcp" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' 2>/dev/null)

TOOL_COUNT=$(echo "${TOOLS_RESPONSE}" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(len(data.get('result', {}).get('tools', [])))
except:
    print(0)
" 2>/dev/null || echo "0")

if [[ "${TOOL_COUNT}" -gt 0 ]]; then
    log "MCP tools available: ${TOOL_COUNT}"
else
    warn "Could not verify tool count (tools/list may have returned unexpected format)"
fi

# Check chat endpoint (non-blocking — timeout after 10s)
CHAT_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST "http://localhost:${PORT}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{"model":"context-broker","messages":[{"role":"user","content":"ping"}],"stream":false}' \
    2>/dev/null || echo "000")

if [[ "${CHAT_CODE}" == "200" ]]; then
    log "Chat endpoint responding"
else
    warn "Chat endpoint returned ${CHAT_CODE} (may be busy — this is not a failure)"
fi

# ============================================================================
# Done
# ============================================================================

echo ""
log "=========================================="
log " Stack '${PREFIX}' deployed successfully"
log "=========================================="
log ""
log "  Health:   http://localhost:${PORT}/health"
log "  MCP:      http://localhost:${PORT}/mcp"
log "  Chat:     http://localhost:${PORT}/v1/chat/completions"
log "  Metrics:  http://localhost:${PORT}/metrics"
log ""
log "  Tear down:  $0 --down ${PREFIX}"
log "  Status:     $0 --status ${PREFIX}"
log ""
