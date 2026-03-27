import pytest


def chat_content(cb_client, message: str) -> str:
    payload = {
        "model": "imperator",
        "messages": [{"role": "user", "content": message}],
        "stream": False,
    }
    resp = cb_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["choices"][0]["message"]["content"]


def db_query(cb_client, sql: str) -> str:
    prompt = (
        "Use db_query to run the following SQL and return only the raw output:\n"
        f"{sql}"
    )
    return chat_content(cb_client, prompt)


@pytest.mark.integration
class TestSchemaAndMigrations:
    def test_required_tables_exist(self, cb_client):
        required = [
            "schema_migrations",
            "conversations",
            "conversation_messages",
            "context_windows",
            "conversation_summaries",
            "system_logs",
            "stategraph_packages",
            "domain_information",
            "schedules",
            "schedule_history",
        ]
        placeholders = ", ".join([f"'{name}'" for name in required])
        sql = (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name IN (" + placeholders + ") "
            "ORDER BY table_name"
        )
        output = db_query(cb_client, sql)
        missing = [name for name in required if name not in output]
        assert not missing, f"Missing tables: {missing}. Output: {output}"

    def test_schema_version(self, cb_client):
        sql = "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        output = db_query(cb_client, sql)
        # Expect a numeric version in output (table-like text)
        digits = [int(tok) for tok in output.split() if tok.isdigit()]
        assert digits, f"No version found in output: {output}"
        assert max(digits) >= 20, f"Schema version too low: {max(digits)}"

    def test_required_columns_exist(self, cb_client):
        checks = [
            ("conversation_messages", "sender"),
            ("conversation_messages", "recipient"),
            ("conversation_messages", "tool_calls"),
            ("conversation_messages", "tool_call_id"),
            ("conversation_messages", "sequence_number"),
            ("context_windows", "max_token_budget"),
            ("context_windows", "build_type"),
            ("context_windows", "last_accessed_at"),
            ("schedules", "last_fired_at"),
        ]
        for table, column in checks:
            sql = (
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.columns "
                f"WHERE table_name = '{table}' AND column_name = '{column}'" 
                ") AS exists"
            )
            output = db_query(cb_client, sql)
            assert "t" in output.lower() or "true" in output.lower(), (
                f"Missing {table}.{column}. Output: {output}"
            )
