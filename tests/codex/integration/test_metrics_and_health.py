import pytest

from tests.codex.conftest import extract_mcp_result, mcp_call


@pytest.mark.integration
class TestMetricsAndHealth:
    def test_health_endpoint(self, cb_client):
        resp = cb_client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") in {"ok", "healthy", "degraded"} or body.get("status")

    def test_metrics_endpoint(self, cb_client):
        resp = cb_client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert "#" in text

    def test_metrics_get_tool(self, cb_client):
        resp = mcp_call(cb_client, "metrics_get", {})
        assert resp.status_code == 200
        result = extract_mcp_result(resp)
        assert "metrics" in result
        assert "#" in result["metrics"]
