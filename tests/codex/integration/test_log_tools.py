import pytest

from tests.codex.conftest import extract_mcp_result, mcp_call


@pytest.mark.integration
class TestLogTools:
    def test_query_logs(self, cb_client):
        resp = mcp_call(cb_client, "query_logs", {"limit": 5})
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "entries" in result

    def test_search_logs(self, cb_client):
        resp = mcp_call(cb_client, "search_logs", {"query": "error", "limit": 3})
        if resp.status_code == 200:
            result = extract_mcp_result(resp)
            assert "entries" in result
        else:
            body = resp.json()
            assert "error" in body
