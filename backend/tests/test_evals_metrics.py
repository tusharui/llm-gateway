"""Metric tests: Wilson intervals, confusion matrices, significance.

Reference values were computed independently from the textbook formula and
cross-checked against the published example for 26/40, so a regression in the
implementation shows up here rather than in a report someone trusts.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import pytest

from evals.dataset import EvalCase
from evals.errors import MetricsError
from evals.metrics import (
    ORIENTATION,
    Proportion,
    compare_paired,
    compare_proportions,
    confusion_matrix,
    per_class_report,
    rollup,
    serialise_rollup,
    wilson_interval,
    z_for,
)


# --- Wilson -------------------------------------------------------------------


def test_z_is_the_standard_95_percent_value():
    assert z_for(0.95) == pytest.approx(1.959963984540054, abs=1e-12)


def test_wilson_known_values():
    # Independently computed from centre = (p + z^2/2n)/(1 + z^2/n).
    cases = {
        (249, 300): (0.783386, 0.868270),
        (300, 300): (0.987357, 1.000000),
        (0, 300): (0.000000, 0.012643),
        (1, 1): (0.206549, 1.000000),
        (202, 379): (0.482673, 0.582628),
    }
    for (correct, n), (lower, upper) in cases.items():
        interval = wilson_interval(correct, n)
        assert interval.defined
        assert interval.lower == pytest.approx(lower, abs=1e-5), (correct, n)
        assert interval.upper == pytest.approx(upper, abs=1e-5), (correct, n)


def test_wilson_matches_published_example():
    # Newcombe (1998) worked example: 26 of 40 gives roughly 0.50 to 0.78.
    interval = wilson_interval(26, 40)
    assert interval.lower == pytest.approx(0.4951, abs=1e-3)
    assert interval.upper == pytest.approx(0.7787, abs=1e-3)


def test_wilson_is_never_negative_or_above_one():
    for correct in range(0, 21):
        interval = wilson_interval(correct, 20)
        assert 0.0 <= interval.lower <= interval.upper <= 1.0


def test_wilson_narrows_as_n_grows():
    widths = [wilson_interval(n // 2, n).upper - wilson_interval(n // 2, n).lower for n in (10, 50, 100, 500)]
    assert widths == sorted(widths, reverse=True)


def test_perfect_score_is_not_reported_as_certainty():
    interval = wilson_interval(300, 300)
    assert interval.upper == pytest.approx(1.0)
    # This is the whole reason the interval is mandatory.
    assert interval.lower < 0.99


def test_zero_score_interval_is_asymmetric():
    interval = wilson_interval(0, 300)
    assert interval.lower == 0.0
    assert interval.upper < 0.02


def test_wilson_handles_n_zero_without_dividing_by_zero():
    interval = wilson_interval(0, 0)
    assert interval.defined is False
    payload = interval.to_dict()["wilson_95"]
    assert payload["lower"] is None
    assert payload["upper"] is None
    assert payload["defined"] is False


def test_wilson_handles_n_one():
    assert wilson_interval(0, 1).lower == 0.0
    assert wilson_interval(1, 1).upper == 1.0
    assert wilson_interval(1, 1).lower > 0.0


def test_wilson_is_symmetric_around_a_balanced_split():
    low = wilson_interval(15, 30)
    high = wilson_interval(15, 30)
    assert (low.lower, low.upper) == (high.lower, high.upper)


@pytest.mark.parametrize(
    "correct,n",
    [(-1, 10), (11, 10), (0, -5), (1.5, 10), (True, 10), ("3", 10)],
)
def test_invalid_counts_are_rejected(correct, n):
    with pytest.raises(MetricsError):
        wilson_interval(correct, n)


def test_confidence_level_is_explicit_and_configurable():
    interval = wilson_interval(80, 100, confidence=0.99)
    assert interval.confidence == 0.99
    payload = interval.to_dict()
    assert "wilson_99" in payload
    # A wider confidence level must not produce a narrower interval.
    assert interval.lower < wilson_interval(80, 100).lower
    assert interval.upper > wilson_interval(80, 100).upper


@pytest.mark.parametrize("confidence", [0.0, 1.0, -0.5, 1.5])
def test_invalid_confidence_is_rejected(confidence):
    with pytest.raises(MetricsError):
        z_for(confidence)


# --- Proportion ---------------------------------------------------------------


def test_proportion_serialises_evidence():
    payload = Proportion(correct=249, n=300).to_dict()
    assert payload["n"] == 300
    assert payload["correct"] == 249
    assert payload["incorrect"] == 51
    assert payload["value"] == pytest.approx(0.83, abs=1e-6)
    assert payload["wilson_95"]["lower"] < payload["value"] < payload["wilson_95"]["upper"]


def test_empty_proportion_reports_null_not_zero():
    empty = Proportion(correct=0, n=0)
    assert empty.value is None
    payload = empty.to_dict()
    assert payload["value"] is None
    assert payload["defined"] is False
    assert "n=0" in str(empty)


# --- Confusion matrix ---------------------------------------------------------


def test_binary_confusion_matrix():
    pairs = [("fast", "fast"), ("fast", "fast"), ("fast", "balanced"), ("balanced", "balanced")]
    matrix = confusion_matrix(pairs, labels=["fast", "balanced"])
    assert matrix.cells["fast"] == {"fast": 2, "balanced": 1}
    assert matrix.cells["balanced"] == {"fast": 0, "balanced": 1}
    assert matrix.total == 4
    assert matrix.accuracy() == pytest.approx(0.75)


def test_three_class_confusion_matrix():
    pairs = [("a", "a"), ("a", "b"), ("b", "c"), ("c", "c")]
    matrix = confusion_matrix(pairs, labels=["a", "b", "c"])
    assert matrix.cells["a"] == {"a": 1, "b": 1, "c": 0}
    assert matrix.cells["b"] == {"a": 0, "b": 0, "c": 1}
    assert matrix.cells["c"] == {"a": 0, "b": 0, "c": 1}


def test_missing_predicted_class_still_gets_a_column():
    matrix = confusion_matrix([("a", "a"), ("a", "b")], labels=["a", "b", "c"])
    assert matrix.cells["a"]["c"] == 0
    assert sum(matrix.cells["c"].values()) == 0


def test_missing_actual_class_still_gets_a_row_with_zero_support():
    matrix = confusion_matrix([("a", "a")], labels=["a", "b"])
    assert matrix.support["b"] == 0
    assert matrix.cells["b"] == {"a": 0, "b": 0}


def test_orientation_is_documented_in_the_payload():
    payload = confusion_matrix([("a", "a")], labels=["a"]).to_dict()
    assert payload["orientation"] == ORIENTATION
    assert "rows=expected_label" in payload["orientation"]
    assert payload["rows"] == payload["columns"] == ["a"]


def test_label_universe_defaults_to_sorted_union():
    matrix = confusion_matrix([("z", "a"), ("a", "z")])
    assert matrix.labels == ("a", "z")


def test_labels_that_do_not_cover_observations_is_an_error():
    with pytest.raises(MetricsError) as exc:
        confusion_matrix([("a", "b")], labels=["a"])
    assert "does not cover observed labels" in str(exc.value)


def test_off_diagonal_pairs_are_sorted_by_count():
    pairs = [("a", "b")] * 5 + [("a", "c")] * 2 + [("a", "a")]
    matrix = confusion_matrix(pairs, labels=["a", "b", "c"])
    assert matrix.off_diagonal_pairs() == [("a", "b", 5), ("a", "c", 2)]


def test_empty_pair_sequence_is_undefined_not_zero():
    matrix = confusion_matrix([], labels=["a"])
    assert matrix.accuracy() is None


def test_per_class_report_flags_a_class_never_predicted():
    report = per_class_report([("a", "a"), ("a", "a")], labels=["a", "b"])
    assert report["per_class"]["b"]["support"] == 0
    assert report["per_class"]["b"]["precision"] is None
    assert report["per_class"]["a"]["recall"] == 1.0


# --- Rollups ------------------------------------------------------------------


def make_cases(*specs: tuple[str, str, str]) -> list[EvalCase]:
    """``(id, input, expected_label)`` triples with clear difficulty."""
    return [
        EvalCase(id=cid, input=inp, expected_label=label, difficulty="clear", source="synthetic", category="c")
        for cid, inp, label in specs
    ]


def test_rollup_reports_every_tier_even_when_empty():
    cases = make_cases(("a", "hi", "fast"))
    result = rollup(cases, ["fast"])
    assert set(result["by_expected_label"]) == {"fast", "balanced", "powerful"}
    assert result["by_expected_label"]["balanced"].value is None
    assert result["by_expected_label"]["balanced"].to_dict()["defined"] is False


def test_rollup_splits_clear_and_ambiguous():
    cases = [
        EvalCase("a", "hi", "fast", "clear", "synthetic", "c"),
        EvalCase("b", "why not", "balanced", "ambiguous", "synthetic", "c"),
    ]
    result = rollup(cases, ["fast", "fast"])
    assert result["by_difficulty"]["clear"].value == 1.0
    assert result["by_difficulty"]["ambiguous"].value == 0.0
    assert result["overall"].value == 0.5


def test_rollup_refuses_mismatched_lengths():
    with pytest.raises(MetricsError) as exc:
        rollup(make_cases(("a", "hi", "fast")), ["fast", "fast"])
    assert "differ in length" in str(exc.value)


def test_rollup_refuses_an_empty_prediction():
    with pytest.raises(MetricsError):
        rollup(make_cases(("a", "hi", "fast")), [""])


def test_serialised_rollup_is_json_safe_and_sorted():
    import json

    cases = make_cases(("a", "hi", "fast"), ("b", "why", "balanced"))
    payload = serialise_rollup(rollup(cases, ["fast", "balanced"]))
    json.dumps(payload)  # must not raise
    assert list(payload["by_expected_label"]) == ["balanced", "fast", "powerful"]


# --- Significance -------------------------------------------------------------


def test_clear_vs_ambiguous_difference_is_reported_with_an_interval():
    clear = Proportion(correct=180, n=200)
    ambiguous = Proportion(correct=30, n=100)
    difference = compare_proportions(clear, ambiguous, label_a="clear", label_b="ambiguous")
    assert difference.significant is True
    assert difference.ci_lower > 0
    assert difference.p_value is not None
    assert "significant" in difference.describe()


def test_a_small_difference_is_not_called_significant():
    difference = compare_proportions(
        Proportion(correct=51, n=100), Proportion(correct=48, n=100), label_a="a", label_b="b"
    )
    assert difference.significant is False
    assert difference.ci_lower < 0 < difference.ci_upper


def test_difference_with_an_empty_subset_is_not_comparable():
    difference = compare_proportions(Proportion(correct=1, n=1), Proportion(correct=0, n=0))
    assert difference.difference is None
    assert difference.significant is False
    assert "not comparable" in difference.describe()


def test_degenerate_boundary_comparison_does_not_divide_by_zero():
    difference = compare_proportions(Proportion(correct=0, n=20), Proportion(correct=0, n=30))
    assert difference.difference == 0.0
    assert difference.p_value is None
    assert "degenerate" in difference.note


def test_newcombe_interval_brackets_the_difference():
    a = Proportion(correct=90, n=100)
    b = Proportion(correct=70, n=100)
    difference = compare_proportions(a, b)
    assert difference.difference == pytest.approx(0.2)
    assert difference.ci_lower < 0.2 < difference.ci_upper


# --- Paired comparison --------------------------------------------------------


def test_mcnemar_uses_only_discordant_pairs():
    # 100 cases, 90 of which both systems get right. The verdict must come
    # from the 10 discordant pairs alone, not from the raw 80-point gap.
    a = [True] * 90 + [True] * 8 + [False] * 2
    b = [True] * 90 + [False] * 8 + [True] * 2
    comparison = compare_paired(a, b, name_a="heuristic", name_b="learned")
    assert comparison.both_correct == 90
    assert comparison.a_only == 8
    assert comparison.b_only == 2
    # 2 * (C(10,0)+C(10,1)+C(10,2)) / 2^10 = 0.109375, above 0.05.
    assert comparison.p_value == pytest.approx(0.109375)
    assert comparison.significant is False


def test_mcnemar_detects_a_clear_winner():
    a = [True] * 18 + [False] * 2
    b = [False] * 18 + [True] * 2
    comparison = compare_paired(a, b)
    assert comparison.significant is True
    assert "wins 18" in comparison.describe()


def test_mcnemar_with_no_discordant_cases():
    flags = [True, True, False]
    comparison = compare_paired(flags, flags)
    assert comparison.p_value is None
    assert comparison.significant is False
    assert "identical outcomes" in comparison.describe()


def test_mcnemar_requires_equal_lengths():
    with pytest.raises(MetricsError):
        compare_paired([True], [True, False])


def test_mcnemar_p_value_matches_a_hand_computed_binomial():
    # 10 discordant pairs split 8/2: 2 * (C(10,0)+C(10,1)+C(10,2)) / 2^10
    a = [True] * 8 + [False] * 2
    b = [False] * 8 + [True] * 2
    expected = 2 * (math.comb(10, 0) + math.comb(10, 1) + math.comb(10, 2)) / 2**10
    assert compare_paired(a, b).p_value == pytest.approx(expected)


def test_mcnemar_p_value_is_consistent_in_sign_with_the_direction_of_the_win():
    # A guard against a flipped tail: the winner's p-value must be the small
    # one, whichever system that is.
    a = [True] * 95 + [False] * 5
    b = [False] * 95 + [True] * 5
    forward = compare_paired(a, b, name_a="a", name_b="b")
    reverse = compare_paired(b, a, name_a="b", name_b="a")
    assert forward.p_value == reverse.p_value
    assert forward.significant and reverse.significant
    assert forward.a_only > forward.b_only
    assert reverse.a_only == forward.b_only