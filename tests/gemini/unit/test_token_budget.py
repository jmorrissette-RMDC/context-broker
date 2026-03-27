import pytest
import httpx
from unittest.mock import patch, MagicMock, AsyncMock

from app.token_budget import resolve_token_budget, _query_provider_context_length

@pytest.mark.asyncio
async def test_resolve_token_budget_caller_override():
    config = {}
    bt_config = {"max_context_tokens": 1000, "fallback_tokens": 500}
    budget = await resolve_token_budget(config, bt_config, caller_override=2000)
    assert budget == 2000

@pytest.mark.asyncio
async def test_resolve_token_budget_explicit_int():
    config = {}
    bt_config = {"max_context_tokens": 1500, "fallback_tokens": 500}
    budget = await resolve_token_budget(config, bt_config)
    assert budget == 1500

@pytest.mark.asyncio
async def test_resolve_token_budget_unrecognized():
    config = {}
    bt_config = {"max_context_tokens": "invalid", "fallback_tokens": 500}
    budget = await resolve_token_budget(config, bt_config)
    assert budget == 500

@pytest.mark.asyncio
@patch("app.token_budget._query_provider_context_length")
async def test_resolve_token_budget_auto(mock_query):
    mock_query.return_value = 4000
    config = {}
    bt_config = {"max_context_tokens": "auto", "fallback_tokens": 500}
    budget = await resolve_token_budget(config, bt_config)
    assert budget == 4000
    mock_query.assert_called_once_with(config, 500)

@pytest.mark.asyncio
async def test_query_provider_missing_config():
    config = {"llm": {}}
    res = await _query_provider_context_length(config, 1000)
    assert res == 1000

@pytest.mark.asyncio
@patch("app.token_budget.get_api_key", return_value="secret")
@patch("httpx.AsyncClient")
async def test_query_provider_success(mock_client_class, mock_get_api_key):
    config = {
        "llm": {
            "base_url": "http://api.example.com/v1",
            "model": "test-model"
        }
    }
    
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"id": "other-model", "context_length": 1000},
            {"id": "test-model", "context_length": 8192}
        ]
    }
    
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client_class.return_value.__aenter__.return_value = mock_client
    
    res = await _query_provider_context_length(config, 1000)
    assert res == 8192

@pytest.mark.asyncio
@patch("app.token_budget.get_api_key", return_value="")
@patch("httpx.AsyncClient")
async def test_query_provider_model_not_found(mock_client_class, mock_get_api_key):
    config = {
        "llm": {
            "base_url": "http://api.example.com/v1",
            "model": "test-model"
        }
    }
    
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [{"id": "other-model", "context_length": 1000}]
    }
    
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client_class.return_value.__aenter__.return_value = mock_client
    
    res = await _query_provider_context_length(config, 500)
    assert res == 500

@pytest.mark.asyncio
@patch("app.token_budget.get_api_key", return_value="secret")
@patch("httpx.AsyncClient")
async def test_query_provider_http_error(mock_client_class, mock_get_api_key):
    config = {
        "llm": {
            "base_url": "http://api.example.com/v1",
            "model": "test-model"
        }
    }
    
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPError("error")
    mock_client.get.return_value = mock_response
    mock_client_class.return_value.__aenter__.return_value = mock_client
    
    res = await _query_provider_context_length(config, 500)
    assert res == 500

@pytest.mark.asyncio
@patch("app.token_budget.get_api_key", return_value="secret")
@patch("httpx.AsyncClient")
async def test_query_provider_json_error(mock_client_class, mock_get_api_key):
    config = {
        "llm": {
            "base_url": "http://api.example.com/v1",
            "model": "test-model"
        }
    }
    
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.json.side_effect = ValueError("invalid json")
    mock_client.get.return_value = mock_response
    mock_client_class.return_value.__aenter__.return_value = mock_client
    
    res = await _query_provider_context_length(config, 500)
    assert res == 500
