"""Tests for log_shipper/shipper.py — LogShipper class."""

import asyncio
import json
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# aiodocker may not be installed in the test environment.  Provide a
# lightweight stub so that ``import log_shipper.shipper`` succeeds.
# ---------------------------------------------------------------------------
class _StubDockerError(Exception):
    def __init__(self, status=0, data=None):
        self.status = status
        super().__init__(str(data))


_mock_aiodocker = MagicMock()
_mock_aiodocker.exceptions = MagicMock()
_mock_aiodocker.exceptions.DockerError = _StubDockerError
sys.modules.setdefault("aiodocker", _mock_aiodocker)
sys.modules.setdefault("aiodocker.exceptions", _mock_aiodocker.exceptions)

# ---------------------------------------------------------------------------
# Module-under-test import is deferred so we can patch env vars first.
# ---------------------------------------------------------------------------


@pytest.fixture
def _patch_env(monkeypatch):
    """Ensure predictable config values."""
    monkeypatch.setenv("BATCH_SIZE", "5")
    monkeypatch.setenv("FLUSH_INTERVAL_SEC", "0.1")


@pytest.fixture
def shipper_module(_patch_env):
    """Import (or re-import) the shipper module with patched env."""
    import importlib
    import log_shipper.shipper as mod

    mod = importlib.reload(mod)
    return mod


@pytest.fixture
def shipper(shipper_module):
    """Return a fresh LogShipper instance."""
    return shipper_module.LogShipper()


# -- Mock helpers -----------------------------------------------------------

def _make_async_cm(return_value):
    """Create a proper async context manager mock."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=return_value)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _mock_pg_pool(conn=None):
    if conn is None:
        conn = AsyncMock()
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_make_async_cm(conn))
    pool.close = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    pool.executemany = AsyncMock()
    return pool


def _mock_docker():
    docker = AsyncMock()
    docker.close = AsyncMock()
    return docker


# ===========================================================================
# setup()
# ===========================================================================

@pytest.mark.asyncio
async def test_setup_connects_postgres_and_discovers_network_via_self(
    shipper, shipper_module, monkeypatch
):
    """setup() connects to Postgres pool, discovers Docker network via own container."""
    monkeypatch.setenv("HOSTNAME", "abc123container")

    mock_pool = _mock_pg_pool()
    mock_docker = _mock_docker()

    # Simulate container inspection returning network info
    mock_container = {
        "NetworkSettings": {
            "Networks": {
                "context-broker-net": {"NetworkID": "net-id-001"}
            }
        }
    }
    mock_docker.containers.get = AsyncMock(return_value=mock_container)

    with patch.object(
        shipper_module.asyncpg, "create_pool", AsyncMock(return_value=mock_pool)
    ), patch.object(shipper_module, "aiodocker", MagicMock()) as mock_aio:
        mock_aio.Docker.return_value = mock_docker
        await shipper.setup()

    assert shipper.pg_pool is mock_pool
    assert shipper.network_id == "net-id-001"


@pytest.mark.asyncio
async def test_setup_fallback_network_discovery_by_name(
    shipper, shipper_module, monkeypatch
):
    """setup() falls back to network discovery by name when HOSTNAME is unset."""
    monkeypatch.delenv("HOSTNAME", raising=False)

    mock_pool = _mock_pg_pool()
    mock_docker = _mock_docker()
    mock_docker.networks.list = AsyncMock(
        return_value=[
            {"Name": "bridge", "Id": "bridge-id"},
            {"Name": "context-broker-net", "Id": "net-id-002"},
        ]
    )

    with patch.object(
        shipper_module.asyncpg, "create_pool", AsyncMock(return_value=mock_pool)
    ), patch.object(shipper_module, "aiodocker", MagicMock()) as mock_aio:
        mock_aio.Docker.return_value = mock_docker
        await shipper.setup()

    assert shipper.network_id == "net-id-002"


# ===========================================================================
# _get_last_timestamp()
# ===========================================================================

@pytest.mark.asyncio
async def test_get_last_timestamp_returns_unix_ts(shipper):
    """_get_last_timestamp() returns last log_timestamp for a container."""
    dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"log_timestamp": dt})

    shipper.pg_pool = _mock_pg_pool(conn=mock_conn)

    result = await shipper._get_last_timestamp("my-container")
    assert result == str(int(dt.timestamp()))


@pytest.mark.asyncio
async def test_get_last_timestamp_returns_zero_for_new_container(shipper):
    """_get_last_timestamp() returns '0' when no logs exist for the container."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)

    shipper.pg_pool = _mock_pg_pool(conn=mock_conn)

    result = await shipper._get_last_timestamp("brand-new")
    assert result == "0"


# ===========================================================================
# tail_container()
# ===========================================================================

@pytest.mark.asyncio
async def test_tail_container_parses_docker_log_lines(shipper, shipper_module):
    """tail_container() parses Docker log lines with timestamps."""
    shipper.running = True
    shipper.pg_pool = _mock_pg_pool()

    # Prepare a mock container that streams one log line then stops
    log_line = "2025-06-01T12:00:00.123456789Z Hello from container"

    async def _stream():
        yield log_line
        shipper.running = False

    mock_container = {
        "Name": "/test-app",
    }
    mock_container_obj = AsyncMock()
    mock_container_obj.__getitem__ = lambda self, k: mock_container[k]
    mock_container_obj.log = MagicMock(return_value=_stream())

    mock_docker = _mock_docker()
    mock_docker.containers.get = AsyncMock(return_value=mock_container_obj)
    shipper.docker = mock_docker

    # Patch _get_last_timestamp
    shipper._get_last_timestamp = AsyncMock(return_value="0")

    await shipper.tail_container("container-id-001")

    # Verify item was queued
    assert not shipper.log_queue.empty()
    payload = shipper.log_queue.get_nowait()
    assert payload["container_name"] == "test-app"
    assert payload["message"] == "Hello from container"


@pytest.mark.asyncio
async def test_tail_container_handles_json_structured_logs(shipper):
    """tail_container() extracts message/msg fields from JSON logs."""
    shipper.running = True
    shipper.pg_pool = _mock_pg_pool()

    json_msg = json.dumps({"message": "Structured log entry", "level": "info"})
    log_line = f"2025-06-01T12:00:00.000000Z {json_msg}"

    async def _stream():
        yield log_line
        shipper.running = False

    mock_container_obj = AsyncMock()
    mock_container_obj.__getitem__ = lambda self, k: {"Name": "/json-app"}[k]
    mock_container_obj.log = MagicMock(return_value=_stream())

    mock_docker = _mock_docker()
    mock_docker.containers.get = AsyncMock(return_value=mock_container_obj)
    shipper.docker = mock_docker
    shipper._get_last_timestamp = AsyncMock(return_value="0")

    await shipper.tail_container("container-json-001")

    payload = shipper.log_queue.get_nowait()
    assert payload["message"] == "Structured log entry"


@pytest.mark.asyncio
async def test_tail_container_extracts_msg_field(shipper):
    """tail_container() falls back to 'msg' field in JSON logs."""
    shipper.running = True
    shipper.pg_pool = _mock_pg_pool()

    json_msg = json.dumps({"msg": "Alt structured entry", "level": "debug"})
    log_line = f"2025-06-01T12:00:00.000000Z {json_msg}"

    async def _stream():
        yield log_line
        shipper.running = False

    mock_container_obj = AsyncMock()
    mock_container_obj.__getitem__ = lambda self, k: {"Name": "/msg-app"}[k]
    mock_container_obj.log = MagicMock(return_value=_stream())

    mock_docker = _mock_docker()
    mock_docker.containers.get = AsyncMock(return_value=mock_container_obj)
    shipper.docker = mock_docker
    shipper._get_last_timestamp = AsyncMock(return_value="0")

    await shipper.tail_container("container-msg-001")

    payload = shipper.log_queue.get_nowait()
    assert payload["message"] == "Alt structured entry"


@pytest.mark.asyncio
async def test_tail_container_skips_self(shipper):
    """tail_container() skips the log-shipper's own container."""
    shipper.running = True
    shipper.pg_pool = _mock_pg_pool()

    mock_container_obj = AsyncMock()
    mock_container_obj.__getitem__ = lambda self, k: {
        "Name": "/context-broker-log-shipper"
    }[k]

    mock_docker = _mock_docker()
    mock_docker.containers.get = AsyncMock(return_value=mock_container_obj)
    shipper.docker = mock_docker

    await shipper.tail_container("self-container-id")

    # Nothing should be queued
    assert shipper.log_queue.empty()


@pytest.mark.asyncio
async def test_tail_container_handles_docker_error_404(shipper, shipper_module):
    """tail_container() handles DockerError 404 when container is gone."""
    shipper.running = True

    DockerError = shipper_module.aiodocker.exceptions.DockerError
    mock_docker = _mock_docker()
    mock_docker.containers.get = AsyncMock(
        side_effect=DockerError(status=404, data={"message": "not found"})
    )
    shipper.docker = mock_docker
    shipper.active_tasks["gone-container"] = MagicMock()

    await shipper.tail_container("gone-container")

    # Task should be cleaned up
    assert "gone-container" not in shipper.active_tasks


# ===========================================================================
# _write_batch()
# ===========================================================================

@pytest.mark.asyncio
async def test_write_batch_bulk_inserts(shipper):
    """_write_batch() bulk inserts to system_logs table."""
    mock_conn = AsyncMock()

    shipper.pg_pool = _mock_pg_pool(conn=mock_conn)

    batch = [
        {
            "container_name": "app-1",
            "timestamp": datetime.now(timezone.utc),
            "message": "test log",
            "data": '{"raw": "test log"}',
        }
    ]

    await shipper._write_batch(batch)
    mock_conn.executemany.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_batch_empty_is_noop(shipper):
    """_write_batch() does nothing for an empty batch."""
    shipper.pg_pool = _mock_pg_pool()
    await shipper._write_batch([])
    # acquire should never be called
    shipper.pg_pool.acquire.assert_not_called()


@pytest.mark.asyncio
async def test_write_batch_handles_db_failure(shipper):
    """_write_batch() handles DB failure gracefully (logs error, does not raise)."""
    import asyncpg
    mock_conn = AsyncMock()
    mock_conn.executemany = AsyncMock(side_effect=asyncpg.PostgresError("DB down"))

    shipper.pg_pool = _mock_pg_pool(conn=mock_conn)

    batch = [
        {
            "container_name": "app-1",
            "timestamp": datetime.now(timezone.utc),
            "message": "will fail",
            "data": "{}",
        }
    ]

    # Should not raise
    await shipper._write_batch(batch)


# ===========================================================================
# postgres_writer_loop()
# ===========================================================================

@pytest.mark.asyncio
async def test_postgres_writer_loop_batches_by_size(shipper):
    """postgres_writer_loop() flushes when batch reaches BATCH_SIZE."""
    shipper.running = True
    shipper._write_batch = AsyncMock()

    # Pre-fill the queue with items (BATCH_SIZE is 5 from fixture)
    for i in range(5):
        await shipper.log_queue.put(
            {
                "container_name": "app",
                "timestamp": datetime.now(timezone.utc),
                "message": f"msg-{i}",
                "data": "{}",
            }
        )

    async def _stop_after_flush(*args, **kwargs):
        shipper.running = False

    shipper._write_batch.side_effect = _stop_after_flush

    await shipper.postgres_writer_loop()

    shipper._write_batch.assert_awaited()
    # The batch should have had items
    call_args = shipper._write_batch.call_args_list[0][0][0]
    assert len(call_args) >= 1


@pytest.mark.asyncio
async def test_postgres_writer_loop_flushes_on_timeout(shipper):
    """postgres_writer_loop() flushes partial batch after flush interval."""
    shipper.running = True
    shipper._write_batch = AsyncMock()

    # Put just 1 item (below BATCH_SIZE of 5)
    await shipper.log_queue.put(
        {
            "container_name": "app",
            "timestamp": datetime.now(timezone.utc),
            "message": "lonely",
            "data": "{}",
        }
    )

    async def _stop_after_flush(*args, **kwargs):
        shipper.running = False

    shipper._write_batch.side_effect = _stop_after_flush

    await shipper.postgres_writer_loop()

    shipper._write_batch.assert_awaited()


# ===========================================================================
# scan_existing_containers()
# ===========================================================================

@pytest.mark.asyncio
async def test_scan_existing_containers_starts_tailing(shipper):
    """scan_existing_containers() starts tailing containers on our network."""
    shipper.network_id = "net-id-001"

    mock_container = AsyncMock()
    mock_container.show = AsyncMock(
        return_value={
            "Id": "container-aaa",
            "NetworkSettings": {
                "Networks": {
                    "context-broker-net": {"NetworkID": "net-id-001"}
                }
            },
        }
    )

    mock_docker = _mock_docker()
    mock_docker.containers.list = AsyncMock(return_value=[mock_container])
    shipper.docker = mock_docker

    # Patch tail_container so it does nothing
    shipper.tail_container = AsyncMock()

    await shipper.scan_existing_containers()

    assert "container-aaa" in shipper.active_tasks


# ===========================================================================
# event_watcher_loop()
# ===========================================================================

@pytest.mark.asyncio
async def test_event_watcher_connect_starts_tail(shipper):
    """event_watcher_loop() starts tail on 'connect' event."""
    shipper.running = True
    shipper.network_id = "net-001"
    shipper.tail_container = AsyncMock()

    connect_event = {
        "Action": "connect",
        "Actor": {
            "Attributes": {"container": "new-container-id"}
        },
    }

    call_count = 0

    async def _mock_get():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return connect_event
        shipper.running = False
        return None

    mock_subscriber = AsyncMock()
    mock_subscriber.get = _mock_get

    mock_docker = _mock_docker()
    mock_docker.events.subscribe = MagicMock(return_value=mock_subscriber)
    shipper.docker = mock_docker

    await shipper.event_watcher_loop()

    assert "new-container-id" in shipper.active_tasks


@pytest.mark.asyncio
async def test_event_watcher_disconnect_cancels_tail(shipper):
    """event_watcher_loop() cancels tail on 'disconnect' event."""
    shipper.running = True
    shipper.network_id = "net-001"

    # Pre-existing task
    mock_task = MagicMock()
    shipper.active_tasks["leaving-container"] = mock_task

    disconnect_event = {
        "Action": "disconnect",
        "Actor": {
            "Attributes": {"container": "leaving-container"}
        },
    }

    call_count = 0

    async def _mock_get():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return disconnect_event
        shipper.running = False
        return None

    mock_subscriber = AsyncMock()
    mock_subscriber.get = _mock_get

    mock_docker = _mock_docker()
    mock_docker.events.subscribe = MagicMock(return_value=mock_subscriber)
    shipper.docker = mock_docker

    await shipper.event_watcher_loop()

    mock_task.cancel.assert_called_once()
    assert "leaving-container" not in shipper.active_tasks


# ===========================================================================
# handle_sigterm()
# ===========================================================================

def test_handle_sigterm_sets_running_false_and_cancels(shipper, shipper_module):
    """handle_sigterm() sets running=False and cancels the main task."""
    shipper.running = True
    mock_task = MagicMock()

    shipper_module.handle_sigterm(shipper, mock_task)

    assert shipper.running is False
    mock_task.cancel.assert_called_once()
