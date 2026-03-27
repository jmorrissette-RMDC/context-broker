import pytest
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from context_broker_te.tools.alerting import (
    add_alert_instruction, list_alert_instructions,
    update_alert_instruction, delete_alert_instruction, _embed_description
)

@pytest.mark.asyncio
async def test_add_alert_instruction_success():
    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(return_value={"id": 123})
    
    with patch("context_broker_te.tools.alerting.get_pg_pool", return_value=mock_pool), \
         patch("context_broker_te.tools.alerting._embed_description", AsyncMock(return_value=[0.1, 0.2])):
        
        channels = json.dumps([{"type": "ntfy", "url": "http://ntfy.sh/test"}])
        result = await add_alert_instruction.ainvoke({
            "description": "test alert",
            "instruction": "format this alert",
            "channels": channels
        })
        assert "Alert instruction added (id=123)" in result
        assert "Channels: ['ntfy']" in result

@pytest.mark.asyncio
async def test_add_alert_instruction_invalid_json():
    result = await add_alert_instruction.ainvoke({
        "description": "test", "instruction": "test", "channels": "invalid json"
    })
    assert "Error: invalid JSON in channels" in result

@pytest.mark.asyncio
async def test_list_alert_instructions_success():
    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[
        {"id": 1, "description": "desc1", "channels": '[{"type": "slack"}]', "created_at": datetime.now()},
        {"id": 2, "description": "desc2", "channels": [{"type": "ntfy"}], "created_at": datetime.now()}
    ])
    
    with patch("context_broker_te.tools.alerting.get_pg_pool", return_value=mock_pool):
        result = await list_alert_instructions.ainvoke({})
        assert "2 alert instruction(s)" in result
        assert "[1] desc1" in result
        assert "[2] desc2" in result

@pytest.mark.asyncio
async def test_update_alert_instruction_success():
    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(return_value={"id": 1})
    mock_pool.execute = AsyncMock()
    
    with patch("context_broker_te.tools.alerting.get_pg_pool", return_value=mock_pool), \
         patch("context_broker_te.tools.alerting._embed_description", AsyncMock(return_value=[0.1])):
        
        result = await update_alert_instruction.ainvoke({
            "instruction_id": 1,
            "description": "new desc"
        })
        assert "Instruction 1 updated." in result
        mock_pool.execute.assert_called_once()

@pytest.mark.asyncio
async def test_delete_alert_instruction_success():
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock(return_value="DELETE 1")
    
    with patch("context_broker_te.tools.alerting.get_pg_pool", return_value=mock_pool):
        result = await delete_alert_instruction.ainvoke({"instruction_id": 1})
        assert "Instruction 1 deleted." in result

@pytest.mark.asyncio
async def test_embed_description_success():
    mock_config = {}
    mock_model = MagicMock()
    mock_model.aembed_documents = AsyncMock(return_value=[[0.1, 0.2]])
    
    with patch("app.config.async_load_config", AsyncMock(return_value=mock_config)), \
         patch("app.config.get_embeddings_model", return_value=mock_model):
        result = await _embed_description("test")
        assert result == [0.1, 0.2]

@pytest.mark.asyncio
async def test_embed_description_fail():
    with patch("app.config.async_load_config", side_effect=Exception("Fail")):
        with pytest.raises(Exception, match="Fail"):
            await _embed_description("test")
