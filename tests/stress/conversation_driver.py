"""Drive the user side of a conversation using a cheap LLM.

Calls the Gemini Flash Lite API to generate realistic user messages
based on the Imperator's last response. The goal is not quality
reasoning — just keeping a coherent conversation going so the
compaction pipeline has realistic data to work with.
"""

import logging
import os

import httpx

_log = logging.getLogger("stress.conversation_driver")

DEFAULT_MODEL = "gemini-2.5-flash-lite"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


class ConversationDriver:
    """Generates user-side messages for stress test conversations."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        api_key_env: str = "GOOGLE_API_KEY",
        system_prompt: str | None = None,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = os.environ.get(api_key_env, "")
        if not self.api_key:
            raise RuntimeError(
                f"API key not found in env var {api_key_env}. "
                f"Set it before running the stress test."
            )
        self.system_prompt = system_prompt or (
            "You are a curious user having a long conversation with an AI assistant. "
            "Your role is to keep the conversation going naturally. Ask follow-up questions, "
            "introduce related topics, request clarifications, and occasionally change "
            "subjects. Keep your messages between 1-4 sentences. Be conversational and "
            "natural — you are a real person, not a test script. "
            "Topics you're interested in: software architecture, AI systems, distributed "
            "computing, project management, and technical design patterns."
        )
        self._history: list[dict] = []
        self._client = httpx.Client(timeout=60.0)

    def generate_message(self, assistant_response: str) -> str:
        """Generate the next user message given the assistant's last response."""
        # Keep a rolling window of recent exchanges for context
        self._history.append({"role": "assistant", "content": assistant_response})

        # Trim history to last 10 exchanges (20 messages) to keep costs down
        if len(self._history) > 20:
            self._history = self._history[-20:]

        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self._history)
        messages.append({
            "role": "user",
            "content": "Generate your next message in this conversation. "
            "Reply ONLY with the message itself, no meta-commentary.",
        })

        try:
            resp = self._client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.9,
                    "max_tokens": 300,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            self._history.append({"role": "user", "content": content})
            return content
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            _log.warning("Driver LLM call failed: %s", exc)
            # Fallback: generic follow-up
            fallback = "That's interesting. Can you tell me more about that?"
            self._history.append({"role": "user", "content": fallback})
            return fallback

    def generate_initial_message(self) -> str:
        """Generate the first user message to start a conversation."""
        try:
            resp = self._client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {
                            "role": "user",
                            "content": "Generate an opening message to start a "
                            "conversation with an AI assistant about software "
                            "architecture or AI systems. Reply ONLY with the message.",
                        },
                    ],
                    "temperature": 1.0,
                    "max_tokens": 200,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            self._history.append({"role": "user", "content": content})
            return content
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            _log.warning("Driver initial message failed: %s", exc)
            fallback = "Tell me about the architectural patterns you use for distributed AI systems."
            self._history.append({"role": "user", "content": fallback})
            return fallback

    def reset_history(self):
        """Clear conversation history for topic pivot.

        The talker forgets everything, but the agent's context continues.
        This creates semantic islands that stress compaction at boundaries.
        """
        self._history.clear()

    def generate_pivot_message(self) -> str:
        """Generate a message that asks the agent to pick a completely new topic.

        Used during stress testing to create diverse semantic islands.
        """
        try:
            resp = self._client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {
                            "role": "user",
                            "content": "Generate a message that asks the AI to pick a "
                            "completely different topic from a vastly different field "
                            "and time period. For example, if we were discussing "
                            "software, switch to ancient history or marine biology. "
                            "Reply ONLY with the message itself.",
                        },
                    ],
                    "temperature": 1.0,
                    "max_tokens": 200,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            self._history.append({"role": "user", "content": content})
            return content
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            _log.warning("Driver pivot message failed: %s", exc)
            fallback = "Let's change topics entirely. Pick something from a completely different field and time period and tell me about it in detail."
            self._history.append({"role": "user", "content": fallback})
            return fallback

    def close(self):
        self._client.close()
