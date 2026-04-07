import pytest
import json
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import httpx
from fastapi import Request
from email.message import EmailMessage

import alerter.alerter as alerter_module
from alerter.alerter import (
    health, _embed_text, _find_instruction, webhook,
    _send_slack, _send_discord, _send_webhook, _send_ntfy, _send_smtp, _send_twilio
)

@pytest.fixture(autouse=True)
def mock_alerter_state():
    alerter_module._config = {}
    alerter_module._pool = AsyncMock()
    yield
    alerter_module._config = {}
    alerter_module._pool = None

@pytest.mark.asyncio
async def test_health():
    alerter_module._pool.fetchval.return_value = 5
    result = await health()
    assert result["status"] == "healthy"
    assert result["postgres"] is True
    assert result["instructions"] == 5

    alerter_module._pool = None
    result = await health()
    assert result["postgres"] is False

@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_embed_text_success(mock_client_class):
    alerter_module._config = {
        "embeddings": {"base_url": "http://emb", "model": "test-model"}
    }
    
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2]}]}
    mock_client.post.return_value = mock_response
    mock_client_class.return_value.__aenter__.return_value = mock_client
    
    vec = await _embed_text("test")
    assert vec == [0.1, 0.2]

@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_embed_text_failure(mock_client_class):
    alerter_module._config = {
        "embeddings": {"base_url": "http://emb", "model": "test-model"}
    }
    
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPError("error")
    mock_client.post.return_value = mock_response
    mock_client_class.return_value.__aenter__.return_value = mock_client
    
    vec = await _embed_text("test")
    assert vec is None

@pytest.mark.asyncio
@patch("alerter.alerter._embed_text", return_value=[0.1, 0.2])
async def test_find_instruction_vector(mock_embed):
    event = {"type": "test", "data": {"message": "hello"}}
    
    alerter_module._pool.fetchrow.side_effect = [
        {"id": 1, "description": "desc", "instruction": "inst", "channels": "[]"}, # vector hit
        None # text miss
    ]
    
    inst = await _find_instruction(event)
    assert inst["id"] == 1

@pytest.mark.asyncio
@patch("alerter.alerter._embed_text", return_value=None)
async def test_find_instruction_text(mock_embed):
    event = {"type": "test", "data": {"message": "hello"}}
    
    alerter_module._pool.fetchrow.side_effect = [
        {"id": 2, "description": "desc", "instruction": "inst", "channels": "[]"}, # text hit
    ]
    
    inst = await _find_instruction(event)
    assert inst["id"] == 2

@pytest.mark.asyncio
@patch("alerter.alerter._find_instruction")
@patch("alerter.alerter._send_to_channel")
@patch("alerter.alerter._record_event_and_deliveries")
async def test_webhook_success(mock_record, mock_send, mock_find):
    mock_find.return_value = {"id": 1, "instruction": "sys", "channels": '[{"type": "slack"}]'}
    request = MagicMock(spec=Request)
    request.json = AsyncMock(return_value={"type": "test", "data": {"message": "hello"}})
    
    response = await webhook(request)
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["status"] == "processed"
    assert "slack" in body["channels_succeeded"]

@pytest.mark.asyncio
async def test_webhook_invalid_json():
    request = MagicMock(spec=Request)
    request.json = AsyncMock(side_effect=ValueError("bad json"))
    response = await webhook(request)
    assert response.status_code == 400

@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_send_slack(mock_client_class):
    mock_client = AsyncMock()
    mock_client_class.return_value.__aenter__.return_value = mock_client
    
    await _send_slack({"webhook_url": "http://slack"}, "hello")
    mock_client.post.assert_called_once()
    assert mock_client.post.call_args[1]["json"] == {"text": "hello"}

@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_send_discord(mock_client_class):
    mock_client = AsyncMock()
    mock_client_class.return_value.__aenter__.return_value = mock_client
    
    await _send_discord({"webhook_url": "http://discord"}, "hello")
    mock_client.post.assert_called_once()
    assert mock_client.post.call_args[1]["json"] == {"content": "hello"}

@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_send_webhook(mock_client_class):
    mock_client = AsyncMock()
    mock_client_class.return_value.__aenter__.return_value = mock_client
    
    await _send_webhook({"url": "http://webhook"}, "hello", {"type": "t"})
    mock_client.post.assert_called_once()
    assert mock_client.post.call_args[1]["json"]["message"] == "hello"

@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_send_ntfy(mock_client_class):
    mock_client = AsyncMock()
    mock_client_class.return_value.__aenter__.return_value = mock_client
    
    await _send_ntfy({"url": "http://ntfy", "priority": "high"}, "hello", {"subject": "test"})
    mock_client.post.assert_called_once()
    assert mock_client.post.call_args[1]["content"] == "hello"

@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_send_twilio(mock_client_class):
    mock_client = AsyncMock()
    mock_client_class.return_value.__aenter__.return_value = mock_client
    
    with patch("os.environ.get", return_value="token"):
        await _send_twilio({"account_sid": "sid", "auth_token_env": "TOKEN", "from": "123", "to": "456"}, "hello")
    mock_client.post.assert_called_once()
    assert mock_client.post.call_args[1]["data"]["Body"] == "hello"

@pytest.mark.asyncio
@patch("alerter.alerter._sync_send_smtp")
async def test_send_smtp(mock_sync_send):
    config = {"host": "smtp.example.com", "to": "test@example.com"}
    await _send_smtp(config, "hello", {"type": "test_alert"})
    mock_sync_send.assert_called_once()
    msg = mock_sync_send.call_args[0][4]
    assert isinstance(msg, EmailMessage)
    assert msg.get_content().strip() == "hello"
    assert msg["To"] == "test@example.com"
