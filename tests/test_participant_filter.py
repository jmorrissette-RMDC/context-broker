"""Tests for participant filter and conversation listing (§4.15).

Unit tests for the ListConversationsInput model.
"""

import pytest
from pydantic import ValidationError

from app.models import ListConversationsInput


class TestListConversationsInput:
    """T-15.x: Input validation for conv_list_conversations."""

    def test_valid_no_participant(self):
        inp = ListConversationsInput()
        assert inp.participant is None
        assert inp.limit == 50
        assert inp.offset == 0

    def test_valid_with_participant(self):
        inp = ListConversationsInput(participant="context-broker-langgraph")
        assert inp.participant == "context-broker-langgraph"

    def test_limit_bounds(self):
        with pytest.raises(ValidationError):
            ListConversationsInput(limit=0)
        with pytest.raises(ValidationError):
            ListConversationsInput(limit=501)

    def test_offset_bounds(self):
        with pytest.raises(ValidationError):
            ListConversationsInput(offset=-1)

    def test_participant_max_length(self):
        with pytest.raises(ValidationError):
            ListConversationsInput(participant="x" * 256)
