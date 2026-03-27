"""
Imperator persistent state manager.

Manages the Imperator's conversation_id across restarts.
Reads/writes /data/imperator_state.json.

On first boot: creates a new conversation, writes the ID.
On subsequent boots: reads the ID and verifies it exists in the DB.
If missing: creates a new conversation.

Context windows are created automatically by get_context (D-03) —
the state manager only tracks the conversation.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from app.database import get_pg_pool

_log = logging.getLogger("context_broker.imperator.state_manager")

IMPERATOR_STATE_FILE = Path("/data/imperator_state.json")


class ImperatorStateManager:
    """Manages the Imperator's persistent conversation state."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._conversation_id: Optional[uuid.UUID] = None

    async def initialize(self) -> None:
        """Initialize the Imperator's conversation state.

        Reads the state file if it exists and verifies the conversation.
        Creates a new conversation if needed.
        """
        saved_conv_id = self._read_state_file()

        if saved_conv_id is not None:
            if await self._conversation_exists(saved_conv_id):
                self._conversation_id = saved_conv_id
                _log.info("Imperator: resuming conversation %s", self._conversation_id)
                return
            else:
                _log.warning(
                    "Imperator: conversation %s no longer exists, creating new",
                    saved_conv_id,
                )

        # Create new conversation
        self._conversation_id = await self._create_imperator_conversation()
        self._write_state_file(self._conversation_id)
        _log.info("Imperator: created new conversation %s", self._conversation_id)

    async def get_conversation_id(self) -> Optional[uuid.UUID]:
        """Return the Imperator's current conversation ID.

        Verifies the conversation still exists in the DB (PG-43).
        If it was deleted at runtime, creates a new one transparently.
        """
        if self._conversation_id is not None:
            if not await self._conversation_exists(self._conversation_id):
                _log.warning(
                    "Imperator: conversation %s deleted at runtime, creating new",
                    self._conversation_id,
                )
                self._conversation_id = await self._create_imperator_conversation()
                self._write_state_file(self._conversation_id)
                _log.info(
                    "Imperator: created replacement conversation %s",
                    self._conversation_id,
                )
        return self._conversation_id

    async def get_context_window_id(self) -> Optional[uuid.UUID]:
        """Backward compatibility — returns conversation_id.

        The chat route and tool dispatch use this. With D-03, context windows
        are created automatically by get_context. This returns the conversation_id
        which is used as the MemorySaver thread_id.

        Delegates to get_conversation_id() for PG-43 runtime recovery.
        """
        return await self.get_conversation_id()

    def _read_state_file(self) -> Optional[uuid.UUID]:
        """Read the conversation ID from the state file."""
        if not IMPERATOR_STATE_FILE.exists():
            return None

        try:
            with open(IMPERATOR_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)

            conv_str = data.get("conversation_id")
            if conv_str:
                return uuid.UUID(conv_str)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            _log.warning("Failed to read imperator state file: %s", exc)

        return None

    def _write_state_file(self, conversation_id: uuid.UUID) -> None:
        """Write the conversation ID to the state file."""
        try:
            IMPERATOR_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(IMPERATOR_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"conversation_id": str(conversation_id)}, f)
        except OSError as exc:
            _log.error("Failed to write imperator state file: %s", exc)

    async def _conversation_exists(self, conversation_id: uuid.UUID) -> bool:
        """Check if a conversation exists in PostgreSQL."""
        pool = get_pg_pool()
        row = await pool.fetchrow(
            "SELECT id FROM conversations WHERE id = $1", conversation_id
        )
        return row is not None

    async def _create_imperator_conversation(self) -> uuid.UUID:
        """Create a new conversation for the Imperator.

        Uses direct SQL because the state_manager runs during startup
        before flows are compiled (G5-17).
        """
        pool = get_pg_pool()
        new_id = uuid.uuid4()
        # R7-m27: Include flow_id and user_id in the INSERT
        await pool.execute(
            "INSERT INTO conversations (id, title, flow_id, user_id) VALUES ($1, $2, $3, $4)",
            new_id,
            "Imperator — System Conversation",
            "imperator",
            "system",
        )
        return new_id
