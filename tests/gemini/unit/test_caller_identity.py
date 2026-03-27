import pytest
import socket
from unittest.mock import patch, MagicMock
from fastapi import Request

from app.routes.caller_identity import resolve_caller, _dns_cache

@pytest.fixture(autouse=True)
def clear_cache():
    _dns_cache.clear()
    yield

def test_resolve_caller_with_user_field():
    request = MagicMock(spec=Request)
    assert resolve_caller(request, "test_user") == "test_user"

@patch("socket.gethostbyaddr")
def test_resolve_caller_with_ip_success(mock_gethostbyaddr):
    request = MagicMock(spec=Request)
    request.client.host = "192.168.1.1"
    mock_gethostbyaddr.return_value = ("test-container", [], ["192.168.1.1"])

    result = resolve_caller(request)
    assert result == "test-container"
    assert _dns_cache["192.168.1.1"] == "test-container"
    mock_gethostbyaddr.assert_called_once_with("192.168.1.1")

    # Test cache hit
    mock_gethostbyaddr.reset_mock()
    result2 = resolve_caller(request)
    assert result2 == "test-container"
    mock_gethostbyaddr.assert_not_called()

@patch("socket.gethostbyaddr")
def test_resolve_caller_with_ip_failure(mock_gethostbyaddr):
    request = MagicMock(spec=Request)
    request.client.host = "192.168.1.2"
    mock_gethostbyaddr.side_effect = socket.herror("host not found")

    result = resolve_caller(request)
    assert result == "192.168.1.2"
    assert _dns_cache["192.168.1.2"] == "192.168.1.2"
    
    # Test cache hit
    mock_gethostbyaddr.reset_mock()
    result2 = resolve_caller(request)
    assert result2 == "192.168.1.2"
    mock_gethostbyaddr.assert_not_called()

def test_resolve_caller_no_client():
    request = MagicMock(spec=Request)
    request.client = None
    assert resolve_caller(request) == "unknown"

def test_resolve_caller_no_host():
    request = MagicMock(spec=Request)
    request.client.host = None
    assert resolve_caller(request) == "unknown"
