"""Quality gate tests, including the cases a gate must not paper over."""

from __future__ import annotations

import json

import pytest

from evals.baselines import evaluate_baseline
from evals.dataset import EvalCase
from evals.errors import GateConfigurationError
from evals.gates import (
    MODE_OBSERVED,
    MODE_WILSON_LOWER,
    GateSpec,
    baseline_snapshot,
    build_gates,
    evaluate_gates,
    gates_passed,
    parse_tier_gate,
)
from evals.metrics import rollup


def case(cid: str, text: str, label: str, difficulty: str = "clear") -> EvalCase:
    return EvalCase(
        id=cid,
        input=text,
        expected_label=label,
        difficulty=difficulty,
        source="synthetic",
        category="c",
    )


def report_with(specs, predictions, **kwargs):
    cases = [case(*spec) for spec in specs]
    return evaluate_baseline("heuristic", "rule_based", cases, predictions, **kwargs)


# --- Threshold validation -----------------------------------------------------


@pytest.mark.parametrize("threshold", [0.0, 0.5, 1.0])
def test_valid_thresholds_are_accepted(threshold):
    specs = build_gates(min_accuracy=threshold)
    assert specs[0].threshold == threshold


@pytest.mark.parametrize("threshold", [-0.1, 1.1, 90])
def test_invalid_thresholds_are_rejected(threshold):
    with pytest.raises(GateConfigurationError) as exc:
        build_gates(min_accuracy=threshold)
    assert "pass 0.9 rather than 90" in str(exc.value) or "[0, 1]" in str(exc.value)


def test_boolean_threshold_is_rejected():
    with pytest.raises(GateConfigurationError):
        build_gates(min_accuracy=True)


def test_unknown_tier_gate_is_rejected():
    with pytest.raises(GateConfigurationError) as exc:
        parse_tier_gate("bogus=0.5")
    assert "unknown tier" in str(exc.value)


def test_tier_gate_without_equals_is_rejected():
    with pytest.raises(GateConfigurationError) as exc:
        parse_tier_gate("fast")
    assert "TIER=THRESHOLD" in str(exc.value)


def test_tier_gate_with_non_numeric_threshold_is_rejected():
    with pytest.raises(GateConfigurationError) as exc:
        parse_tier_gate("fast=high")
    assert "not a number" in str(exc.value)


def test_only_requested_gates_are_built():
    specs = build_gates(min_accuracy=0.5)
    assert [s.name for s in specs] == ["min-accuracy"]


# --- Passing and failing ------------------------------------------------------


def test_gate_passes_above_the_threshold():
    report = report_with([("a", "hi", "fast"), ("b", "thanks", "fast")], ["fast", "fast"])
    results = evaluate_gates({"heuristic": report}, build_gates(min_accuracy=0.9))
    assert gates_passed(results)
    assert results[0].observed == 1.0


def test_gate_fails_below_the_threshold():
    report = report_with([("a", "hi", "fast"), ("b", "thanks", "fast")], ["fast", "balanced"])
    results = evaluate_gates({"heuristic": report}, build_gates(min_accuracy=0.9))
    assert not gates_passed(results)
    assert "below the threshold" in results[0].reason


def test_clear_subset_gate_can_fail_while_overall_passes():
    cases = [case("a", "hi", "fast", "clear"), case("b", "why", "balanced", "ambiguous")]
    report = evaluate_baseline("heuristic", "rule_based", cases, ["balanced", "balanced"])
    assert report.accuracy().value == 0.5
    results = evaluate_gates(
        {"heuristic": report},
        build_gates(min_accuracy=0.4, min_clear_accuracy=0.9),
    )
    by_name = {r.name: r for r in results}
    assert by_name["min-accuracy"].passed is True
    assert by_name["min-clear-accuracy"].passed is False
    assert by_name["min-clear-accuracy"].selector == "clear-accuracy"


def test_ambiguous_subset_gate_can_fail_while_overall_passes():
    cases = [case("a", "hi", "fast", "clear"), case("b", "why", "balanced", "ambiguous")]
    report = evaluate_baseline("heuristic", "rule_based", cases, ["fast", "fast"])
    results = evaluate_gates({"heuristic": report}, build_gates(min_ambiguous_accuracy=0.5))
    assert not gates_passed(results)
    assert results[0].selector == "ambiguous-accuracy"


def test_tier_gate_evaluates_the_expected_tier():
    cases = [case("a", "hi", "fast"), case("b", "thanks", "balanced")]
    report = evaluate_baseline("heuristic", "rule_based", cases, ["fast", "fast"])
    results = evaluate_gates({"heuristic": report}, [parse_tier_gate("balanced=0.9")])
    assert not gates_passed(results)
    assert results[0].n == 1


# --- Modes --------------------------------------------------------------------


def test_wilson_lower_mode_is_stricter_than_observed():
    report = report_with([(f"f{i}", "hi", "fast") for i in range(20)] + [(f"b{i}", "thanks", "fast") for i in range(20)], ["fast"] * 20 + ["balanced"] * 20)
    specs = [GateSpec(name="g", selector="accuracy", threshold=0.5)]
    observed = evaluate_gates({"heuristic": report}, specs, mode=MODE_OBSERVED)
    lower = evaluate_gates({"heuristic": report}, specs, mode=MODE_WILSON_LOWER)
    assert observed[0].passed is True
    assert lower[0].passed is False
    assert lower[0].compared == lower[0].ci_lower


def test_invalid_gate_mode_is_rejected():
    report = report_with([("a", "hi", "fast")], ["fast"])
    with pytest.raises(GateConfigurationError):
        evaluate_gates({"heuristic": report}, build_gates(min_accuracy=0.1), mode="vibes")


# --- Fail closed --------------------------------------------------------------


def test_empty_subset_fails_the_gate_rather_than_passing():
    # Only clear cases exist, so the ambiguous subset is n=0. Reporting 0.0 and
    # failing is honest; reporting 0.0 and passing would not be.
    report = report_with([("a", "hi", "fast")], ["fast"])
    results = evaluate_gates({"heuristic": report}, build_gates(min_ambiguous_accuracy=0.1))
    assert not gates_passed(results)
    assert results[0].observed is None
    assert results[0].n == 0
    assert "Failing closed" in results[0].reason


def test_gate_on_a_system_that_was_not_evaluated_fails():
    report = report_with([("a", "hi", "fast")], ["fast"])
    specs = [GateSpec(name="g", selector="accuracy", threshold=0.1, system="typo_name")]
    results = evaluate_gates({"heuristic": report}, specs)
    assert not gates_passed(results)
    assert "was not evaluated" in results[0].reason


def test_unknown_selector_fails():
    report = report_with([("a", "hi", "fast")], ["fast"])
    specs = [GateSpec(name="g", selector="nonsense", threshold=0.1)]
    results = evaluate_gates({"heuristic": report}, specs)
    assert not gates_passed(results)
    assert "does not exist" in results[0].reason


# --- Serialisation and snapshots ----------------------------------------------


def test_gate_result_serialises_its_evidence():
    report = report_with([(f"f{i}", "hi", "fast") for i in range(10)], ["fast"] * 9 + ["balanced"])
    payload = evaluate_gates({"heuristic": report}, build_gates(min_accuracy=0.5))[0].to_dict()
    json.dumps(payload)
    assert payload["n"] == 10
    assert payload["observed"] == 0.9
    assert payload["ci_lower"] < 0.9 < payload["ci_upper"]
    assert payload["rationale"]
    assert payload["metric_path"] == "heuristic.accuracy"


def test_snapshot_without_specs_rounds_the_measurement_down():
    cases = [case(f"c{i}", "hi", "fast") for i in range(100)]
    cases += [case(f"b{i}", "explain it", "balanced") for i in range(10)]
    report = evaluate_baseline("heuristic", "rule_based", cases, ["fast"] * 100 + ["balanced"] * 10)
    snapshot = baseline_snapshot({"heuristic": report}, [], mode=MODE_OBSERVED)
    entry = next(t for t in snapshot["thresholds"] if t["selector"] == "accuracy")
    assert entry["threshold"] <= report.accuracy().value
    assert entry["auto"] is True


def test_snapshot_with_specs_records_them_verbatim():
    cases = [case(f"c{i}", "hi", "fast") for i in range(10)]
    report = evaluate_baseline("heuristic", "rule_based", cases, ["fast"] * len(cases))
    specs = build_gates(min_accuracy=0.3)
    snapshot = baseline_snapshot({"heuristic": report}, specs, mode=MODE_OBSERVED)
    entry = snapshot["thresholds"][0]
    assert entry["threshold"] == 0.3
    assert entry["auto"] is False
    assert entry["observed_at_write_time"] == 1.0


def test_snapshot_thresholds_round_trip_through_the_runner_loader(tmp_path):
    from evals.run import _specs_from_baseline_file

    cases = [case(f"c{i}", "hi", "fast") for i in range(10)]
    report = evaluate_baseline("heuristic", "rule_based", cases, ["fast"] * len(cases))
    snapshot = baseline_snapshot({"heuristic": report}, [], mode=MODE_OBSERVED)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    specs = _specs_from_baseline_file(str(path), system="heuristic")
    assert [s.name for s in specs] == [
        "min-accuracy",
        "min-clear-accuracy",
        "min-ambiguous-accuracy",
        "min-adversarial-accuracy",
    ]
    # Round down: a generated threshold can never exceed its measurement.
    assert all(
        spec.threshold <= report.accuracy().value
        for spec in specs
        if spec.selector == "accuracy"
    )


def test_snapshot_is_json_serialisable():
    cases = [case("a", "hi", "fast"), case("b", "explain", "balanced")]
    report = evaluate_baseline("heuristic", "rule_based", cases, ["fast", "fast"])
    json.dumps(baseline_snapshot({"heuristic": report}, [], mode=MODE_OBSERVED))


def test_rollup_rejects_a_subset_selector_that_does_not_exist():
    cases = [case("a", "hi", "fast")]
    result = rollup(cases, ["fast"])
    assert "balanced" not in result["by_category"]
    assert "balanced" in result["by_expected_label"]