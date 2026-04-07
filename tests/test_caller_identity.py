"""Tests for caller identity resolution (§4.14).

Verifies resolve_caller with user field, reverse DNS, and fallbacks.
"""

import socket
from unittest.mock import MagicMock, patch

import pytest

# caller_identity imports fastapi which may not be in the test env
# Import conditionally and skip if not available
try:
    from app.routes.caller_identity import resolve_caller, _dns_cache

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


@pytest.fixture(autouse=True)
def _clear_dns_cache():
    """Clear DNS cache between tests."""
    if HAS_FASTAPI:
        _dns_cache.clear()
    yield
    if HAS_FASTAPI:
        _dns_cache.clear()


def _make_request(client_host: str | None = None) -> MagicMock:
    """Create a mock FastAPI Request."""
    request = MagicMock()
    if client_host:
        request.client.host = client_host
    else:
        request.client = None
    return request


class TestResolveCallerWithUserField:
    """T-14.1: resolve_caller with explicit user field."""

    def test_user_field_takes_priority(self):
        request = _make_request("192.168.1.50")
        assert resolve_caller(request, "jason") == "jason"

    def test_user_field_empty_string_falls_through(self):
        request = _make_request("192.168.1.50")
        with patch("app.routes.caller_identity.socket.gethostbyaddr") as mock_dns:
            mock_dns.return_value = ("some-container", [], [])
            result = resolve_caller(request, "")
            # Empty string is falsy, falls through to DNS
            assert result == "some-container"


class TestResolveCallerReverseDNS:
    """T-14.2: resolve_caller reverse DNS fallback."""

    def test_reverse_dns_returns_hostname(self):
        request = _make_request("172.18.0.5")
        with patch("app.routes.caller_identity.socket.gethostbyaddr") as mock_dns:
            mock_dns.return_value = ("context-broker-langgraph", [], [])
            result = resolve_caller(request)
            assert result == "context-broker-langgraph"

    def test_dns_result_is_cached(self):
        request = _make_request("172.18.0.5")
        with patch("app.routes.caller_identity.socket.gethostbyaddr") as mock_dns:
            mock_dns.return_value = ("cached-host", [], [])
            resolve_caller(request)
            resolve_caller(request)
            # Should only call DNS once due to cache
            mock_dns.assert_called_once()


class TestResolveCallerFallback:
    """T-14.3: resolve_caller returns IP when DNS fails."""

    def test_dns_failure_returns_ip(self):
        request = _make_request("10.0.0.99")
        with patch("app.routes.caller_identity.socket.gethostbyaddr") as mock_dns:
            mock_dns.side_effect = socket.herror("not found")
            result = resolve_caller(request)
            assert result == "10.0.0.99"

    def test_no_client_returns_unknown(self):
        request = _make_request(None)
        result = resolve_caller(request)
        assert result == "unknown"
