"""
Exhaustive tests for alerter/alerter.py — the Context Broker Alerter service.

Covers all 15 capabilities: webhook endpoint, health endpoint,
_find_instruction, _llm_format, _send_slack, _send_discord, _send_ntfy,
_send_smtp, _send_twilio, _send_webhook, _send_to_channel,
_fetch_log_context, _record_event_and_deliveries, _ensure_tables,
_embed_text, and _load_config.
"""

import json
import smtplib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, ANY

import httpx
import pytest
import pytest_asyncio

from alerter.alerter import (
    app,
    _find_instruction,
    _llm_format,
    _send_slack,
    _send_discord,
    _send_ntfy,
    _send_smtp,
    _send_twilio,
    _send_webhook,
    _send_to_channel,
    _fetch_log_context,
    _record_event_and_deliveries,
    _embed_text,
    _ensure_tables,
    _load_config,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_pool():
    """Return an AsyncMock that behaves like an asyncpg.Pool."""
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    pool.close = AsyncMock()
    return pool


@pytest.fixture
def sample_event():
    return {
        "type": "system.error",
        "source": "context-broker",
        "subject": "pipeline-failure",
        "data": {"message": "Pipeline X crashed"},
    }


@pytest.fixture
def sample_instruction():
    return {
        "id": 42,
        "description": "system errors",
        "instruction": "Format this alert concisely for Slack.",
        "channels": [{"type": "slack", "webhook_url": "https://hooks.slack.com/test"}],
    }


@pytest_asyncio.fixture
async def async_client():
    """httpx AsyncClient wired to the FastAPI app via ASGITransport."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ===================================================================
# 1. POST /webhook
# ===================================================================


@pytest.mark.asyncio
async def test_webhook_invalid_json(async_client):
    resp = await async_client.post(
        "/webhook", content=b"not json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 400
    assert "Invalid JSON" in resp.json()["error"]


@pytest.mark.asyncio
async def test_webhook_missing_type(async_client):
    resp = await async_client.post("/webhook", json={"data": {"message": "hi"}})
    assert resp.status_code == 400
    assert "type" in resp.json()["error"]


@pytest.mark.asyncio
async def test_webhook_missing_data(async_client):
    resp = await async_client.post("/webhook", json={"type": "x"})
    assert resp.status_code == 400
    assert "data" in resp.json()["error"]


@pytest.mark.asyncio
async def test_webhook_happy_path_no_instruction(async_client, sample_event):
    with (
        patch("alerter.alerter._find_instruction", new_callable=AsyncMock, return_value=None),
        patch("alerter.alerter._record_event_and_deliveries", new_callable=AsyncMock),
        patch("alerter.alerter._config", {"default_channels": [{"type": "log"}]}),
        patch("alerter.alerter._pool", None),
    ):
        resp = await async_client.post("/webhook", json=sample_event)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "processed"
    assert body["instruction_id"] is None
    assert "log" in body["channels_succeeded"]


@pytest.mark.asyncio
async def test_webhook_happy_path_with_instruction(
    async_client, sample_event, sample_instruction
):
    with (
        patch("alerter.alerter._find_instruction", new_callable=AsyncMock, return_value=sample_instruction),
        patch("alerter.alerter._llm_format", new_callable=AsyncMock, return_value="formatted msg"),
        patch("alerter.alerter._send_to_channel", new_callable=AsyncMock),
        patch("alerter.alerter._record_event_and_deliveries", new_callable=AsyncMock),
        patch("alerter.alerter._config", {"llm": {"base_url": "http://llm"}}),
        patch("alerter.alerter._pool", MagicMock()),
    ):
        resp = await async_client.post("/webhook", json=sample_event)
    body = resp.json()
    assert body["status"] == "processed"
    assert body["instruction_id"] == 42


@pytest.mark.asyncio
async def test_webhook_channel_failure_recorded(
    async_client, sample_event, sample_instruction
):
    async def _boom(*a, **kw):
        raise httpx.HTTPStatusError("fail", request=MagicMock(), response=MagicMock())

    with (
        patch("alerter.alerter._find_instruction", new_callable=AsyncMock, return_value=sample_instruction),
        patch("alerter.alerter._llm_format", new_callable=AsyncMock, return_value=None),
        patch("alerter.alerter._send_to_channel", side_effect=_boom),
        patch("alerter.alerter._record_event_and_deliveries", new_callable=AsyncMock) as mock_rec,
        patch("alerter.alerter._config", {}),
        patch("alerter.alerter._pool", MagicMock()),
    ):
        resp = await async_client.post("/webhook", json=sample_event)
    body = resp.json()
    assert "slack" in body["channels_failed"]
    mock_rec.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_log_context_enrichment(async_client, sample_event):
    with (
        patch("alerter.alerter._find_instruction", new_callable=AsyncMock, return_value=None),
        patch("alerter.alerter._fetch_log_context", new_callable=AsyncMock, return_value="some logs"),
        patch("alerter.alerter._record_event_and_deliveries", new_callable=AsyncMock),
        patch("alerter.alerter._config", {"log_context": {"enabled": True}, "default_channels": [{"type": "log"}]}),
        patch("alerter.alerter._pool", MagicMock()),
    ):
        resp = await async_client.post("/webhook", json=sample_event)
    assert resp.status_code == 200


# ===================================================================
# 2. GET /health
# ===================================================================


@pytest.mark.asyncio
async def test_health_with_pool(async_client, mock_pool):
    mock_pool.fetchval.return_value = 5
    with patch("alerter.alerter._pool", mock_pool):
        resp = await async_client.get("/health")
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["postgres"] is True
    assert body["instructions"] == 5


@pytest.mark.asyncio
async def test_health_without_pool(async_client):
    with patch("alerter.alerter._pool", None):
        resp = await async_client.get("/health")
    body = resp.json()
    assert body["postgres"] is False
    assert body["instructions"] == 0


@pytest.mark.asyncio
async def test_health_pool_error(async_client, mock_pool):
    import asyncpg
    mock_pool.fetchval.side_effect = asyncpg.PostgresError("oops")
    with patch("alerter.alerter._pool", mock_pool):
        resp = await async_client.get("/health")
    body = resp.json()
    assert body["postgres"] is True
    assert body["instructions"] == 0


# ===================================================================
# 3. _find_instruction
# ===================================================================


@pytest.mark.asyncio
async def test_find_instruction_no_pool():
    with patch("alerter.alerter._pool", None):
        result = await _find_instruction({"type": "x", "data": {"message": "m"}})
    assert result is None


@pytest.mark.asyncio
async def test_find_instruction_vector_match(mock_pool):
    row = {"id": 1, "description": "d", "instruction": "i", "channels": "[]"}
    mock_pool.fetchrow.return_value = row
    with (
        patch("alerter.alerter._pool", mock_pool),
        patch("alerter.alerter._embed_text", new_callable=AsyncMock, return_value=[0.1, 0.2]),
    ):
        result = await _find_instruction({"type": "sys.err", "data": {"message": "m"}})
    assert result == dict(row)


@pytest.mark.asyncio
async def test_find_instruction_text_fallback(mock_pool):
    mock_pool.fetchrow.side_effect = [None, {"id": 2, "description": "d", "instruction": "i", "channels": "[]"}]
    with (
        patch("alerter.alerter._pool", mock_pool),
        patch("alerter.alerter._embed_text", new_callable=AsyncMock, return_value=[0.1]),
    ):
        result = await _find_instruction({"type": "sys.err", "data": {"message": "m"}})
    assert result["id"] == 2


@pytest.mark.asyncio
async def test_find_instruction_no_embedding_text_fallback(mock_pool):
    mock_pool.fetchrow.return_value = {"id": 3, "description": "d", "instruction": "i", "channels": "[]"}
    with (
        patch("alerter.alerter._pool", mock_pool),
        patch("alerter.alerter._embed_text", new_callable=AsyncMock, return_value=None),
    ):
        result = await _find_instruction({"type": "deploy", "data": {"message": "m"}})
    assert result["id"] == 3


@pytest.mark.asyncio
async def test_find_instruction_vector_search_error(mock_pool):
    import asyncpg
    mock_pool.fetchrow.side_effect = [asyncpg.PostgresError("vec fail"), None]
    with (
        patch("alerter.alerter._pool", mock_pool),
        patch("alerter.alerter._embed_text", new_callable=AsyncMock, return_value=[0.5]),
    ):
        result = await _find_instruction({"type": "x", "data": {"message": "m"}})
    assert result is None


@pytest.mark.asyncio
async def test_find_instruction_text_search_error(mock_pool):
    import asyncpg
    mock_pool.fetchrow.side_effect = asyncpg.PostgresError("text fail")
    with (
        patch("alerter.alerter._pool", mock_pool),
        patch("alerter.alerter._embed_text", new_callable=AsyncMock, return_value=None),
    ):
        result = await _find_instruction({"type": "x", "data": {"message": "m"}})
    assert result is None


# ===================================================================
# 4. _llm_format
# ===================================================================


@pytest.mark.asyncio
async def test_llm_format_success():
    llm_resp = {"choices": [{"message": {"content": "Formatted!"}}]}
    mock_resp = MagicMock()
    mock_resp.json.return_value = llm_resp
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("alerter.alerter.httpx.AsyncClient", return_value=mock_client):
        result = await _llm_format(
            {"base_url": "http://llm", "model": "gpt-test"},
            "instruction text",
            {"type": "t", "data": {}},
        )
    assert result == "Formatted!"


@pytest.mark.asyncio
async def test_llm_format_no_base_url():
    result = await _llm_format({"model": "m"}, "inst", {"type": "t"})
    assert result is None


@pytest.mark.asyncio
async def test_llm_format_no_model():
    result = await _llm_format({"base_url": "http://x"}, "inst", {"type": "t"})
    assert result is None


@pytest.mark.asyncio
async def test_llm_format_http_error():
    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.ConnectError("down")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("alerter.alerter.httpx.AsyncClient", return_value=mock_client):
        result = await _llm_format(
            {"base_url": "http://llm", "model": "m"}, "inst", {"type": "t"}
        )
    assert result is None


@pytest.mark.asyncio
async def test_llm_format_with_api_key():
    llm_resp = {"choices": [{"message": {"content": "ok"}}]}
    mock_resp = MagicMock()
    mock_resp.json.return_value = llm_resp
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("alerter.alerter.httpx.AsyncClient", return_value=mock_client),
        patch.dict("os.environ", {"MY_LLM_KEY": "secret123"}),
    ):
        result = await _llm_format(
            {"base_url": "http://llm", "model": "m", "api_key_env": "MY_LLM_KEY"},
            "inst",
            {"type": "t"},
        )
    assert result == "ok"
    call_kwargs = mock_client.post.call_args
    assert "Bearer secret123" in call_kwargs.kwargs.get("headers", {}).get("Authorization", "")


# ===================================================================
# 5. _send_slack
# ===================================================================


@pytest.mark.asyncio
async def test_send_slack_success():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("alerter.alerter.httpx.AsyncClient", return_value=mock_client):
        await _send_slack({"webhook_url": "https://hooks.slack.com/x"}, "hello")
    mock_client.post.assert_awaited_once()
    payload = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1].get("json")
    assert payload == {"text": "hello"}


@pytest.mark.asyncio
async def test_send_slack_missing_url():
    with pytest.raises(RuntimeError, match="webhook_url"):
        await _send_slack({}, "msg")


# ===================================================================
# 6. _send_discord
# ===================================================================


@pytest.mark.asyncio
async def test_send_discord_success():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("alerter.alerter.httpx.AsyncClient", return_value=mock_client):
        await _send_discord({"webhook_url": "https://discord.com/api/wh/x"}, "hello")
    payload = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1].get("json")
    assert payload == {"content": "hello"}


@pytest.mark.asyncio
async def test_send_discord_missing_url():
    with pytest.raises(RuntimeError, match="webhook_url"):
        await _send_discord({}, "msg")


# ===================================================================
# 7. _send_ntfy
# ===================================================================


@pytest.mark.asyncio
async def test_send_ntfy_success():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    event = {"type": "deploy.fail", "subject": "my-deploy"}
    config = {"url": "https://ntfy.sh/alerts", "priority": "high"}
    with patch("alerter.alerter.httpx.AsyncClient", return_value=mock_client):
        await _send_ntfy(config, "boom", event)
    call_kwargs = mock_client.post.call_args.kwargs if mock_client.post.call_args.kwargs else mock_client.post.call_args[1]
    headers = call_kwargs["headers"]
    assert headers["Title"] == "my-deploy"
    assert headers["Priority"] == "high"
    assert headers["Tags"] == "deploy.fail"


@pytest.mark.asyncio
async def test_send_ntfy_missing_url():
    with pytest.raises(RuntimeError, match="url"):
        await _send_ntfy({}, "msg", {"type": "t"})


@pytest.mark.asyncio
async def test_send_ntfy_no_subject_uses_type():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("alerter.alerter.httpx.AsyncClient", return_value=mock_client):
        await _send_ntfy({"url": "https://ntfy.sh/x"}, "msg", {"type": "alert.cpu"})
    headers = mock_client.post.call_args.kwargs["headers"]
    assert headers["Title"] == "alert.cpu"


# ===================================================================
# 8. _send_smtp
# ===================================================================


@pytest.mark.asyncio
async def test_send_smtp_success():
    with (
        patch("alerter.alerter._sync_send_smtp") as mock_sync,
        patch.dict("os.environ", {"SMTP_PASS": "pass123"}),
    ):
        config = {
            "host": "smtp.example.com",
            "port": 587,
            "username": "user@example.com",
            "password_env": "SMTP_PASS",
            "to": "dest@example.com",
        }
        await _send_smtp(config, "Alert body", {"type": "sys.err", "source": "cb"})
    mock_sync.assert_called_once()
    args = mock_sync.call_args[0]
    assert args[0] == "smtp.example.com"
    assert args[1] == 587
    assert args[2] == "user@example.com"
    assert args[3] == "pass123"


@pytest.mark.asyncio
async def test_send_smtp_missing_host():
    with pytest.raises(RuntimeError, match="host"):
        await _send_smtp({"to": "x@x.com"}, "msg", {"type": "t"})


@pytest.mark.asyncio
async def test_send_smtp_missing_to():
    with pytest.raises(RuntimeError, match="to"):
        await _send_smtp({"host": "smtp.x.com"}, "msg", {"type": "t"})


# ===================================================================
# 9. _send_twilio
# ===================================================================


@pytest.mark.asyncio
async def test_send_twilio_success():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    config = {
        "account_sid": "AC123",
        "auth_token_env": "TW_TOKEN",
        "from": "+15551234567",
        "to": "+15559876543",
    }
    with (
        patch("alerter.alerter.httpx.AsyncClient", return_value=mock_client),
        patch.dict("os.environ", {"TW_TOKEN": "tok"}),
    ):
        await _send_twilio(config, "Alert SMS")
    call_kwargs = mock_client.post.call_args.kwargs or mock_client.post.call_args[1]
    assert "AC123" in call_kwargs.get("auth", ("",))[0]


@pytest.mark.asyncio
async def test_send_twilio_missing_fields():
    with pytest.raises(RuntimeError, match="required fields"):
        await _send_twilio({"account_sid": "AC1"}, "msg")


# ===================================================================
# 10. _send_webhook
# ===================================================================


@pytest.mark.asyncio
async def test_send_webhook_success():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    config = {"url": "https://webhook.site/abc", "headers": {"X-Key": "val"}}
    event = {"type": "build.done", "source": "ci", "time": "2026-01-01"}
    with patch("alerter.alerter.httpx.AsyncClient", return_value=mock_client):
        await _send_webhook(config, "build completed", event)
    payload = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1]["json"]
    assert payload["type"] == "build.done"
    assert payload["message"] == "build completed"


@pytest.mark.asyncio
async def test_send_webhook_missing_url():
    with pytest.raises(RuntimeError, match="url"):
        await _send_webhook({}, "msg", {"type": "t"})


# ===================================================================
# 11. _send_to_channel — routing
# ===================================================================


@pytest.mark.asyncio
async def test_send_to_channel_slack():
    with patch("alerter.alerter._send_slack", new_callable=AsyncMock) as m:
        await _send_to_channel("slack", {"webhook_url": "u"}, "msg", {"type": "t"})
    m.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_to_channel_discord():
    with patch("alerter.alerter._send_discord", new_callable=AsyncMock) as m:
        await _send_to_channel("discord", {"webhook_url": "u"}, "msg", {"type": "t"})
    m.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_to_channel_ntfy():
    with patch("alerter.alerter._send_ntfy", new_callable=AsyncMock) as m:
        await _send_to_channel("ntfy", {"url": "u"}, "msg", {"type": "t"})
    m.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_to_channel_smtp():
    with patch("alerter.alerter._send_smtp", new_callable=AsyncMock) as m:
        await _send_to_channel("smtp", {"host": "h"}, "msg", {"type": "t"})
    m.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_to_channel_twilio():
    with patch("alerter.alerter._send_twilio", new_callable=AsyncMock) as m:
        await _send_to_channel("twilio", {}, "msg", {"type": "t"})
    m.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_to_channel_webhook():
    with patch("alerter.alerter._send_webhook", new_callable=AsyncMock) as m:
        await _send_to_channel("webhook", {"url": "u"}, "msg", {"type": "t"})
    m.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_to_channel_log(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="alerter"):
        await _send_to_channel("log", {}, "log this", {"type": "sys.info"})
    assert "ALERT" in caplog.text


@pytest.mark.asyncio
async def test_send_to_channel_unknown(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="alerter"):
        await _send_to_channel("carrier_pigeon", {}, "msg", {"type": "t"})
    assert "Unknown channel" in caplog.text


# ===================================================================
# 12. _fetch_log_context
# ===================================================================


@pytest.mark.asyncio
async def test_fetch_log_context_no_pool():
    with patch("alerter.alerter._pool", None):
        result = await _fetch_log_context({"level": "ERROR", "limit": 5, "minutes": 5})
    assert result is None


@pytest.mark.asyncio
async def test_fetch_log_context_with_rows(mock_pool):
    ts = datetime(2026, 3, 25, 12, 0, 0, tzinfo=timezone.utc)
    mock_pool.fetch.return_value = [
        {"container_name": "broker", "level": "ERROR", "message": "segfault", "timestamp": ts},
    ]
    with patch("alerter.alerter._pool", mock_pool):
        result = await _fetch_log_context({"level": "ERROR", "limit": 10, "minutes": 5})
    assert "segfault" in result
    assert "broker" in result


@pytest.mark.asyncio
async def test_fetch_log_context_empty_rows(mock_pool):
    mock_pool.fetch.return_value = []
    with patch("alerter.alerter._pool", mock_pool):
        result = await _fetch_log_context({"level": "ERROR", "limit": 10, "minutes": 5})
    assert result is None


@pytest.mark.asyncio
async def test_fetch_log_context_db_error(mock_pool):
    import asyncpg
    mock_pool.fetch.side_effect = asyncpg.PostgresError("fail")
    with patch("alerter.alerter._pool", mock_pool):
        result = await _fetch_log_context({"level": "ERROR"})
    assert result is None


# ===================================================================
# 13. _record_event_and_deliveries
# ===================================================================


@pytest.mark.asyncio
async def test_record_event_no_pool():
    with patch("alerter.alerter._pool", None):
        # Should silently return
        await _record_event_and_deliveries(
            {"type": "t"}, "msg", "fmt", None, [], [], []
        )


@pytest.mark.asyncio
async def test_record_event_inserts(mock_pool):
    mock_pool.fetchval.return_value = 99
    channels = [{"type": "slack"}, {"type": "log"}]
    with patch("alerter.alerter._pool", mock_pool):
        await _record_event_and_deliveries(
            {"type": "sys.err", "source": "cb", "subject": "pipe"},
            "raw msg",
            "formatted msg",
            42,
            channels,
            ["slack"],
            ["log"],
        )
    # event insert
    mock_pool.fetchval.assert_awaited_once()
    # two delivery inserts
    assert mock_pool.execute.await_count == 2


@pytest.mark.asyncio
async def test_record_event_same_message_null_formatted(mock_pool):
    mock_pool.fetchval.return_value = 1
    with patch("alerter.alerter._pool", mock_pool):
        await _record_event_and_deliveries(
            {"type": "t"}, "msg", "msg", None, [{"type": "log"}], ["log"], []
        )
    # When formatted == message, formatted_message should be None
    call_args = mock_pool.fetchval.call_args[0]
    assert call_args[5] is None  # formatted_message param


@pytest.mark.asyncio
async def test_record_event_db_error(mock_pool):
    import asyncpg
    mock_pool.fetchval.side_effect = asyncpg.PostgresError("insert fail")
    with patch("alerter.alerter._pool", mock_pool):
        # Should not raise, just log warning
        await _record_event_and_deliveries(
            {"type": "t"}, "m", "f", None, [], [], []
        )


# ===================================================================
# 14. _ensure_tables
# ===================================================================


@pytest.mark.asyncio
async def test_ensure_tables_creates_three_tables(mock_pool):
    with patch("alerter.alerter._pool", mock_pool):
        await _ensure_tables()
    assert mock_pool.execute.await_count == 3


@pytest.mark.asyncio
async def test_ensure_tables_no_pool():
    with patch("alerter.alerter._pool", None):
        await _ensure_tables()  # should not raise


# ===================================================================
# 15. _embed_text
# ===================================================================


@pytest.mark.asyncio
async def test_embed_text_success():
    embed_resp = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
    mock_resp = MagicMock()
    mock_resp.json.return_value = embed_resp
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("alerter.alerter.httpx.AsyncClient", return_value=mock_client),
        patch("alerter.alerter._config", {"embeddings": {"base_url": "http://emb", "model": "e5"}}),
    ):
        result = await _embed_text("test text")
    assert result == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_embed_text_no_config():
    with patch("alerter.alerter._config", {}):
        result = await _embed_text("text")
    assert result is None


@pytest.mark.asyncio
async def test_embed_text_no_base_url():
    with patch("alerter.alerter._config", {"embeddings": {"model": "e5"}}):
        result = await _embed_text("text")
    assert result is None


@pytest.mark.asyncio
async def test_embed_text_no_model():
    with patch("alerter.alerter._config", {"embeddings": {"base_url": "http://x"}}):
        result = await _embed_text("text")
    assert result is None


@pytest.mark.asyncio
async def test_embed_text_http_error():
    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.ConnectError("down")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("alerter.alerter.httpx.AsyncClient", return_value=mock_client),
        patch("alerter.alerter._config", {"embeddings": {"base_url": "http://emb", "model": "e5"}}),
    ):
        result = await _embed_text("text")
    assert result is None


@pytest.mark.asyncio
async def test_embed_text_with_api_key():
    embed_resp = {"data": [{"embedding": [1.0]}]}
    mock_resp = MagicMock()
    mock_resp.json.return_value = embed_resp
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("alerter.alerter.httpx.AsyncClient", return_value=mock_client),
        patch("alerter.alerter._config", {"embeddings": {"base_url": "http://emb", "model": "e5", "api_key_env": "EMB_KEY"}}),
        patch.dict("os.environ", {"EMB_KEY": "mykey"}),
    ):
        result = await _embed_text("text")
    assert result == [1.0]
    call_kwargs = mock_client.post.call_args.kwargs or mock_client.post.call_args[1]
    assert "Bearer mykey" in call_kwargs["headers"]["Authorization"]


# ===================================================================
# _load_config
# ===================================================================


def test_load_config_success(tmp_path):
    cfg_file = tmp_path / "alerter.yml"
    cfg_file.write_text("llm:\n  base_url: http://llm\n")
    with patch("alerter.alerter.CONFIG_PATH", str(cfg_file)):
        result = _load_config()
    assert result["llm"]["base_url"] == "http://llm"


def test_load_config_file_not_found():
    with patch("alerter.alerter.CONFIG_PATH", "/nonexistent/alerter.yml"):
        result = _load_config()
    assert result == {}


def test_load_config_invalid_yaml(tmp_path):
    cfg_file = tmp_path / "alerter.yml"
    cfg_file.write_text(":\n  :\n    - ][")
    with patch("alerter.alerter.CONFIG_PATH", str(cfg_file)):
        result = _load_config()
    assert result == {}


def test_load_config_empty_file(tmp_path):
    cfg_file = tmp_path / "alerter.yml"
    cfg_file.write_text("")
    with patch("alerter.alerter.CONFIG_PATH", str(cfg_file)):
        result = _load_config()
    assert result == {}
