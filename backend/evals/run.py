"""Routing quality evaluation runner.

Usage::

    python -m evals.run                       # report, no gates
    python -m evals.run --report              # plus a per-case table
    python -m evals.run --min-accuracy 0.53   # fail (exit 1) below threshold
    python -m evals.run --dump-failures 20 --failures-out evals/failures.json
    python -m evals.run --write-baseline evals/baseline_gates.json

Exit codes:

    0  every requested gate passed
    1  at least one gate failed
    2  dataset or configuration error (nothing was measured)
    3  a required dependency is missing
    4  an unexpected internal error (traceback with --traceback)

Exit 2 is distinct from exit 1 on purpose. "The classifier is bad" and "the
dataset is broken" are different facts and CI should not read them the same
way.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.baselines import (  # noqa: E402
    BaselineReport,
    evaluate_heuristic,
    evaluate_learned,
    fail_case_report,
)
from evals.dataset import DIFFICULTIES, TIERS, load_dataset  # noqa: E402
from evals.errors import (  # noqa: E402
    BaselineError,
    DatasetError,
    DatasetValidationError,
    DependencyError,
    EvalError,
    GateConfigurationError,
    MetricsError,
    ResultsWriteError,
)
from evals.gates import (  # noqa: E402
    DEFAULT_GATE_SYSTEM,
    GATE_MODES,
    MODE_OBSERVED,
    build_gates,
    evaluate_gates,
    gates_passed,
)
from evals.report import build_report, load_baseline_file, write_results  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
GOLDEN_FILE = Path(__file__).resolve().parent / "golden.json"
# The dataset lives in a directory of shards so adding cases to one concern
# produces a reviewable diff and production-derived cases have their own file.
# Fall back to the single-file form for older checkouts.
GOLDEN = GOLDEN_DIR if GOLDEN_DIR.is_dir() else GOLDEN_FILE
RESULTS = Path(__file__).resolve().parent / "results.json"
BASELINE = Path(__file__).resolve().parent / "baseline_gates.json"


def _repo_relative(path: str) -> str:
    """A path relative to the repo root, so results.json is host-independent.

    An absolute path would make two developers' reports differ for no reason
    and would record where someone's checkout happens to live.
    """
    resolved = Path(path)
    try:
        # Forward slashes regardless of platform: a Windows backslash would make
        # two developers' reports differ for no reason.
        return resolved.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.name


def _scrub_argv(argv: Sequence[str]) -> list[str]:
    """Record which flags were used, without recording their values wholesale.

    A value could be a path outside the repo. Flag names are enough to
    reproduce a command line; the values are all in ``configuration``.
    """
    return [token if token.startswith("-") else "<value>" for token in argv]


def _merge_baseline(existing: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    """Keep human-written sections of a baseline file when regenerating it.

    The measurements and thresholds are refreshed. The policy paragraph, the
    dataset pointer and the deliberately-not-gated list are preserved, because
    those are judgement calls that a command cannot regenerate.
    """
    merged = dict(existing)
    merged.update(
        {
            key: fresh[key]
            for key in ("measurements", "thresholds", "gate_mode", "note")
            if key in fresh
        }
    )
    # A threshold the human reviewed keeps its reviewed flag; a threshold whose
    # value moved is marked auto again so the change cannot pass unnoticed.
    previous = {
        entry["name"]: entry
        for entry in existing.get("thresholds", [])
        if isinstance(entry, dict) and "name" in entry
    }
    for entry in merged.get("thresholds", []):
        old = previous.get(entry["name"])
        if old is None or old.get("threshold") != entry["threshold"]:
            entry["auto"] = True
    return merged


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run",
        description="Routing quality eval harness for the auto-router's classifier.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--dataset", default=str(GOLDEN), help="dataset file or shard directory")
    parser.add_argument("--results", default=str(RESULTS), help="where to write results.json")
    parser.add_argument("--no-save", action="store_true", help="do not write results.json")
    parser.add_argument("--report", action="store_true", help="print a per-case table")

    gates = parser.add_argument_group("quality gates")
    gates.add_argument("--min-accuracy", type=float, default=None, help="fail below this overall accuracy")
    gates.add_argument(
        "--min-clear-accuracy", type=float, default=None, help="fail below this accuracy on clear cases"
    )
    gates.add_argument(
        "--min-ambiguous-accuracy",
        type=float,
        default=None,
        help="fail below this accuracy on ambiguous cases",
    )
    gates.add_argument(
        "--min-adversarial-accuracy",
        type=float,
        default=None,
        help="fail below this accuracy on adversarially tagged cases",
    )
    gates.add_argument(
        "--min-tier-accuracy",
        action="append",
        default=[],
        metavar="TIER=X",
        help=f"per-tier floor, repeatable. Tiers: {', '.join(TIERS)}",
    )
    gates.add_argument(
        "--gate-on",
        choices=GATE_MODES,
        default=MODE_OBSERVED,
        help="compare the observed accuracy or the lower Wilson bound (default: observed)",
    )
    gates.add_argument(
        "--gate-system",
        default=None,
        help=(
            "scope every gate on the command line to this baseline "
            f"(default: {DEFAULT_GATE_SYSTEM}). When set, it also overrides the system "
            "recorded in a --load-baseline file, so a scoped run cannot be silently "
            "redirected."
        ),
    )

    output = parser.add_argument_group("output and inspection")
    output.add_argument("--dump-failures", type=int, default=0, metavar="N", help="write the first N misclassifications")
    output.add_argument("--failures-out", default=None, help="where to write the misclassification dump")
    output.add_argument(
        "--write-baseline", default=None, metavar="PATH", help="write measured values and suggested thresholds"
    )
    output.add_argument(
        "--load-baseline", default=None, metavar="PATH", help="apply thresholds recorded in a baseline file"
    )
    output.add_argument("--quiet", action="store_true", help="suppress the human summary")

    behaviour = parser.add_argument_group("behaviour")
    behaviour.add_argument(
        "--no-learned",
        action="store_true",
        help="skip the TF-IDF baseline (sklearn is not required for the heuristic)",
    )
    behaviour.add_argument("--seed", type=int, default=None, help="override the learned baseline's random seed")
    behaviour.add_argument("--confidence", type=float, default=0.95, help="confidence level for intervals")
    behaviour.add_argument("--traceback", action="store_true", help="print a traceback on internal errors")
    return parser


# --- Presentation -------------------------------------------------------------


def _print_report(reports: dict[str, BaselineReport], dataset_warnings: Sequence[Any]) -> None:
    for name in sorted(reports):
        report = reports[name]
        print(f"{name}  ({report.kind})")
        print(f"  overall      {report.accuracy()}")
        print(f"  clear        {report.rollup['by_difficulty']['clear']}")
        print(f"  ambiguous    {report.rollup['by_difficulty']['ambiguous']}")
        print(f"  adversarial  {report.rollup['adversarial_any']}")
        multi = report.rollup["multi_turn_any"]
        long_conv = report.rollup["long_conversation"]
        if multi.n or long_conv.n:
            print(f"  multi-turn   {multi}")
            print(f"  10+ msgs     {long_conv}")
        for tier in TIERS:
            print(f"  tier {tier:<9}{report.rollup['by_expected_label'][tier]}")
        per_class = report.rollup["per_class"]
        print(f"  macro F1     {per_class['macro_f1']}")
        if per_class["macro_f1"] is not None:
            for tier in TIERS:
                entry = per_class["per_class"].get(tier, {})
                print(
                    f"    {tier:<10} precision {entry.get('precision')} "
                    f"recall {entry.get('recall')} f1 {entry.get('f1')} support {entry.get('support')}"
                )
        matrix = report.rollup["confusion_matrix"]
        print(f"  confusion matrix ({matrix.orientation})")
        header = "".join(f"{column:>11}" for column in matrix.labels)
        print(f"    {'expected\\pred':<13}{header}")
        for row in matrix.labels:
            counts = "".join(f"{matrix.cells[row][column]:>11}" for column in matrix.labels)
            print(f"    {row:<13}{counts}")
        top = matrix.off_diagonal_pairs()[:5]
        if top:
            print("  most confused (expected -> predicted):")
            for expected, predicted, count in top:
                print(f"    {expected:<10} -> {predicted:<10} {count}")
        print()

    print("clear vs ambiguous:")
    for name in sorted(reports):
        report = reports[name]
        clear = report.rollup["by_difficulty"]["clear"]
        ambiguous = report.rollup["by_difficulty"]["ambiguous"]
        if clear.value is None or ambiguous.value is None:
            print(f"  {name:<34} not comparable, one subset is empty")
            continue
        print(f"  {name:<34} clear {clear.value:.1%} vs ambiguous {ambiguous.value:.1%}")
    print()

    if dataset_warnings:
        print(f"dataset warnings ({len(dataset_warnings)}):")
        for issue in dataset_warnings:
            print(f"  {issue}")
        print()


def _print_table(
    reports: dict[str, BaselineReport], cases: Sequence[Any], predictions: dict[str, tuple[str, ...]]
) -> None:
    """One row per case, one column per system, wrong answers marked.

    Columns are aligned **by case id**, not by list position. The holdout
    baseline only predicts 95 of the 379 cases, so positional indexing raises
    an IndexError partway down the table and loses the rest of the report.
    """
    names = sorted(predictions)
    by_id = {
        name: dict(zip(report.case_ids, report.predictions))
        for name, report in reports.items()
    }
    widths = [max(len(name), 10) for name in names]
    header = f"{'id':<34} {'expected':<10} " + " ".join(
        f"{name:<{width}}" for name, width in zip(names, widths)
    )
    print(header)
    print("-" * len(header))

    disagreeing_cases = 0
    wrong_cells = 0
    missing = 0
    for case in cases:
        cells = []
        answers = []
        for name, width in zip(names, widths):
            predicted = by_id[name].get(case.id)
            if predicted is None:
                missing += 1
                cells.append(f"{'-':<{width}}")
                continue
            answers.append(predicted)
            if predicted != case.expected_label:
                wrong_cells += 1
            cells.append(f"{predicted:<{width - 1}}{' ' if predicted == case.expected_label else 'X'}")
        if len(set(answers)) > 1:
            disagreeing_cases += 1
        print(f"{case.id:<34} {case.expected_label:<10} " + " ".join(cells))

    print(
        f"\n{wrong_cells} wrong answer(s) across {disagreeing_cases} case(s) where the "
        f"scored systems disagree."
    )
    if missing:
        print(f"{missing} case(s) were not evaluated by at least one system (shown as '-').")


# --- Baseline assembly --------------------------------------------------------


def collect_baselines(
    cases: Sequence[Any],
    *,
    include_learned: bool,
    seed: int | None,
    confidence: float,
) -> dict[str, BaselineReport]:
    reports: dict[str, BaselineReport] = {}
    heuristic = evaluate_heuristic(cases, confidence=confidence)
    reports[heuristic.name] = heuristic
    if include_learned:
        learned, companions = evaluate_learned(
            cases, seed=seed, confidence=confidence, with_holdout=True, with_majority=True
        )
        reports[learned.name] = learned
        for companion in companions:
            reports[companion.name] = companion
    return reports


def _build_specs(entry: dict[str, Any], *, default_system: str | None, source: str):
    """Build one GateSpec from a baseline-file entry, with full validation.

    The CLI path validates thresholds through ``_validate_threshold``. Without
    the same check here a baseline file containing ``90`` (a percentage where a
    proportion was expected) or ``-1`` would install a gate that can never pass,
    with no diagnostic -- the exact failure the CLI path has an error message
    for.
    """
    from evals.gates import GateSpec, _validate_threshold

    name = str(entry["name"])
    selector = str(entry["selector"])
    raw_threshold = entry["threshold"]
    if isinstance(raw_threshold, bool):
        raise GateConfigurationError(
            f"{source}: gate {name!r} has threshold {raw_threshold!r}, which is a boolean, "
            "not a proportion"
        )
    try:
        threshold = float(raw_threshold)
    except (TypeError, ValueError) as exc:
        raise GateConfigurationError(
            f"{source}: gate {name!r} has threshold {entry['threshold']!r}, which is not a number"
        ) from exc
    system = str(entry.get("system") or default_system or DEFAULT_GATE_SYSTEM)
    if default_system is not None and entry.get("system"):
        # The command line wins over the file, and the file must not be able to
        # silently redirect a gate the operator thought they had scoped.
        system = default_system
    return GateSpec(
        name=name,
        selector=selector,
        threshold=_validate_threshold(name, threshold),
        system=system,
        rationale=str(entry.get("rationale", "")),
    )


def _specs_from_baseline_file(path: str, *, system: str | None = None) -> list[Any]:
    payload = load_baseline_file(path)
    entries = payload.get("thresholds")
    if not isinstance(entries, list) or not entries:
        raise GateConfigurationError(
            f"baseline file {path} has no 'thresholds' list. Regenerate it with "
            "'python -m evals.run --write-baseline'."
        )
    return [
        _build_specs(entry, default_system=system, source=f"{path} thresholds[{index}]")
        for index, entry in enumerate(entries)
    ]


# --- Entry point --------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if not 0.0 < args.confidence < 1.0:
            raise GateConfigurationError(
                f"--confidence must be in (0, 1), got {args.confidence}"
            )

        cases, dataset_warnings = load_dataset(args.dataset)

        reports = collect_baselines(
            cases,
            include_learned=not args.no_learned,
            seed=args.seed,
            confidence=args.confidence,
        )

        cli_system = args.gate_system
        specs = []
        if args.load_baseline:
            specs.extend(_specs_from_baseline_file(args.load_baseline, system=cli_system))
        specs.extend(
            build_gates(
                min_accuracy=args.min_accuracy,
                min_clear_accuracy=args.min_clear_accuracy,
                min_ambiguous_accuracy=args.min_ambiguous_accuracy,
                min_adversarial_accuracy=args.min_adversarial_accuracy,
                tier_gates=args.min_tier_accuracy,
                system=cli_system or DEFAULT_GATE_SYSTEM,
            )
        )
        gate_results = evaluate_gates(reports, specs, mode=args.gate_on)
        # The systems the gates actually ran against, which can differ from the
        # CLI default when --load-baseline supplied per-system thresholds.
        effective_gate_systems = sorted({result.system for result in gate_results}) or [DEFAULT_GATE_SYSTEM]

        configuration = {
            "dataset": _repo_relative(args.dataset),
            "gate_mode": args.gate_on,
            "gate_system": cli_system or "per-threshold (see gates.results[].system)",
            "gate_systems_effective": effective_gate_systems,
            "confidence": args.confidence,
            "learned_baseline_enabled": not args.no_learned,
            "seed": args.seed,
            "tiers": list(TIERS),
            "difficulties": list(DIFFICULTIES),
            "argv": _scrub_argv(argv if argv is not None else sys.argv[1:]),
        }

        # Results are written before anything is printed, so a rendering bug in
        # the human summary can never cost a run its results.json.
        failures_payload: dict[str, Any] | None = None
        if args.dump_failures > 0:
            failures_payload = {
                "requested": args.dump_failures,
                "by_system": {
                    name: fail_case_report(cases, report.predictions, limit=args.dump_failures)
                    for name, report in sorted(reports.items())
                },
                "note": (
                    "Misclassifications in dataset order. Read them before changing anything: "
                    "a disagreement is not automatically a model failure."
                ),
            }

        document = build_report(
            cases=cases,
            reports=reports,
            dataset_path=_repo_relative(args.dataset),
            dataset_warnings=dataset_warnings,
            configuration=configuration,
            gate_results=gate_results,
            repo_root=ROOT,
            failures=failures_payload,
        )

        if args.write_baseline:
            from evals.gates import baseline_snapshot

            snapshot = baseline_snapshot(reports, specs, mode=args.gate_on)
            target = Path(args.write_baseline)
            if target.is_file():
                # Regeneration must not delete the parts a human wrote: the
                # policy paragraph, the "not gated" list, the fingerprint.
                snapshot = _merge_baseline(load_baseline_file(str(target)), snapshot)
            write_results(args.write_baseline, snapshot)
            if not args.quiet:
                print(f"wrote measured baseline to {args.write_baseline}\n")

        if args.dump_failures > 0:
            destination = Path(args.failures_out or (Path(args.results).with_name("failures.json")))
            write_results(destination, failures_payload)
            if not args.quiet:
                for name, rows in failures_payload["by_system"].items():
                    print(f"wrote {len(rows)} {name} misclassifications to {destination}")
                print()

        if not args.no_save:
            written = write_results(args.results, document)
            if not args.quiet:
                print(f"results written to {written}")

        if not args.quiet:
            _print_report(reports, dataset_warnings)
            if args.report:
                _print_table(reports, cases, {n: r.predictions for n, r in reports.items()})
                print()

        if gate_results and not args.quiet:
            print("\nquality gates:")
            for result in gate_results:
                print(f"  {result.describe()}")

        if not gates_passed(gate_results):
            if not args.quiet:
                failed = [r.name for r in gate_results if not r.passed]
                print(f"\nFAIL: {len(failed)} gate(s) failed: {', '.join(failed)}")
            return 1
        if gate_results and not args.quiet:
            print("\nPASS: all gates met")
        return 0

    except DatasetValidationError as exc:
        print(f"\nDataset validation failed. Nothing was measured.\n{exc}", file=sys.stderr)
        return 2
    except (DatasetError, GateConfigurationError, MetricsError) as exc:
        print(f"\nConfiguration or dataset error: {exc}", file=sys.stderr)
        return 2
    except DependencyError as exc:
        print(f"\nMissing dependency: {exc}", file=sys.stderr)
        return 3
    except ResultsWriteError as exc:
        print(f"\nCould not write results: {exc}", file=sys.stderr)
        return 2
    except BaselineError as exc:
        print(f"\nA baseline could not be evaluated: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. results.json was left untouched.", file=sys.stderr)
        return 4
    except EvalError as exc:
        print(f"\nEvaluation error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - last resort, must not be silent
        print(f"\nUnexpected internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        if args.traceback:
            traceback.print_exc()
        else:
            print("Re-run with --traceback for the full stack.", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())