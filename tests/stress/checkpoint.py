"""Checkpoint save/resume for long-running stress tests.

Saves progress to a JSON file so the test can resume after interruption.
"""

import json
import logging
import time
from pathlib import Path

_log = logging.getLogger("stress.checkpoint")

DEFAULT_PATH = Path(__file__).parent / "checkpoint.json"


class Checkpoint:
    """Manages stress test progress persistence."""

    def __init__(self, path: str | Path = DEFAULT_PATH, save_interval: int = 60):
        self.path = Path(path)
        self.save_interval = save_interval
        self._last_save = 0.0
        self._data: dict = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
                _log.info("Loaded checkpoint: %s", self.path)
            except (json.JSONDecodeError, OSError) as exc:
                _log.warning("Failed to load checkpoint: %s", exc)
                self._data = {}

    def save(self, force: bool = False):
        """Save checkpoint if interval has elapsed (or force=True)."""
        now = time.time()
        if not force and (now - self._last_save) < self.save_interval:
            return
        try:
            self.path.write_text(
                json.dumps(self._data, indent=2, default=str),
                encoding="utf-8",
            )
            self._last_save = now
        except OSError as exc:
            _log.warning("Failed to save checkpoint: %s", exc)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value

    @property
    def scenario(self) -> str | None:
        return self._data.get("current_scenario")

    @scenario.setter
    def scenario(self, value: str):
        self._data["current_scenario"] = value
        self.save(force=True)

    @property
    def conversation_id(self) -> str | None:
        return self._data.get("conversation_id")

    @conversation_id.setter
    def conversation_id(self, value: str):
        self._data["conversation_id"] = value

    @property
    def messages_loaded(self) -> int:
        return self._data.get("messages_loaded", 0)

    @messages_loaded.setter
    def messages_loaded(self, value: int):
        self._data["messages_loaded"] = value

    @property
    def compaction_cycles(self) -> int:
        return self._data.get("compaction_cycles", 0)

    @compaction_cycles.setter
    def compaction_cycles(self, value: int):
        self._data["compaction_cycles"] = value

    @property
    def live_turns(self) -> int:
        return self._data.get("live_turns", 0)

    @live_turns.setter
    def live_turns(self, value: int):
        self._data["live_turns"] = value

    def add_cost(self, category: str, amount: float):
        costs = self._data.setdefault("costs", {})
        costs[category] = costs.get(category, 0.0) + amount

    @property
    def total_cost(self) -> float:
        return sum(self._data.get("costs", {}).values())

    def add_quality_checkpoint(self, cycle: int, budget: int, rating: str, reasoning: str):
        checkpoints = self._data.setdefault("quality_checkpoints", [])
        checkpoints.append({
            "cycle": cycle,
            "budget": budget,
            "rating": rating,
            "reasoning": reasoning[:200],
            "timestamp": time.time(),
        })

    def clear(self):
        self._data = {}
        if self.path.exists():
            self.path.unlink()
