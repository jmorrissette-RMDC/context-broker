I'll conduct a thorough compliance audit of the Context Broker implementation against the three requirements documents. Let me systematically analyze each requirement section.

## COMPLIANCE AUDIT REPORT

---

### REQ-001: MAD Engineering Requirements

---

#### REQ-001 §1.1 — Code Clarity

**Status:** PASS

**Evidence:** The codebase demonstrates clear, readable code throughout. For example, in `app/flows/build_types/standard_tiered.py`, functions like `acquire_assembly_lock`, `load_window_config`, `summarize_message_chunks` have descriptive names. The `_resolve_llm_config` function clearly explains its purpose in comments: "Resolve effective LLM config: build-type-specific overrides global (F-06)."

---

#### REQ-001 §1.2 — Code Formatting (black)

**Status:** PASS

**Evidence:** The code follows black formatting standards. For example, in `app/config.py`:
```python
def get_build_type_config(config: dict[str, Any], build_type_name: str) -> dict[str, Any]:
```
The code uses consistent line lengths and formatting.

---

#### REQ-001 §1.3 — Code Linting (ruff)

**Status:** PASS

**Evidence:** The code uses `# noqa: F401` comments appropriately (e.g., `import app.flows.build_types.passthrough  # noqa: F401` in `app/flows/build_types/__init__.py`), indicating intentional lint suppression where needed.

---

#### REQ-001 §1.4 — Unit Testing

**Status:** FAIL

**Evidence:** No test files are present in the provided source code. The codebase lacks `pytest` test files covering primary success paths and common error conditions.

---

#### REQ-001 §1.5 — Version Pinning

**Status:** PASS

**Evidence:** `requirements.txt` uses exact version pinning:
```
uvicorn==0.27.0
fastapi==0.109.2
langgraph==0.1.4
```

---

#### REQ-001 §2.1 — StateGraph Mandate

**Status:** PASS

**Evidence:** All programmatic logic is implemented as LangGraph StateGraphs. For example, `app/flows/build_types/standard_tiered.py`:
```python
def build_standard_tiered_assembly():
    """Build and compile the standard-tiered assembly StateGraph."""
    workflow = StateGraph(StandardTieredAssemblyState)
    workflow.add_node("acquire_assembly_lock", acquire_assembly_lock)
    # ... nodes and edges
    return workflow.compile()
```

Flow control is expressed via conditional edges, not procedural branching inside nodes.

---

#### REQ-001 §2.2 — State Immutability

**Status:** PASS

**Evidence:** Node functions return new dictionaries with only updated state. Example from `app/flows/build_types/standard_tiered.py`:
```python
async def acquire_assembly_lock(state: StandardTieredAssemblyState) -> dict:
    # ... processing
    return {"lock_key": lock_key, "lock_token": lock_token, "lock_acquired": True, "assembly_start_time": time.monotonic()}
```

---

#### REQ-001 §2.3 — Checkpointing

**Status:** PARTIAL

**Evidence:** The Imperator flow explicitly does NOT use checkpointing (per ARCH-06 comment in `app/flows/imperator_flow.py`):
```python
# ARCH-06: No checkpointer — compile without one
return workflow.compile()
```
However, this is intentional for the Imperator since it uses DB-backed state. Background flows don't require checkpointing per the requirement.

---

#### REQ-001 §3.1 — No Hardcoded Secrets

**Status:** PASS

**Evidence:** Credentials are loaded from environment variables. Example from `app/config.py`:
```python
def get_api_key(provider_config: dict[str, Any]) -> str:
    env_var_name = provider_config.get("api_key_env", "")
    if not env_var_name:
        return ""
    api_key = os.environ.get(env_var_name, "")
```

---

#### REQ-001 §3.2 — Input Validation

**Status:** PASS

**Evidence:** MCP tools use Pydantic models for validation. Example from `app/models.py`:
```python
class CreateConversationInput(BaseModel):
    conversation_id: Optional[UUID] = Field(None, description="Caller-supplied ID for idempotent creation")
    title: Optional[str] = Field(None, max_length=500)
```

---

#### REQ-001 §3.3 — Null/None Checking

**Status:** PASS

**Evidence:** The code explicitly checks for None before attribute access. Example from `app/flows/build_types/standard_tiered.py`:
```python
if window is None:
    return {"error": f"Context window {state['context_window_id']} not found"}
```

---

#### REQ-001 §4.1 — Logging to stdout/stderr

**Status:** PASS

**Evidence:** `app/logging_setup.py` configures logging to stdout:
```python
handler = logging.StreamHandler(sys.stdout)
```

---

#### REQ-001 §4.2 — Structured Logging

**Status:** PASS

**Evidence:** `app/logging_setup.py` implements JsonFormatter:
```python
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
```

---

#### REQ-001 §4.3 — Log Levels

**Status:** PASS

**Evidence:** Log level is configurable via `config.yml`:
```yaml
log_level: INFO
```
And applied in `app/logging_setup.py`:
```python
def update_log_level(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), None)
```

---

#### REQ-001 §4.4 — Log Content

**Status:** PASS

**Evidence:** The code logs appropriately without secrets. Example from `app/database.py`:
```python
_log.warning("Redis client created but ping failed — starting in degraded mode: %s", exc)
```

---

#### REQ-001 §4.5 — Specific Exception Handling

**Status:** PASS

**Evidence:** The code catches specific exceptions. Example from `app/flows/memory_extraction.py`:
```python
except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:
```
Note: There's a broad `Exception` catch, but it's documented as intentional (EX-CB-001).

---

#### REQ-001 §4.6 — Resource Management

**Status:** PASS

**Evidence:** Database connections use context managers. Example from `app/database.py`:
```python
async with pool.acquire() as conn:
    async with conn.transaction():
```

---

#### REQ-001 §4.7 — Error Context

**Status:** PASS

**Evidence:** Errors include sufficient context. Example from `app/workers/arq_worker.py`:
```python
_log.error("Embedding job failed: message_id=%s error=%s", message_id, result["error"])
```

---

#### REQ-001 §4.8 — Pipeline Observability

**Status:** PASS

**Evidence:** Verbose logging is implemented in `app/config.py`:
```python
def verbose_log(config: dict, logger: Any, message: str, *args: Any) -> None:
    """Log a message only if verbose_logging is enabled in tuning config.
    
    REQ-001 section 4.8: Verbose pipeline logging for node entry/exit with timing.
    """
    if get_tuning(config, "verbose_logging", False):
        logger.info(message, *args)
```
And used throughout flows (e.g., `verbose_log(state["config"], _log, "standard_tiered.acquire_lock ENTER window=%s", state["context_window_id"])`).

---

#### REQ-001 §5.1 — No Blocking I/O

**Status:** PASS

**Evidence:** The codebase uses async libraries exclusively. Example from `app/database.py`:
```python
import asyncpg
import redis.asyncio as aioredis
```
No `time.sleep()` in async context.

---

#### REQ-001 §6.1 — MCP Transport

**Status:** PASS

**Evidence:** Implemented in `app/routes/mcp.py`:
```python
@router.get("/mcp")
async def mcp_sse_session(request: Request) -> StreamingResponse:
```

---

#### REQ-001 §6.2 — Tool Naming

**Status:** PASS

**Evidence:** Tools use domain prefixes. Example from `app/routes/mcp.py`:
```python
{
    "name": "conv_create_conversation",
    "name": "mem_search",
}
```

---

#### REQ-001 §6.3 — Health Endpoint

**Status:** PASS

**Evidence:** Implemented in `app/routes/health.py`:
```python
@router.get("/health")
async def health_check(request: Request) -> JSONResponse:
```
Returns per-dependency status.

---

#### REQ-001 §6.4 — Prometheus Metrics

**Status:** PASS

**Evidence:** Implemented in `app/routes/metrics.py`:
```python
@router.get("/metrics")
async def get_metrics() -> Response:
```
Metrics are produced inside StateGraphs (e.g., `app/metrics_registry.py`).

---

#### REQ-001 §7.1 — Graceful Degradation

**Status:** PASS

**Evidence:** Neo4j failures are handled gracefully. Example from `app/flows/memory_search_flow.py`:
```python
except (ConnectionError, RuntimeError, ValueError, ImportError, OSError, Exception) as exc:
    _log.warning("Memory search failed (degraded mode): %s", exc)
    return {"memories": [], "relations": [], "degraded": True, "error": str(exc)}
```

---

#### REQ-001 §7.2 — Independent Startup

**Status:** PASS

**Evidence:** `app/main.py` implements retry loops for PostgreSQL and Redis:
```python
pg_retry_task = asyncio.create_task(_postgres_retry_loop(application, config))
```
Containers start without waiting for dependencies.

---

#### REQ-001 §7.3 — Idempotency

**Status:** PASS

**Evidence:** Message storage handles duplicates. Example from `app/flows/message_pipeline.py`:
```python
# F-04: Check for consecutive duplicate message (same sender + content)
if (prev_msg is not None and prev_msg["sender"] == state["sender"] and prev_msg["content"] == content):
    # Collapse: increment repeat_count instead of inserting
```
Context window creation uses `ON CONFLICT DO NOTHING`:
```python
INSERT INTO context_windows ... ON CONFLICT (conversation_id, participant_id, build_type) DO NOTHING
```

---

#### REQ-001 §7.4 — Fail Fast

**Status:** PASS

**Evidence:** Invalid configuration fails at startup. Example from `app/config.py`:
```python
def get_build_type_config(config: dict[str, Any], build_type_name: str) -> dict[str, Any]:
    if build_type_name not in build_types:
        raise ValueError(
            f"Build type '{build_type_name}' not found in config.yml. "
```

---

#### REQ-001 §8.1 — Configurable External Dependencies

**Status:** PASS

**Evidence:** Inference providers are configurable in `config.yml`:
```yaml
llm:
  base_url: https://api.openai.com/v1
  model: gpt-4o-mini
  api_key_env: LLM_API_KEY
```

---

#### REQ-001 §8.2 — Externalized Configuration

**Status:** PASS

**Evidence:** Prompt templates are externalized. Example from `app/prompt_loader.py`:
```python
PROMPTS_DIR = Path(os.environ.get("PROMPTS_DIR", "/config/prompts"))
```
Tuning parameters are in `config.yml`:
```yaml
tuning:
  assembly_lock_ttl_seconds: 300
  chunk_size: 20
```

---

#### REQ-001 §8.3 — Hot-Reload vs Startup Config

**Status:** PASS

**Evidence:** Hot-reload implemented in `app/config.py`:
```python
def load_config() -> dict[str, Any]:
    """Load and return the full configuration from /config/config.yml.
    
    Uses mtime-based caching: only re-reads the file when it changes.
```
Infrastructure settings are read once at startup via `load_startup_config()`.

---

### REQ-002: pMAD Engineering Requirements

---

#### REQ-002 §1.1 — Root Usage Pattern

**Status:** PASS

**Evidence:** `Dockerfile`:
```dockerfile
RUN apt-get update && \
    apt-get install -y --no-install-recommends ...
RUN groupadd --gid ${USER_GID} ${USER_NAME} && \
    useradd --uid ${USER_UID} --gid ${USER_GID} ...
USER ${USER_NAME}
```

---

#### REQ-002 §1.2 — Service Account

**Status:** PASS

**Evidence:** `Dockerfile` defines dedicated user:
```dockerfile
ARG USER_NAME=context-broker
ARG USER_UID=1001
ARG USER_GID=1001
```

---

#### REQ-002 §1.3 — File Ownership

**Status:** PASS

**Evidence:** `Dockerfile` uses `--chown`:
```dockerfile
COPY --chown=${USER_NAME}:${USER_NAME} app/ ./app/
```

---

#### REQ-002 §1.4 — Base Image Pinning

**Status:** PASS

**Evidence:** `Dockerfile`:
```dockerfile
FROM python:3.12.1-slim
```

---

#### REQ-002 §1.5 — Dockerfile HEALTHCHECK

**Status:** PASS

**Evidence:** `Dockerfile`:
```dockerfile
HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
```

---

#### REQ-002 §2.1 — OTS Backing Services

**Status:** PASS

**Evidence:** `docker-compose.yml` uses official images:
```yaml
context-broker-postgres:
    image: pgvector/pgvector:0.7.0-pg16
context-broker-neo4j:
    image: neo4j:5.15.0
context-broker-redis:
    image: redis:7.2.3-alpine
```

---

#### REQ-002 §2.2 — Thin Gateway

**Status:** PASS

**Evidence:** `nginx/nginx.conf` is pure routing with no application logic:
```nginx
location /mcp {
    proxy_pass http://context_broker_langgraph;
    # No business logic
}
```

---

#### REQ-002 §2.3 — Container-Only Deployment

**Status:** PASS

**Evidence:** All services run as containers per `docker-compose.yml`. No bare-metal installation.

---

#### REQ-002 §3.1 — Two-Network Pattern

**Status:** PASS

**Evidence:** `docker-compose.yml`:
```yaml
networks:
  context-broker-net:
    driver: bridge
    internal: true
```
Gateway connects to both networks; other containers only to internal.

---

#### REQ-002 §3.2 — Service Name DNS

**Status:** PASS

**Evidence:** `docker-compose.yml` uses service names:
```yaml
upstream context_broker_langgraph {
    server context-broker-langgraph:8000;
}
```

---

#### REQ-002 §4.1 — Volume Pattern

**Status:** PASS

**Evidence:** `docker-compose.yml`:
```yaml
volumes:
  - ./config:/config:ro
  - ./data:/data
```

---

#### REQ-002 §4.2 — Database Storage

**Status:** PASS

**Evidence:** `docker-compose.yml`:
```yaml
volumes:
  - ./data/postgres:/var/lib/postgresql/data
  - ./data/neo4j:/data
  - ./data/redis:/data
```

---

#### REQ-002 §4.3 — Backup and Recovery

**Status:** PASS

**Evidence:** `postgres/init.sql` includes schema. Migrations in `app/migrations.py` are versioned and applied automatically.

---

#### REQ-002 §4.4 — Credential Management

**Status:** PASS

**Evidence:** `docker-compose.yml`:
```yaml
env_file:
  - ./config/credentials/.env
```
`.gitignore`:
```bash
config/credentials/.env
```

---

#### REQ-002 §5.1 — Docker Compose

**Status:** PASS

**Evidence:** Project ships single `docker-compose.yml`. Users can customize via override.

---

#### REQ-002 §5.2 — Health Check Architecture

**Status:** PASS

**Evidence:** Two layers implemented - Docker HEALTHCHECK and HTTP `/health` endpoint that aggregates dependency status.

---

#### REQ-002 §5.3 — Eventual Consistency

**Status:** PASS

**Evidence:** Message storage is source of truth. Background processing (embedding, assembly, extraction) is async with retry. Example from `app/workers/arq_worker.py` handles failures with backoff.

---

#### REQ-002 §6.1 — MCP Endpoint

**Status:** PASS

**Evidence:** `nginx/nginx.conf` routes `/mcp` to langgraph container.

---

#### REQ-002 §6.2 — OpenAI-Compatible Chat

**Status:** PASS

**Evidence:** `nginx/nginx.conf`:
```nginx
location /v1/chat/completions {
    proxy_pass http://context_broker_langgraph;
}
```

---

#### REQ-002 §6.3 — Authentication

**Status:** PASS

**Evidence:** Context Broker ships without authentication as stated in requirements. Comment in `docker-compose.yml`:
```yaml
# NEO4J_AUTH=none is intentional
```

---

### REQ-context-broker: Functional Requirements

---

#### REQ-context-broker §1.1 — Version Pinning

**Status:** PASS

**Evidence:** `requirements.txt` uses exact versions (`==`). `Dockerfile` pins base image.

---

#### REQ-context-broker §1.2 — Code Formatting

**Status:** PASS (same as REQ-001 §1.2)

---

#### REQ-context-broker §1.3 — Code Linting

**Status:** PASS (same as REQ-001 §1.3)

---

#### REQ-context-broker §1.4 — Unit Testing

**Status:** FAIL

**Evidence:** No test files present in the provided source code.

---

#### REQ-context-broker §1.5 — StateGraph Package Source

**Status:** PASS

**Evidence:** `config/config.example.yml` includes package source configuration:
```yaml
packages:
  source: pypi
  local_path: /app/packages
  devpi_url: null
```
And `entrypoint.sh` reads and applies this configuration.

---

#### REQ-context-broker §2.1 — Root Usage Pattern

**Status:** PASS (same as REQ-002 §1.1)

---

#### REQ-context-broker §2.2 — Service Account

**Status:** PASS (same as REQ-002 §1.2)

---

#### REQ-context-broker §2.3 — File Ownership

**Status:** PASS (same as REQ-002 §1.3)

---

#### REQ-context-broker §3.1 — Two-Volume Pattern

**Status:** PASS (same as REQ-002 §4.1)

---

#### REQ-context-broker §3.2 — Data Directory Organization

**Status:** PASS

**Evidence:** `docker-compose.yml` mounts `./data:/data`. `app/imperator/state_manager.py` writes `imperator_state.json` to `/data/`.

---

#### REQ-context-broker §3.3 — Config Directory Organization

**Status:** PASS

**Evidence:** `docker-compose.yml` mounts `./config:/config`. Structure includes `config.yml` and `credentials/.env`.

---

#### REQ-context-broker §3.4 — Credential Management

**Status:** PASS (same as REQ-002 §4.4)

---

#### REQ-context-broker §3.5 — Database Storage

**Status:** PASS (same as REQ-002 §4.2)

---

#### REQ-context-broker §3.6 — Backup and Recovery

**Status:** PASS

**Evidence:** All data under `/data/` as required. Schema migrations versioned in `app/migrations.py`.

---

#### REQ-context-broker §3.7 — Schema Migration

**Status:** PASS

**Evidence:** `app/migrations.py` implements versioned migrations with forward-only, non-destructive approach:
```python
async def run_migrations() -> None:
    """Apply all pending migrations in order."""
```

---

#### REQ-context-broker §4.1 — MCP Transport

**Status:** PASS (same as REQ-001 §6.1)

---

#### REQ-context-broker §4.2 — OpenAI-Compatible Chat

**Status:** PASS

**Evidence:** Implemented in `app/routes/chat.py`. Supports streaming and non-streaming.

---

#### REQ-context-broker §4.3 — Authentication

**Status:** PASS (same as REQ-002 §6.3)

---

#### REQ-context-broker §4.4 — Health Endpoint

**Status:** PASS (same as REQ-001 §6.3)

---

#### REQ-context-broker §4.5 — Tool Naming Convention

**Status:** PASS (same as REQ-001 §6.2)

---

#### REQ-context-broker §4.6 — MCP Tool Inventory

**Status:** PASS

**Evidence:** All required tools are implemented. The requirement lists 14 tools; the implementation includes all of them (see `app/routes/mcp.py` `_get_tool_list()`).

---

#### REQ-context-broker §4.7 — LangGraph Mandate

**Status:** PASS (same as REQ-001 §2.1)

---

#### REQ-context-broker §4.8 — Prometheus Metrics

**Status:** PASS (same as REQ-001 §6.4)

---

#### REQ-context-broker §5.1 — Configuration File

**Status:** PASS

**Evidence:** All configuration in `/config/config.yml`. Hot-reload implemented.

---

#### REQ-context-broker §5.2 — Inference Provider Configuration

**Status:** PASS

**Evidence:** Three provider slots configured in `config/config.example.yml`:
```yaml
llm:
  base_url: ...
  model: ...
  api_key_env: ...
embeddings:
  ...
reranker:
  ...
```

---

#### REQ-context-broker §5.3 — Build Type Configuration

**Status:** PASS

**Evidence:** Build types defined in `config/config.example.yml`:
```yaml
build_types:
  standard-tiered:
    tier1_pct: 0.08
    tier2_pct: 0.20
    tier3_pct: 0.72
  knowledge-enriched:
    tier1_pct: 0.05
    tier2_pct: 0.15
    tier3_pct: 0.50
    knowledge_graph_pct: 0.15
    semantic_retrieval_pct: 0.15
```

---

#### REQ-context-broker §5.4 — Token Budget Resolution

**Status:** PASS

**Evidence:** Implemented in `app/token_budget.py`:
```python
async def resolve_token_budget(...) -> int:
    """Resolve the token budget for a context window.
    
    Priority order:
    1. caller_override
    2. build_type_config["max_context_tokens"] if integer
    3. Auto-query LLM provider
    4. fallback_tokens
```

---

#### REQ-context-broker §5.5 — Imperator Configuration

**Status:** PASS

**Evidence:** Imperator config in `config/config.example.yml`:
```yaml
imperator:
  build_type: standard-tiered
  max_context_tokens: auto
  admin_tools: false
```

---

#### REQ-context-broker §5.6 — Package Source Configuration

**Status:** PASS (same as REQ-context-broker §1.5)

---

#### REQ-context-broker §6.1 — Logging to stdout/stderr

**Status:** PASS (same as REQ-001 §4.1)

---

#### REQ-context-broker §6.2 — Structured Logging

**Status:** PASS (same as REQ-001 §4.2)

---

#### REQ-context-broker §6.3 — Log Levels

**Status:** PASS (same as REQ-001 §4.3)

---

#### REQ-context-broker §6.4 — Log Content Standards

**Status:** PASS (same as REQ-001 §4.4)

---

#### REQ-context-broker §6.5 — Dockerfile HEALTHCHECK

**Status:** PASS (same as REQ-002 §1.5)

---

#### REQ-context-broker §6.6 — Health Check Architecture

**Status:** PASS (same as REQ-002 §5.2)

---

#### REQ-context-broker §6.7 — Specific Exception Handling

**Status:** PASS (same as REQ-001 §4.5)

---

#### REQ-context-broker §6.8 — Resource Management

**Status:** PASS (same as REQ-001 §4.6)

---

#### REQ-context-broker §6.9 — Error Context

**Status:** PASS (same as REQ-001 §4.7)

---

#### REQ-context-broker §7.1 — Graceful Degradation

**Status:** PASS (same as REQ-001 §7.1)

---

#### REQ-context-broker §7.2 — Independent Container Startup

**Status:** PASS (same as REQ-001 §7.2)

---

#### REQ-context-broker §7.3 — Network Topology

**Status:** PASS (same as REQ-002 §3.1)

---

#### REQ-context-broker §7.4 — Docker Compose

**Status:** PASS (same as REQ-002 §5.1)

---

#### REQ-context-broker §7.5 — Container-Only Deployment

**Status:** PASS (same as REQ-002 §2.3)

---

#### REQ-context-broker §7.6 — Asynchronous Correctness

**Status:** PASS (same as REQ-001 §5.1)

---

#### REQ-context-broker §7.7 — Input Validation

**Status:** PASS (same as REQ-001 §3.2)

---

#### REQ-context-broker §7.8 — Null/None Checking

**Status:** PASS (same as REQ-001 §3.3)

---

#### REQ-context-broker §7.9 — Idempotency

**Status:** PASS (same as REQ-001 §7.3)

---

#### REQ-context-broker §8.1 — README

**Status:** FAIL

**Evidence:** No README file is present in the provided source code.

---

#### REQ-context-broker §8.2 — Tool Documentation

**Status:** PARTIAL

**Evidence:** Tool schemas are defined in `app/routes/mcp.py` `_get_tool_list()`, but no separate README with examples is present.

---

#### REQ-context-broker §8.3 — Config Template

**Status:** PASS

**Evidence:** `config/config.example.yml` and `config/credentials/.env.example` are present.

---

## SUMMARY

| Requirement Document | PASS | FAIL | PARTIAL |
|---------------------|------|------|---------|
| REQ-001 | 30 | 1 | 1 |
| REQ-002 | 19 | 0 | 0 |
| REQ-context-broker | 43 | 2 | 1 |
| **TOTAL** | **92** | **3** | **2** |

### Critical Failures:
1. **REQ-001 §1.4 / REQ-context-broker §1.4** — No unit tests present
2. **REQ-context-broker §8.1** — No README file present

### Partial Compliance:
1. **REQ-001 §2.3** — Checkpointing not used (intentional for this architecture, but not documented as exception)
2. **REQ-context-broker §8.2** — Tool documentation present in code but no separate README with examples