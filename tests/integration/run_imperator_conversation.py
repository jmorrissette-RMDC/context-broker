"""
Phase 2: Imperator multi-turn conversation test.

Feeds scripted prompts through the Imperator via /v1/chat/completions,
verifies tool usage and response coherence.

Usage:
    python tests/integration/run_imperator_conversation.py
"""

import json
import sys
import time

import httpx

from config import CB_CHAT_URL, PHASE2_DIR

PROMPTS_FILE = PHASE2_DIR / "imperator-turns.json"
TIMEOUT = 180  # Gemini Flash is fast but tool-use ReAct needs time


def chat(message: str, history: list) -> dict:
    """Send a message to the Imperator with conversation history."""
    messages = history + [{"role": "user", "content": message}]
    payload = {"model": "imperator", "messages": messages, "stream": False}
    with httpx.Client() as client:
        resp = client.post(CB_CHAT_URL, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()


def main():
    prompts = json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))
    history = []
    results = []

    print(f"=== Phase 2: Imperator Conversation ({len(prompts)} turns) ===\n")

    for i, turn in enumerate(prompts):
        prompt = turn["prompt"]
        expected_keywords = turn.get("expected_content_keywords", [])

        print(f"Turn {i+1}: {prompt[:80]}...")
        start = time.monotonic()

        try:
            response = chat(prompt, history)
            duration = time.monotonic() - start
            content = response["choices"][0]["message"]["content"]

            # Check for expected keywords
            missing = [kw for kw in expected_keywords if kw.lower() not in content.lower()]

            passed = len(content) > 20 and len(missing) == 0
            detail = f"{len(content)} chars, {duration:.1f}s"
            if missing:
                detail += f", missing: {missing}"

            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {detail}")
            print(f"  Response: {content[:200]}...")

            results.append({"turn": i+1, "passed": passed, "detail": detail})

            # Add to history for next turn
            history.append({"role": "user", "content": prompt})
            history.append({"role": "assistant", "content": content})

        except (httpx.HTTPError, KeyError) as exc:
            duration = time.monotonic() - start
            print(f"  [FAIL] Error: {exc} ({duration:.1f}s)")
            results.append({"turn": i+1, "passed": False, "detail": str(exc)})

    # Summary
    passed = sum(1 for r in results if r["passed"])
    print(f"\n=== Results: {passed}/{len(results)} passed ===")

    # Save transcript
    transcript_path = PHASE2_DIR / "transcript.json"
    transcript_path.write_text(json.dumps({
        "history": history,
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"Transcript saved to {transcript_path}")

    if passed < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
