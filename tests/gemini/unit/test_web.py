import pytest
import sys
from unittest.mock import AsyncMock, patch, MagicMock

# Mock external modules before importing the tool
sys.modules['duckduckgo_search'] = MagicMock()
sys.modules['crawl4ai'] = MagicMock()

from context_broker_te.tools.web import web_search, web_read

@pytest.mark.asyncio
async def test_web_search_success():
    mock_results = [
        {"title": "Result 1", "href": "http://res1.com", "body": "Body 1"},
        {"title": "Result 2", "href": "http://res2.com", "body": "Body 2"}
    ]
    with patch("duckduckgo_search.DDGS") as MockDDGS:
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = mock_results
        MockDDGS.return_value = mock_ddgs_instance

        result = await web_search.ainvoke({"query": "test query"})
        assert "Found 2 results:" in result
        assert "- **Result 1**" in result
        assert "http://res1.com" in result
        assert "Body 1" in result

@pytest.mark.asyncio
async def test_web_search_no_results():
    with patch("duckduckgo_search.DDGS") as MockDDGS:
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = []
        MockDDGS.return_value = mock_ddgs_instance

        result = await web_search.ainvoke({"query": "test query"})
        assert "No search results found." in result

@pytest.mark.asyncio
async def test_web_search_error():
    with patch("duckduckgo_search.DDGS") as MockDDGS:
        MockDDGS.side_effect = RuntimeError("Search failed")
        result = await web_search.ainvoke({"query": "test query"})
        assert "Search error: Search failed" in result

@pytest.mark.asyncio
async def test_web_read_html_crawl4ai_success():
    html_content = "<html><body><h1>Test</h1><p>Content</p></body></html>"
    with patch("httpx.AsyncClient") as MockClient, \
         patch("crawl4ai.AsyncWebCrawler") as MockCrawler:
        
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.text = html_content
        
        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_resp
        MockClient.return_value.__aenter__.return_value = mock_client_instance

        mock_crawler_result = MagicMock()
        mock_crawler_result.markdown = "# Test\nContent"
        
        mock_crawler_instance = AsyncMock()
        mock_crawler_instance.arun.return_value = mock_crawler_result
        MockCrawler.return_value.__aenter__.return_value = mock_crawler_instance

        result = await web_read.ainvoke({"url": "http://test.com"})
        assert result == "# Test\nContent"

@pytest.mark.asyncio
async def test_web_read_html_fallback_success():
    html_content = "<html><head><script>alert(1)</script><style>body { color: red; }</style></head><body><h1>Test</h1><p>Content</p></body></html>"
    with patch("httpx.AsyncClient") as MockClient, \
         patch("crawl4ai.AsyncWebCrawler", side_effect=ImportError("crawl4ai not installed")):
        
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.text = html_content
        
        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_resp
        MockClient.return_value.__aenter__.return_value = mock_client_instance

        result = await web_read.ainvoke({"url": "http://test.com"})
        assert result == "Test Content" # tags and whitespace stripped

@pytest.mark.asyncio
async def test_web_read_plaintext_success():
    plain_content = "Just plain text content."
    with patch("httpx.AsyncClient") as MockClient:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {"content-type": "text/plain"}
        mock_resp.text = plain_content
        
        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_resp
        MockClient.return_value.__aenter__.return_value = mock_client_instance

        result = await web_read.ainvoke({"url": "http://test.com"})
        assert result == "Just plain text content."

@pytest.mark.asyncio
async def test_web_read_error():
    import httpx
    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__.return_value.get.side_effect = httpx.HTTPError("Read failed")
        result = await web_read.ainvoke({"url": "http://test.com"})
        assert "Error reading http://test.com: Read failed" in result
