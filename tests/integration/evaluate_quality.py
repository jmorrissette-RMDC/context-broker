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
        ["ssh", SSH_TARGET,
         "docker exec context-broker-postgres psql -U context_broker -d context_broker -t -c "
         "\"SELECT id FROM conversations WHERE title LIKE 'conversation-%' ORDER BY created_at LIMIT 2\""],
        capture_output=True, text=True, timeout=15,
    )
    return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]

CONV_IDS = _discover_conv_ids()


def mcp_call(tool_name: str, arguments: dict) -> dict:
    with httpx.Client() as client:
        resp = client.post(CB_MCP_URL, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }, timeout=60)
        body = resp.json()
        if "error" in body:
            return {"error": body["error"]}
        text = body.get("result", {}).get("content", [{}])[0].get("text", "{}")
        return json.loads(text)


def sonnet_evaluate(prompt: str) -> str:
    """Call Claude Sonnet via CLI for quality evaluation.

    Uses stdin to pass the prompt (avoids Windows command line length limit).
    """
    result = subprocess.run(
        ["claude", "--model", SONNET_MODEL, "--print", "-p", "-"],
        input=prompt.encode("utf-8"),
        capture_output=True, timeout=300,
    )
    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        return f"Sonnet CLI error: {stderr[:200]}"
    return (result.stdout or b"").decode("utf-8", errors="replace").strip()


def evaluate_context_assembly(conv_id: str, build_type: str) -> dict:
    """Evaluate the quality of assembled context for a conversation."""
    print(f"\n  Evaluating {conv_id[:8]} / {build_type}...")

    # Get assembled context
    ctx = mcp_call("get_context", {
        "build_type": build_type,
        "budget": 204800,
        "conversation_id": conv_id,
    })

    if not ctx.get("context"):
        return {"passed": False, "detail": "No context returned"}

    context_text = json.dumps(ctx["context"], ensure_ascii=False)[:15000]  # Truncate for eval
    total_tokens = ctx.get("total_tokens", 0)
    tiers = ctx.get("tiers", ctx.get("context_tiers", {}))

    # Get some original messages for comparison
    rows_result = subprocess.run(
        ["ssh", SSH_TARGET,
         f"docker exec context-broker-postgres psql -U context_broker -d context_broker -t -c "
         f"\"SELECT LEFT(content, 200) FROM conversation_messages WHERE conversation_id='{conv_id}' "
         f"AND role='user' AND LENGTH(content) > 50 ORDER BY sequence_number LIMIT 10\""],
        capture_output=True, timeout=15,
    )
    rows = (rows_result.stdout or b"").decode("utf-8", errors="replace")[:5000]

    eval_prompt = f"""You are evaluating the quality of a context assembly system.

ORIGINAL SAMPLE MESSAGES (10 of many):
{rows}

ASSEMBLED CONTEXT ({build_type}, {total_tokens} tokens, tiers: {json.dumps(tiers)}):
{context_text[:20000]}

Evaluate:
1. Does the assembled context preserve the key themes and topics from the original messages?
2. For standard-tiered: are there clear tier 1 (archival summary), tier 2 (chunk summaries), and tier 3 (recent verbatim) sections?
3. Is any critical information obviously lost?
4. Rate the overall quality: GOOD, ACCEPTABLE, or POOR.

Respond with a JSON object: {{"rating": "GOOD/ACCEPTABLE/POOR", "assessment": "brief explanation", "issues": ["list of issues if any"]}}"""

    response = sonnet_evaluate(eval_prompt)
    print(f"    Sonnet: {response[:200]}")

    try:
        result = json.loads(response)
        passed = result.get("rating", "POOR") != "POOR"
        return {"passed": passed, "rating": result.get("rating"), "detail": result.get("assessment", "")}
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
            results.append({
                "conversation": conv_id[:8],
                "build_type": bt,
                **result,
            })

    # Summary
    print(f"\n=== Results ===")
    passed = sum(1 for r in results if r["passed"])
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['conversation']} / {r['build_type']}: {r.get('rating', '?')} — {r.get('detail', '')[:100]}")

    print(f"\nPassed: {passed}/{len(results)}")

    # Save
    results_path = PHASE2_DIR.parent / "quality-results.json"
    results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    if passed < len(results):
        sys.exit(1)


if __name__ == "__main__":
    from config import PHASE2_DIR
    main()
