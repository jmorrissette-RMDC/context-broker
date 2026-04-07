import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from context_broker_te.tools.notify import send_notification

@pytest.mark.asyncio
async def test_send_notification_alerter_success():
    with patch("app.config.load_merged_config", return_value={"imperator": {"notification_webhook": "http://context-broker-alerter:8000/webhook"}}), \
         patch("httpx.AsyncClient") as MockClient, \
         patch("socket.gethostname", return_value="testhost"):
        
        mock_resp = AsyncMock()
        mock_resp.json = MagicMock(return_value={"channels_succeeded": ["slack", "ntfy"], "channels_failed": []})
        mock_resp.raise_for_status = MagicMock()
        
        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_resp
        MockClient.return_value.__aenter__.return_value = mock_client_instance

        result = await send_notification.ainvoke({"message": "Test alert"})
        assert "Alert sent" in result
        
        # Verify CloudEvents format
        call_kwargs = mock_client_instance.post.call_args[1]
        payload = call_kwargs["json"]
        assert payload["type"] == "imperator.notification"
        assert payload["source"] == "testhost"
        assert payload["data"]["message"] == "Test alert"

@pytest.mark.asyncio
async def test_send_notification_ntfy_success():
    with patch("app.config.load_merged_config", return_value={"imperator": {"notification_webhook": "https://ntfy.sh/my-topic"}}), \
         patch("httpx.AsyncClient") as MockClient:
        
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        
        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_resp
        MockClient.return_value.__aenter__.return_value = mock_client_instance

        result = await send_notification.ainvoke({"message": "Ntfy alert", "severity": "error"})
        assert "Notification sent" in result
        
        # Verify ntfy format
        call_kwargs = mock_client_instance.post.call_args[1]
        assert call_kwargs["content"] == "Ntfy alert"
        assert call_kwargs["headers"]["Priority"] == "urgent"

@pytest.mark.asyncio
async def test_send_notification_generic_success():
    with patch("app.config.load_merged_config", return_value={"imperator": {"notification_webhook": "http://custom-webhook"}}), \
         patch("httpx.AsyncClient") as MockClient:
        
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        
        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_resp
        MockClient.return_value.__aenter__.return_value = mock_client_instance

        result = await send_notification.ainvoke({"message": "Generic alert"})
        assert "Notification sent" in result
        
        # Verify generic format
        call_kwargs = mock_client_instance.post.call_args[1]
        payload = call_kwargs["json"]
        assert payload["text"] == "Generic alert"
        assert payload["severity"] == "info"

@pytest.mark.asyncio
async def test_send_notification_error():
    import httpx
    with patch("app.config.load_merged_config", return_value={"imperator": {"notification_webhook": "http://custom-webhook"}}), \
         patch("httpx.AsyncClient") as MockClient:
        
        mock_client_instance = AsyncMock()
        mock_client_instance.post.side_effect = httpx.HTTPError("Network error")
        MockClient.return_value.__aenter__.return_value = mock_client_instance
        
        result = await send_notification.ainvoke({"message": "Error alert"})
        assert "Notification failed" in result
