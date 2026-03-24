"""
Phase 3: Tool exercise tests.

Feeds synthetic prompts to the Imperator that specifically trigger
diagnostic, search, and admin tools. Verifies tools are called
and responses contain expected content.

Usage:
    python tests/integration/run_tool_exercises.py
"""

import json
import subprocess
import sys
import time

import httpx

from config import CB_CHAT_URL, CB_MCP_URL, PHASE3_DIR

SSH_TARGET = "aristotle9@192.168.1.110"
TIMEOUT = 180


def chat(message: str) -> dict:
    payload = {"model": "imperator", "messages": [{"role": "user", "content": message}], "stream": False}
    with httpx.Client() as client:
        resp = client.post(CB_CHAT_URL, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()


def enable_admin_tools():
    """Enable admin_tools in te.yml via SSH."""
    subprocess.run(
        ["ssh", SSH_TARGET,
         "sed -i 's/admin_tools: false/admin_tools: true/' /mnt/storage/projects/portfolio/ContextBroker/config/te.yml"],
        capture_output=True, timeout=10,
    )
    time.sleep(2)  # Wait for hot-reload


def disable_admin_tools():
    """Disable admin_tools in te.yml."""
    subprocess.run(
        ["ssh", SSH_TARGET,
         "sed -i 's/admin_tools: true/admin_tools: false/' /mnt/storage/projects/portfolio/ContextBroker/config/te.yml"],
        capture_output=True, timeout=10,
    )


def run_prompt_file(filepath, requires_admin=False):
    """Run all prompts from a JSON file."""
    prompts = json.loads(filepath.read_text(encoding="utf-8"))
    results = []

    for turn in prompts:
        if turn.get("requires_admin") and not requires_admin:
            continue

        prompt = turn["prompt"]
        expected_keywords = turn.get("expected_content_keywords", [])
        print(f"  {prompt[:70]}...")

        try:
            response = chat(prompt)
            content = response["choices"][0]["message"]["content"]
            missing = [kw for kw in expected_keywords if kw.lower() not in content.lower()]
            passed = len(content) > 10 and len(missing) == 0
            detail = f"{len(content)} chars"
            if missing:
                detail += f", missing: {missing}"
            status = "PASS" if passed else "FAIL"
            print(f"    [{status}] {detail}")
            results.append({"prompt": prompt[:60], "passed": passed, "detail": detail})
        except (httpx.HTTPError, KeyError) as exc:
            print(f"    [FAIL] {exc}")
            results.append({"prompt": prompt[:60], "passed": False, "detail": str(exc)})

    return results


def main():
    print("=== Phase 3: Tool Exercises ===\n")
    all_results = []

    # Diagnostic tools (always available)
    print("--- Diagnostic Tools ---")
    all_results.extend(run_prompt_file(PHASE3_DIR / "diagnostic-prompts.json"))

    # Search tools
    print("\n--- Search Tools ---")
    all_results.extend(run_prompt_file(PHASE3_DIR / "search-prompts.json"))

    # Admin tools (enable first)
    print("\n--- Admin Tools (enabling admin_tools=true) ---")
    enable_admin_tools()
    all_results.extend(run_prompt_file(PHASE3_DIR / "admin-prompts.json", requires_admin=True))
    disable_admin_tools()

    # Summary
    passed = sum(1 for r in all_results if r["passed"])
    print(f"\n=== Results: {passed}/{len(all_results)} passed ===")

    # Save results
    results_path = PHASE3_DIR / "results.json"
    results_path.write_text(json.dumps(all_results, indent=2), encoding="utf-8")

    if passed < len(all_results):
        sys.exit(1)


if __name__ == "__main__":
    main()
