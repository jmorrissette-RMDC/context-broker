"""
Imperator persistent state manager.

Manages the Imperator's conversation_id and context_window_id across restarts.
Reads/writes /data/imperator_state.json.

On first boot: creates a new conversation and context window, writes both IDs.
On subsequent boots: reads the IDs and verifies both exist in the DB.
If either is missing: recreates the missing resource.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from app.config import get_build_type_config
from app.database import get_pg_pool
from app.token_budget import resolve_token_budget

_log = logging.getLogger("context_broker.imperator.state_manager")

IMPERATOR_STATE_FILE = Path("/data/imperator_state.json")


class ImperatorStateManager:
    """Manages the Imperator's persistent conversation and context window state."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._conversation_id: Optional[uuid.UUID] = None
        self._context_window_id: Optional[uuid.UUID] = None

    async def initialize(self) -> None:
        """Initialize the Imperator's conversation and context window state.

        Reads the state file if it exists and verifies both the conversation
        and context window. Creates missing resources as needed.
        """
        saved_conv_id, saved_cw_id = self._read_state_file()

        conv_exists = (
            await self._conversation_exists(saved_conv_id)
            if saved_conv_id is not None
            else False
        )
        cw_exists = (
            await self._context_window_exists(saved_cw_id)
            if saved_cw_id is not None
            else False
        )

        if conv_exists and cw_exists:
            self._conversation_id = saved_conv_id
            self._context_window_id = saved_cw_id
            _log.info(
                "Imperator: resuming conversation %s, context window %s",
                self._conversation_id,
                self._context_window_id,
            )
            return

        # Conversation exists but window doesn't — recreate window
        if conv_exists and not cw_exists:
            self._conversation_id = saved_conv_id
            if saved_cw_id is not None:
                _log.warning(
                    "Imperator: context window %s no longer exists, creating new",
                    saved_cw_id,
                )
            self._context_window_id = await self._create_imperator_context_window(
                self._conversation_id
            )
            self._write_state_file(self._conversation_id, self._context_window_id)
            _log.info(
                "Imperator: created new context window %s for existing conversation %s",
                self._context_window_id,
                self._conversation_id,
            )
            return

        # Window exists but conversation doesn't (shouldn't happen due to FK,
        # but handle defensively) — or neither exists. Create both.
        if saved_conv_id is not None and not conv_exists:
            _log.warning(
                "Imperator: conversation %s no longer exists, creating new",
                saved_conv_id,
            )

        self._conversation_id = await self._create_imperator_conversation()
        self._context_window_id = await self._create_imperator_context_window(
            self._conversation_id
        )
        self._write_state_file(self._conversation_id, self._context_window_id)
        _log.info(
            "Imperator: created new conversation %s and context window %s",
            self._conversation_id,
            self._context_window_id,
        )

    async def get_conversation_id(self) -> Optional[uuid.UUID]:
        """Return the Imperator's current conversation ID."""
        return self._conversation_id

    async def get_context_window_id(self) -> Optional[uuid.UUID]:
        """Return the Imperator's current context window ID."""
        return self._context_window_id

    def _read_state_file(
        self,
    ) -> tuple[Optional[uuid.UUID], Optional[uuid.UUID]]:
        """Read the conversation and context window IDs from the state file.

        Returns (conversation_id, context_window_id). Either or both may be
        None if the file doesn't exist or values are missing/invalid.
        """
        if not IMPERATOR_STATE_FILE.exists():
            return None, None

        try:
            with open(IMPERATOR_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)

            conv_id = None
            cw_id = None

            conv_str = data.get("conversation_id")
            if conv_str:
                conv_id = uuid.UUID(conv_str)

            cw_str = data.get("context_window_id")
            if cw_str:
                cw_id = uuid.UUID(cw_str)

            return conv_id, cw_id
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            _log.warning("Failed to read imperator state file: %s", exc)

        return None, None

    def _write_state_file(
        self, conversation_id: uuid.UUID, context_window_id: uuid.UUID
    ) -> None:
        """Write the conversation and context window IDs to the state file."""
        try:
            IMPERATOR_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(IMPERATOR_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "conversation_id": str(conversation_id),
                        "context_window_id": str(context_window_id),
                    },
                    f,
                )
        except OSError as exc:
            _log.error("Failed to write imperator state file: %s", exc)

    async def _conversation_exists(self, conversation_id: uuid.UUID) -> bool:
        """Check if a conversation exists in PostgreSQL.

        Raises on DB errors so that callers (e.g. initialize) fail fast
        rather than silently treating a DB outage as 'conversation missing'
        (REQ-001 §7.4).
        """
        pool = get_pg_pool()
        row = await pool.fetchrow(
            "SELECT id FROM conversations WHERE id = $1", conversation_id
        )
        return row is not None

    async def _context_window_exists(self, context_window_id: uuid.UUID) -> bool:
        """Check if a context window exists in PostgreSQL.

        Raises on DB errors for the same reason as _conversation_exists.
        """
        pool = get_pg_pool()
        row = await pool.fetchrow(
            "SELECT id FROM context_windows WHERE id = $1", context_window_id
        )
        return row is not None

    async def _create_imperator_conversation(self) -> uuid.UUID:
        """Create a new conversation for the Imperator.

        G5-17: This uses direct SQL instead of the conversation flow
        (build_conversation_flow) because the state_manager runs during
        application startup — before flows are compiled and before the
        full application context is available. Importing and compiling
        the conversation flow here would create a circular dependency
        and violate the startup ordering guarantees. Direct SQL is
        acceptable for this single bootstrap operation.
        """
        pool = get_pg_pool()
        new_id = uuid.uuid4()
        await pool.execute(
            "INSERT INTO conversations (id, title) VALUES ($1, $2)",
            new_id,
            "Imperator — System Conversation",
        )
        return new_id

    async def _create_imperator_context_window(
        self, conversation_id: uuid.UUID
    ) -> uuid.UUID:
        """Create a new context window for the Imperator.

        Uses the build_type from config["imperator"]["build_type"] and resolves
        the token budget using the standard resolve_token_budget function.

        G5-17: Direct SQL for the same startup-ordering reasons as
        _create_imperator_conversation.
        """
        imperator_config = self._config.get("imperator", {})
        build_type_name = imperator_config.get("build_type", "standard-tiered")
        participant_id = imperator_config.get("participant_id", "imperator")

        build_type_config = get_build_type_config(self._config, build_type_name)

        # Resolve token budget using the imperator's max_context_tokens if set,
        # falling back to the build type's own setting.
        imperator_max_tokens = imperator_config.get("max_context_tokens")
        caller_override = (
            imperator_max_tokens if isinstance(imperator_max_tokens, int) else None
        )
        token_budget = await resolve_token_budget(
            config=self._config,
            build_type_config=build_type_config,
            caller_override=caller_override,
        )

        pool = get_pg_pool()

        # G5-08: Idempotent creation via ON CONFLICT on the unique constraint
        # (conversation_id, participant_id, build_type).
        row = await pool.fetchrow(
            """
            INSERT INTO context_windows
                (conversation_id, participant_id, build_type, max_token_budget)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (conversation_id, participant_id, build_type) DO NOTHING
            RETURNING id
            """,
            conversation_id,
            participant_id,
            build_type_name,
            token_budget,
        )

        if row is None:
            # Already exists — look up the existing window
            existing = await pool.fetchrow(
                """
                SELECT id FROM context_windows
                WHERE conversation_id = $1 AND participant_id = $2 AND build_type = $3
                """,
                conversation_id,
                participant_id,
                build_type_name,
            )
            return existing["id"]

        return row["id"]
