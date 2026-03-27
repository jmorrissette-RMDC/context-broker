import os
import pytest
from pathlib import Path
from unittest.mock import patch
from app.prompt_loader import load_prompt, async_load_prompt, _prompt_cache

@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the prompt cache before each test."""
    _prompt_cache.clear()

@pytest.fixture
def temp_prompts(tmp_path):
    """Create a temporary prompts directory with some test files."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    
    (prompts_dir / "test_prompt.md").write_text("Test Content", encoding="utf-8")
    (prompts_dir / "update_prompt.md").write_text("Initial Content", encoding="utf-8")
    
    with patch("app.prompt_loader.PROMPTS_DIR", prompts_dir):
        yield prompts_dir

def test_load_prompt_success(temp_prompts):
    """Test successful synchronous prompt loading."""
    content = load_prompt("test_prompt")
    assert content == "Test Content"
    assert "test_prompt" in _prompt_cache

def test_load_prompt_not_found(temp_prompts):
    """Test that RuntimeError is raised when prompt is missing."""
    with pytest.raises(RuntimeError, match="Prompt template not found"):
        load_prompt("non_existent")

def test_load_prompt_caching(temp_prompts):
    """Test that the cache is used and respects mtime."""
    path = temp_prompts / "update_prompt.md"
    
    # First load
    content1 = load_prompt("update_prompt")
    assert content1 == "Initial Content"
    
    # Update file but keep mtime same (simulated or fast)
    path.write_text("New Content", encoding="utf-8")
    
    # Should still be Initial Content due to cached mtime
    # We force mtime update for the next part
    os.utime(path, (os.stat(path).st_atime, os.stat(path).st_mtime - 10))
    content2 = load_prompt("update_prompt")
    # This might still be "Initial Content" if we didn't clear cache,
    # but the test above cleared it. Wait, the mtime check logic:
    # if cached[0] == current_mtime: return cached[1]
    
    # Let's be precise
    _prompt_cache["update_prompt"] = (os.stat(path).st_mtime, "Cached Content")
    assert load_prompt("update_prompt") == "Cached Content"
    
    # Update mtime
    os.utime(path, (os.stat(path).st_atime, os.stat(path).st_mtime + 100))
    assert load_prompt("update_prompt") == "New Content"

@pytest.mark.asyncio
async def test_async_load_prompt_success(temp_prompts):
    """Test successful asynchronous prompt loading."""
    content = await async_load_prompt("test_prompt")
    assert content == "Test Content"
    assert "test_prompt" in _prompt_cache

@pytest.mark.asyncio
async def test_async_load_prompt_caching(temp_prompts):
    """Test async caching logic."""
    path = temp_prompts / "test_prompt.md"
    _prompt_cache["test_prompt"] = (os.stat(path).st_mtime, "Async Cached")
    
    content = await async_load_prompt("test_prompt")
    assert content == "Async Cached"
