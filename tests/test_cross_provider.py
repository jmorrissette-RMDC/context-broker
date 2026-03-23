"""
Cross-provider direct API verification (Phase 1 of T-12).

Quick sanity checks that each cloud provider's endpoint, model, and auth
pattern work before running the full suite through the Context Broker.
Local providers (Ollama, Infinity) are already verified by the main suite.

These tests read API keys from Z:/credentials/model-providers.json.
They are skipped if credentials are not available.

Phase 2 (full suite per provider) is done by swapping config.yml on irina
and running the existing 268-test suite. See plan Step E for the 6-run matrix.
"""

import json
import pathlib

import httpx
import pytest

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

CREDS_PATH = pathlib.Path("Z:/credentials/model-providers.json")

try:
    _providers = json.loads(CREDS_PATH.read_text())["providers"]
except (FileNotFoundError, KeyError):
    _providers = {}


def _openai_key() -> str | None:
    return _providers.get("gpt-5.4", {}).get("api_key")


def _anthropic_key() -> str | None:
    return _providers.get("sonnet-4.6", {}).get("api_key")


def _google_key() -> str | None:
    return _providers.get("gemini-flash", {}).get("api_key")


def _xai_key() -> str | None:
    return _providers.get("grok-4.2", {}).get("api_key")


def _together_key() -> str | None:
    return _providers.get("deepseek-r1", {}).get("api_key")


# ===================================================================
# LLM Direct Checks
# ===================================================================


class TestLLMDirect:
    """Verify each cloud LLM provider responds to a chat completion."""

    @pytest.mark.skipif(not _together_key(), reason="No Together API key")
    def test_together_llm(self):
        with httpx.Client(timeout=30.0) as c:
            resp = c.post(
                "https://api.together.xyz/v1/chat/completions",
                headers={"Authorization": f"Bearer {_together_key()}"},
                json={
                    "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "max_tokens": 5,
                },
            )
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"]

    @pytest.mark.skipif(not _google_key(), reason="No Google API key")
    def test_google_llm(self):
        with httpx.Client(timeout=30.0) as c:
            resp = c.post(
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                headers={"Authorization": f"Bearer {_google_key()}"},
                json={
                    "model": "gemini-2.5-flash",
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "max_tokens": 5,
                },
            )
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"]

    @pytest.mark.skipif(not _openai_key(), reason="No OpenAI API key")
    def test_openai_llm(self):
        with httpx.Client(timeout=30.0) as c:
            resp = c.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {_openai_key()}"},
                json={
                    "model": "gpt-4.1-nano",
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "max_tokens": 5,
                },
            )
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"]

    @pytest.mark.skipif(not _anthropic_key(), reason="No Anthropic API key")
    def test_anthropic_llm(self):
        """Anthropic uses native API, not OpenAI-compatible."""
        with httpx.Client(timeout=30.0) as c:
            resp = c.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": _anthropic_key(),
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "Say OK"}],
                },
            )
        assert resp.status_code == 200
        assert resp.json()["content"][0]["text"]

    @pytest.mark.skipif(not _xai_key(), reason="No xAI API key")
    def test_xai_llm(self):
        with httpx.Client(timeout=30.0) as c:
            resp = c.post(
                "https://api.x.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {_xai_key()}"},
                json={
                    "model": "grok-3-mini-fast",
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "max_tokens": 5,
                },
            )
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"]


# ===================================================================
# Embedding Direct Checks
# ===================================================================


class TestEmbeddingDirect:
    """Verify each cloud embedding provider returns vectors."""

    @pytest.mark.skipif(not _together_key(), reason="No Together API key")
    def test_together_embedding(self):
        with httpx.Client(timeout=30.0) as c:
            resp = c.post(
                "https://api.together.xyz/v1/embeddings",
                headers={"Authorization": f"Bearer {_together_key()}"},
                json={
                    "model": "intfloat/multilingual-e5-large-instruct",
                    "input": "test embedding",
                },
            )
        assert resp.status_code == 200
        emb = resp.json()["data"][0]["embedding"]
        assert len(emb) > 0
        assert isinstance(emb[0], float)

    @pytest.mark.skipif(not _google_key(), reason="No Google API key")
    def test_google_embedding(self):
        with httpx.Client(timeout=30.0) as c:
            resp = c.post(
                "https://generativelanguage.googleapis.com/v1beta/openai/embeddings",
                headers={"Authorization": f"Bearer {_google_key()}"},
                json={
                    "model": "gemini-embedding-001",
                    "input": "test embedding",
                },
            )
        assert resp.status_code == 200
        emb = resp.json()["data"][0]["embedding"]
        assert len(emb) > 0

    @pytest.mark.skipif(not _openai_key(), reason="No OpenAI API key")
    def test_openai_embedding(self):
        with httpx.Client(timeout=30.0) as c:
            resp = c.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {_openai_key()}"},
                json={
                    "model": "text-embedding-3-small",
                    "input": "test embedding",
                },
            )
        assert resp.status_code == 200
        emb = resp.json()["data"][0]["embedding"]
        assert len(emb) > 0


# ===================================================================
# Reranking Direct Checks
# ===================================================================


class TestRerankDirect:
    """Verify cloud reranking providers return scored results."""

    _QUERY = "What is context engineering?"
    _DOCS = [
        "Context engineering is building purpose-built context windows.",
        "The weather is nice today.",
        "LangGraph uses StateGraphs for agent logic.",
    ]

    @pytest.mark.skip(
        reason="Together rerank models require dedicated endpoints (non-serverless)"
    )
    def test_together_rerank(self):
        """Together AI reranking — requires dedicated endpoint, not available on free tier."""
        pass

    @pytest.mark.skip(reason="No Cohere API key configured")
    def test_cohere_rerank(self):
        """Placeholder — needs Cohere API key."""
        pass

    @pytest.mark.skip(reason="No Jina API key configured")
    def test_jina_rerank(self):
        """Placeholder — needs Jina API key."""
        pass
