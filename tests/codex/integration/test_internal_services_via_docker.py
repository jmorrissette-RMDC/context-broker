import json
import uuid

import pytest

from tests.codex.conftest import wait_for_condition
from tests.codex.utils.remote_docker import docker_exec, psql_query, run_ssh


@pytest.mark.integration
class TestInternalServicesViaDocker:
    def test_alerter_health_and_webhook_via_docker(self):
        payload_id = f"codex-{uuid.uuid4().hex[:8]}"
        python_code = (
            "import json, urllib.request; "
            "health = urllib.request.urlopen('http://context-broker-alerter:8000/health'); "
            "health_body = health.read().decode(); "
            f"payload = json.dumps({{'id':'{payload_id}','type':'codex.alert','source':'codex','data':{{'message':'codex test webhook'}}}}).encode('utf-8'); "
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
        health_body = json.loads(result["health_body"])
        assert health_body.get("status") == "healthy"
        assert result["webhook_status"] == 200
        webhook_body = json.loads(result["webhook_body"])
        assert webhook_body.get("status") == "processed"

    def test_infinity_health_via_docker(self):
        python_code = (
            "import json, urllib.request; "
            "resp = urllib.request.urlopen('http://context-broker-infinity:7997/health'); "
            "body = resp.read().decode(); "
            "print(json.dumps({'status': resp.status, 'body': body}))"
        )
        output = docker_exec("context-broker-langgraph", f"python -c \"{python_code}\"")
        result = json.loads(output)
        assert result["status"] == 200
        body = json.loads(result["body"])
        assert body.get("status") in {"ok", "healthy"} or "unix" in body

    def test_neo4j_query_via_docker(self):
        output = docker_exec(
            "context-broker-neo4j",
            "cypher-shell 'RETURN 1 AS ok'",
        )
        assert "1" in output

    def test_log_shipper_captures_container_logs(self):
        marker = f"codex-log-{uuid.uuid4().hex[:8]}"
        run_ssh(f"curl -s 'http://localhost:8080/health?marker={marker}' > /dev/null")

        def _has_log() -> bool:
            out = psql_query(
                "SELECT COUNT(*) FROM system_logs WHERE container_name = 'context-broker' "
                "AND message LIKE '%" + marker + "%';"
            )
            try:
                return int(out.strip()) > 0
            except ValueError:
                return False

        assert wait_for_condition(_has_log, timeout_seconds=30, interval=2)
