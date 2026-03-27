"""
Prometheus metrics registry for the Context Broker.

All metrics are defined here and imported by flows and routes.
Metrics are incremented inside StateGraph nodes, not in route handlers.

Memory note (TA-01): These metrics accumulate in process memory for the
lifetime of the container. The prometheus_client library pre-allocates
fixed-size bucket arrays per label combination, not per observation.
Current cardinality: ~25 tools × 2 statuses + ~5 job types × 2 statuses
+ ~3 build types ≈ 65 time series. Memory footprint is bounded at <1MB
even after millions of requests. No eviction is needed at current scale.
If high-cardinality labels are added in the future, revisit this.
"""

from prometheus_client import Counter, Histogram, Gauge

# MCP tool request metrics
MCP_REQUESTS = Counter(
    "context_broker_mcp_requests_total",
    "Total MCP tool requests",
    ["tool", "status"],
)

MCP_REQUEST_DURATION = Histogram(
    "context_broker_mcp_request_duration_seconds",
    "Duration of MCP tool requests",
    ["tool"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0],
)

# Chat endpoint metrics
CHAT_REQUESTS = Counter(
    "context_broker_chat_requests_total",
    "Total chat completion requests",
    ["status"],
)

CHAT_REQUEST_DURATION = Histogram(
    "context_broker_chat_request_duration_seconds",
    "Duration of chat completion requests",
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0],
)

# Background job metrics
JOBS_ENQUEUED = Counter(
    "context_broker_jobs_enqueued_total",
    "Total background jobs enqueued",
    ["job_type"],
)

JOBS_COMPLETED = Counter(
    "context_broker_jobs_completed_total",
    "Total background jobs completed",
    ["job_type", "status"],
)

JOB_DURATION = Histogram(
    "context_broker_job_duration_seconds",
    "Duration of background jobs",
    ["job_type"],
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)

# Queue depth gauges
EMBEDDING_QUEUE_DEPTH = Gauge(
    "context_broker_embedding_queue_depth",
    "Number of pending embedding jobs",
)

ASSEMBLY_QUEUE_DEPTH = Gauge(
    "context_broker_assembly_queue_depth",
    "Number of pending context assembly jobs",
)

EXTRACTION_QUEUE_DEPTH = Gauge(
    "context_broker_extraction_queue_depth",
    "Number of pending memory extraction jobs",
)

# Context assembly metrics
CONTEXT_ASSEMBLY_DURATION = Histogram(
    "context_broker_context_assembly_duration_seconds",
    "Duration of context assembly operations",
    ["build_type"],
    buckets=[0.5, 1.0, 5.0, 10.0, 30.0, 60.0],
)
