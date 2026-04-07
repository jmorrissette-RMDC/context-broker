import pytest


@pytest.mark.integration
class TestChatEndpoint:
    def test_chat_completions(self, cb_client):
        payload = {
            "model": "imperator",
            "messages": [
                {"role": "system", "content": "You are a test assistant."},
                {"role": "user", "content": "Say hello."},
            ],
            "stream": False,
        }
        resp = cb_client.post("/v1/chat/completions", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert "choices" in body
        assert body["choices"][0]["message"]["content"]
