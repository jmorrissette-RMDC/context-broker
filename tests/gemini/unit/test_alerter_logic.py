import json
import pytest
import httpx
import respx
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from alerter.alerter import app, _load_config, _send_to_channel, _send_slack, _send_discord

@pytest.fixture
def client():
    return TestClient(app)

def test_load_config_missing_file():
    """Test config loading with a missing file."""
    with patch("builtins.open", side_effect=FileNotFoundError):
        config = _load_config()
        assert config == {}

def test_load_config_valid_file():
    """Test config loading with a valid YAML file."""
    yaml_content = "default_channels: [{'type': 'log'}]"
    with patch("builtins.open", MagicMock(return_value=MagicMock(__enter__=lambda s: MagicMock(read=lambda: yaml_content)))):
        # alerter code uses safe_load directly which calls open
        # We need a better mock for yaml.safe_load
        with patch("yaml.safe_load", return_value={"default_channels": [{"type": "log"}]}):
            config = _load_config()
            assert config["default_channels"] == [{"type": "log"}]

@respx.mock
@pytest.mark.asyncio
async def test_send_slack_success():
    """Test slack channel sending success."""
    respx.post("https://slack.com/webhook").respond(status_code=200)
    config = {"webhook_url": "https://slack.com/webhook"}
    await _send_slack(config, "test message")

@respx.mock
@pytest.mark.asyncio
async def test_send_slack_failure():
    """Test slack channel sending failure."""
    respx.post("https://slack.com/webhook").respond(status_code=500)
    config = {"webhook_url": "https://slack.com/webhook"}
    with pytest.raises(httpx.HTTPStatusError):
        await _send_slack(config, "test message")

import logging

@patch("alerter.alerter._log.info")
@pytest.mark.asyncio
async def test_send_to_channel_log(mock_info):
    """Test the log channel type."""
    config = {"type": "log"}
    event = {"type": "test.event"}
    await _send_to_channel("log", config, "log message", event)
    mock_info.assert_called_with("ALERT [%s]: %s", "test.event", "log message")

@pytest.mark.asyncio
async def test_webhook_no_type(client):
    """Test webhook fails with missing type."""
    response = client.post("/webhook", json={"data": {"message": "hello"}})
    assert response.status_code == 400
    assert "Missing 'type' field" in response.json()["error"]

@patch("alerter.alerter._pool", new_callable=AsyncMock)
@patch("alerter.alerter._find_instruction", return_value=None)
@patch("alerter.alerter._send_to_channel", new_callable=AsyncMock)
@patch("alerter.alerter._record_event_and_deliveries", new_callable=AsyncMock)
def test_webhook_full_flow_no_instruction(mock_record, mock_send, mock_find, mock_pool, client):
    """Test a full webhook flow with no instruction, using default channels."""
    with patch("alerter.alerter._config", {"default_channels": [{"type": "log"}]}):
        event_json = {
            "type": "test.event",
            "data": {"message": "hello world"}
        }
        response = client.post("/webhook", json=event_json)
        assert response.status_code == 200
        assert response.json()["status"] == "processed"
        assert response.json()["channels_succeeded"] == ["log"]
        mock_send.assert_called_once()
