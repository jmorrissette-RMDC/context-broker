"""
Integration test orchestrator — runs all phases in sequence.

Usage:
    python tests/integration/run_all.py
"""

import subprocess
import sys
import time

SCRIPTS = [
    ("Reset databases", "tests/integration/reset_databases.py"),
    ("Bulk load (Phase 1)", "tests/integration/bulk_load.py"),
    ("Imperator conversation (Phase 2)", "tests/integration/run_imperator_conversation.py"),
    ("Tool exercises (Phase 3)", "tests/integration/run_tool_exercises.py"),
    ("Quality evaluation (Phase 4a)", "tests/integration/evaluate_quality.py"),
    ("Performance analysis (Phase 4b)", "tests/integration/analyze_performance.py"),
]


def main():
    print("=" * 60)
    print("  Context Broker Integration Test Suite")
    print("=" * 60)
    print()

    overall_start = time.monotonic()
    results = []

    for name, script in SCRIPTS:
        print(f"\n{'=' * 60}")
        print(f"  {name}")
        print(f"{'=' * 60}\n")

        start = time.monotonic()
        proc = subprocess.run(
            [sys.executable, script],
            timeout=3600,  # 1 hour max per phase
        )
        duration = time.monotonic() - start

        passed = proc.returncode == 0
        results.append({"name": name, "passed": passed, "duration": duration})

        status = "PASS" if passed else "FAIL"
        print(f"\n  [{status}] {name} ({duration:.0f}s)")

        if not passed:
            print(f"\n  STOPPING — {name} failed. Fix before continuing.")
            break

    # Final report
    total_duration = time.monotonic() - overall_start
    total_passed = sum(1 for r in results if r["passed"])

    print(f"\n{'=' * 60}")
    print(f"  FINAL REPORT")
    print(f"{'=' * 60}")
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['name']} ({r['duration']:.0f}s)")
    print(f"\n  Total: {total_passed}/{len(results)} passed in {total_duration:.0f}s")

    if total_passed < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
