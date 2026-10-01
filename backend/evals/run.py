"""Routing quality eval harness.

Evaluates the auto-router's complexity classifier against a golden set of
prompts. Runs offline (no API keys required) so it is safe to run in CI.

Usage:
    python -m evals.run                     # offline routing accuracy
    python -m evals.run --min-accuracy 0.9  # fail (exit 1) below threshold
    python -m evals.run --report            # pretty per-case table
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.auto_router import classify_complexity  # noqa: E402

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_FILE = Path(__file__).parent / "golden.json"
# The dataset now lives in a directory of shards so that adding cases to one
# concern produces a reviewable diff, and so production-derived cases have
# their own file. Fall back to the single-file form for older checkouts.
GOLDEN = GOLDEN_DIR if GOLDEN_DIR.is_dir() else GOLDEN_FILE
RESULTS = Path(__file__).parent / "results.json"
TIERS = ["fast", "balanced", "powerful"]


def load_golden() -> list[dict]:
    from evals.dataset import load_dataset

    cases, _ = load_dataset(GOLDEN)
    return [
        {"id": case.id, "prompt": case.input, "expected_tier": case.expected_label}
        for case in cases
    ]


def run_case(case: dict) -> dict:
    messages = [{"role": "user", "content": case["prompt"]}]
    predicted = classify_complexity(messages)
    return {
        "id": case["id"],
        "prompt": case["prompt"],
        "expected": case["expected_tier"],
        "predicted": predicted,
        "correct": predicted == case["expected_tier"],
    }


def summarize(results: list[dict]) -> dict:
    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    per_tier = {tier: {"total": 0, "correct": 0} for tier in TIERS}
    for r in results:
        per_tier[r["expected"]]["total"] += 1
        per_tier[r["expected"]]["correct"] += int(r["correct"])
    return {
        "total_cases": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "per_tier": {
            tier: {
                "total": v["total"],
                "correct": v["correct"],
                "accuracy": round(v["correct"] / v["total"], 4) if v["total"] else 0.0,
            }
            for tier, v in per_tier.items()
        },
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
    }


def print_table(results: list[dict]) -> None:
    width = max(len(r["prompt"]) for r in results)
    print(f"{'id':<18} {'expected':<9} {'predicted':<9} {'prompt':<{width}} verdict")
    print("-" * (70 + width))
    for r in results:
        verdict = "PASS" if r["correct"] else "FAIL"
        print(
            f"{r['id']:<18} {r['expected']:<9} {r['predicted']:<9} "
            f"{r['prompt']:<{width}} {verdict}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Routing quality eval harness")
    parser.add_argument("--report", action="store_true", help="print per-case table")
    parser.add_argument("--min-accuracy", type=float, default=0.0)
    parser.add_argument("--no-save", action="store_true", help="skip results.json")
    args = parser.parse_args()

    cases = load_golden()
    results = [run_case(c) for c in cases]
    summary = summarize(results)

    if args.report:
        print_table(results)

    print(f"\nRouting accuracy: {summary['accuracy']:.1%} "
          f"({summary['correct']}/{summary['total_cases']})")
    for tier in TIERS:
        t = summary["per_tier"][tier]
        if t["total"]:
            print(f"  {tier:<9} {t['accuracy']:.1%} ({t['correct']}/{t['total']})")

    if not args.no_save:
        RESULTS.write_text(
            json.dumps({"summary": summary, "cases": results}, indent=2),
            encoding="utf-8",
        )
        print(f"\nResults written to {RESULTS}")

    if summary["accuracy"] < args.min_accuracy:
        print(f"\nFAIL: accuracy below minimum {args.min_accuracy:.1%}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
