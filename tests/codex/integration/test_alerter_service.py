import json
import os

import httpx
import pytest

from tests.codex.utils.remote_docker import docker_exec


@pytest.mark.integration
class TestAlerterService:
    def test_health_and_webhook(self):
        base_url = os.environ.get("ALERTER_BASE_URL")
        if not base_url:
            python_code = (
                "import json, urllib.request; "
                "health = urllib.request.urlopen('http://context-broker-alerter:8000/health'); "
                "health_body = health.read().decode(); "
                "payload = json.dumps({'id':'codex-test','type':'codex.alert','source':'codex','data':{'message':'codex test webhook'}}).encode('utf-8'); "
                "req = urllib.request.Request('http://context-broker-alerter:8000/webhook', data=payload, "
                "headers={'Content-Type':'application/json'}); "
                "resp = urllib.request.urlopen(req); "
                "resp_body = resp.read().decode(); "
                "print(json.dumps({'health_status':health.status,'health_body':health_body,"
                "'webhook_status':resp.status,'webhook_body':resp_body}))"
            )
            output = docker_exec("context-broker-langgraph", f"python -c \"{python_code}\"")
            result = json.loads(output)
            assert result["health_status"] == 200
            assert json.loads(result["health_body"]).get("status") == "healthy"
            assert result["webhook_status"] == 200
            assert json.loads(result["webhook_body"]).get("status") == "processed"
            return

        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            health = client.get("/health")
            assert health.status_code == 200
            body = health.json()
            assert body.get("status") == "healthy"

            payload = {
                "id": "codex-test",
                "type": "codex.alert",
                "source": "codex",
                "data": {"message": "codex test webhook"},
            }
            resp = client.post("/webhook", json=payload)
            assert resp.status_code == 200
            result = resp.json()
            assert result.get("status") == "processed"
