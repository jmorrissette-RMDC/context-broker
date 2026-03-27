import pytest
import uuid
import json
from unittest.mock import patch, AsyncMock, MagicMock
from context_broker_te.tools.operational import store_domain_info, search_domain_info

@pytest.fixture
def mock_pool():
    """Create a mock connection pool for safe logic testing."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock()
    pool.execute = AsyncMock()
    return pool

@pytest.fixture(autouse=True)
def mock_infra(mock_pool):
    """Patch get_pg_pool and embeddings model."""
    with patch("context_broker_te.tools.operational.get_pg_pool", return_value=mock_pool):
        # Mock the embeddings model to return a dummy vector
        mock_emb = AsyncMock()
        mock_emb.aembed_query = AsyncMock(return_value=[0.1] * 1536) # standard dimension
        
        with patch("app.config.get_embeddings_model", return_value=mock_emb), \
             patch("app.config.async_load_config", AsyncMock(return_value={})):
            yield

@pytest.mark.asyncio
async def test_domain_info_lifecycle_mocked(mock_pool):
    """Test logic for storing and searching domain info using mocks."""
    content = f"Test learned fact {uuid.uuid4().hex}"
    
    # Setup mock return for store
    mock_pool.execute.return_value = "INSERT 1"
    
    # 1. Store
    res_store = await store_domain_info.ainvoke({"content": content, "source": "gemini-test"})
    assert "Stored domain information" in res_store
    
    # Setup mock return for search
    from datetime import datetime
    mock_pool.fetch.return_value = [
        {"content": content, "source": "gemini-test", "created_at": datetime.now(), "similarity": 1.0}
    ]
    
    # 2. Search
    res_search = await search_domain_info.ainvoke({"query": "learned fact"})
    assert content in res_search
    assert "gemini-test" in res_search
    assert "[1.0]" in res_search
