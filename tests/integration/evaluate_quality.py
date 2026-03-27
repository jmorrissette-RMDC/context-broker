"""
Phase 4a: Quality evaluation via Claude Sonnet CLI.

Evaluates context assembly quality, summarization accuracy,
and knowledge extraction completeness.

Usage:
    python tests/integration/evaluate_quality.py
"""

import json
import subprocess
import sys

import httpx

from config import CB_MCP_URL, SONNET_MODEL

SSH_TARGET = "aristotle9@192.168.1.110"


def _discover_conv_ids() -> list[str]:
    """Discover test conversation IDs from the database."""
    import subprocess

    result = subprocess.run(
        [
            "ssh",
            SSH_TARGET,
            "docker exec context-broker-postgres psql -U context_broker -d context_broker -t -c "
            "\"SELECT id FROM conversations WHERE title LIKE 'conversation-%' ORDER BY created_at LIMIT 2\"",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]


CONV_IDS = _discover_conv_ids()


def mcp_call(tool_name: str, arguments: dict) -> dict:
    with httpx.Client() as client:
        resp = client.post(
            CB_MCP_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            timeout=60,
        )
        body = resp.json()
        if "error" in body:
            return {"error": body["error"]}
        text = body.get("result", {}).get("content", [{}])[0].get("text", "{}")
        return json.loads(text)


def sonnet_evaluate(data_content: str, instruction: str) -> str:
    """Call Claude Sonnet via CLI for quality evaluation.

    Writes data to a temp file, passes a short instruction via -p that
    tells Sonnet to read the file and evaluate it. Avoids prompt length limits.
    """
    import tempfile
    from pathlib import Path

    data_file = Path(tempfile.mktemp(suffix=".md", dir="."))
    try:
        data_file.write_text(data_content, encoding="utf-8")
        prompt = f"Read the file at {data_file.absolute()} and follow the instructions inside it. {instruction}"
        result = subprocess.run(
            [
                "claude",
                "--model",
                SONNET_MODEL,
                "--print",
                "--allowedTools",
                "Read",
                "-p",
                prompt,
            ],
            capture_output=True,
            timeout=300,
        )
        stdout = (result.stdout or b"").decode("utf-8", errors="replace").strip()
        if not stdout:
            stderr = (result.stderr or b"").decode("utf-8", errors="replace")
            return f"Sonnet CLI error: {stderr[:300]}"
        return stdout
    finally:
        data_file.unlink(missing_ok=True)


def evaluate_context_assembly(conv_id: str, build_type: str) -> dict:
    """Evaluate the quality of assembled context for a conversation."""
    print(f"\n  Evaluating {conv_id[:8]} / {build_type}...")

    # Get assembled context
    ctx = mcp_call(
        "get_context",
        {
            "build_type": build_type,
            "budget": 204800,
            "conversation_id": conv_id,
        },
    )

    if not ctx.get("context"):
        return {"passed": False, "detail": "No context returned"}

    context_text = json.dumps(ctx["context"], ensure_ascii=False)
    total_tokens = ctx.get("total_tokens", 0)
    tiers = ctx.get("tiers", ctx.get("context_tiers", {}))
    tier_summary = {
        k: (
            len(v)
            if isinstance(v, list)
            else (len(v) if isinstance(v, str) and v else 0)
        )
        for k, v in tiers.items()
    }

    # Get some original messages for comparison
    rows_result = subprocess.run(
        [
            "ssh",
            SSH_TARGET,
            f"docker exec context-broker-postgres psql -U context_broker -d context_broker -t -c "
            f"\"SELECT LEFT(content, 200) FROM conversation_messages WHERE conversation_id='{conv_id}' "
            f"AND role='user' AND LENGTH(content) > 50 ORDER BY sequence_number LIMIT 10\"",
        ],
        capture_output=True,
        timeout=15,
    )
    rows = (rows_result.stdout or b"").decode("utf-8", errors="replace")

    # Write full data to file for Sonnet to read
    data_content = f"""# Context Assembly Quality Evaluation

## Build Type: {build_type}
## Total Tokens: {total_tokens}
## Tier Counts: {json.dumps(tier_summary)}

## Original Sample Messages (10 of many)
{rows}

## Assembled Context (full)
{context_text}

## Instructions
Evaluate this assembled context:
1. Does it preserve the key themes and topics from the original messages?
2. For standard-tiered: are there clear tier 1 (archival summary), tier 2 (chunk summaries), and tier 3 (recent verbatim)?
3. For knowledge-enriched: are there knowledge graph facts and semantic messages in addition to tiers?
4. Is any critical information obviously lost?
5. Rate the overall quality: GOOD, ACCEPTABLE, or POOR.

Respond with ONLY a JSON object: {{"rating": "GOOD/ACCEPTABLE/POOR", "assessment": "brief explanation", "issues": ["list of issues if any"]}}"""

    instruction = "Evaluate the context assembly quality as described in the file. Respond with ONLY a JSON object."
    response = sonnet_evaluate(data_content, instruction)
    print(f"    Sonnet: {response[:200]}")

    try:
        result = json.loads(response)
        passed = result.get("rating", "POOR") != "POOR"
        return {
            "passed": passed,
            "rating": result.get("rating"),
            "detail": result.get("assessment", ""),
        }
    except json.JSONDecodeError:
        # Sonnet didn't return JSON — check for keywords
        passed = "GOOD" in response or "ACCEPTABLE" in response
        return {"passed": passed, "rating": "unknown", "detail": response[:200]}


def main():
    print("=== Quality Evaluation (Sonnet CLI) ===")
    results = []

    for conv_id in CONV_IDS:
        for bt in ["standard-tiered", "knowledge-enriched"]:
            result = evaluate_context_assembly(conv_id, bt)
            results.append(
                {
                    "conversation": conv_id[:8],
                    "build_type": bt,
                    **result,
                }
            )

    # Summary
    print("\n=== Results ===")
    passed = sum(1 for r in results if r["passed"])
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(
            f"  [{status}] {r['conversation']} / {r['build_type']}: {r.get('rating', '?')} — {r.get('detail', '')[:100]}"
        )

    print(f"\nPassed: {passed}/{len(results)}")

    # Save
    results_path = PHASE2_DIR.parent / "quality-results.json"
    results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    if passed < len(results):
        sys.exit(1)


if __name__ == "__main__":
    from config import PHASE2_DIR

    main()
