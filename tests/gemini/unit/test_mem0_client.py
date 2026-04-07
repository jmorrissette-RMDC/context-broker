import pytest
import os
from unittest.mock import AsyncMock, patch, MagicMock
from context_broker_ae.memory.mem0_client import (
    _compute_config_hash, reset_mem0_client, get_mem0_client,
    _apply_mem0_patches, _build_mem0_instance, _get_embedding_dims
)

def test_compute_config_hash():
    config1 = {"llm": {"model": "A"}, "embeddings": {"model": "B"}, "vector_store": {"host": "localhost"}}
    config2 = {"llm": {"model": "A"}, "embeddings": {"model": "B"}, "vector_store": {"host": "remote"}}
    hash1 = _compute_config_hash(config1)
    hash2 = _compute_config_hash(config2)
    assert hash1 != hash2

def test_reset_mem0_client():
    from context_broker_ae.memory import mem0_client
    mem0_client._mem0_instance = "instance"
    mem0_client._mem0_config_hash = "hash"
    reset_mem0_client()
    assert mem0_client._mem0_instance is None
    assert mem0_client._mem0_config_hash == ""

@pytest.mark.asyncio
async def test_get_mem0_client_success():
    config = {"llm": {"model": "A"}, "embeddings": {"model": "B"}, "vector_store": {"host": "localhost"}}
    with patch("context_broker_ae.memory.mem0_client._build_mem0_instance", return_value="mock_mem0"):
        reset_mem0_client()
        client = await get_mem0_client(config)
        assert client == "mock_mem0"
        
        # Subsequent call should return cached instance
        client2 = await get_mem0_client(config)
        assert client2 == "mock_mem0"

@pytest.mark.asyncio
async def test_get_mem0_client_config_change():
    config1 = {"llm": {"model": "A"}, "embeddings": {"model": "B"}}
    config2 = {"llm": {"model": "C"}, "embeddings": {"model": "B"}}
    with patch("context_broker_ae.memory.mem0_client._build_mem0_instance", side_effect=["mock_mem0_1", "mock_mem0_2"]):
        reset_mem0_client()
        client1 = await get_mem0_client(config1)
        assert client1 == "mock_mem0_1"
        
        client2 = await get_mem0_client(config2)
        assert client2 == "mock_mem0_2"

@pytest.mark.asyncio
async def test_get_mem0_client_error():
    config = {"llm": {"model": "A"}, "embeddings": {"model": "B"}}
    with patch("context_broker_ae.memory.mem0_client._build_mem0_instance", side_effect=ValueError("Init failed")):
        reset_mem0_client()
        client = await get_mem0_client(config)
        assert client is None

def test_get_embedding_dims():
    assert _get_embedding_dims({}, {"embedding_dims": 768}) == 768
    assert _get_embedding_dims({"embedding_dims": 1536}, {}) == 1536
    
    with pytest.raises(ValueError):
        _get_embedding_dims({}, {})

def test_apply_mem0_patches():
    # Reset internal state to ensure patch is run
    import context_broker_ae.memory.mem0_client as m_client
    m_client._patches_applied = False
    
    # We can't easily mock the internals of mem0 if it's not installed,
    # but we can verify it doesn't crash on ImportError.
    _apply_mem0_patches()
    assert m_client._patches_applied is True
