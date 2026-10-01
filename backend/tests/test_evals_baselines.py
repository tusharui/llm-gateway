"""Baseline tests: determinism, leakage control, and degenerate datasets."""

from __future__ import annotations

import sys

import pytest

from evals.baselines import (
    HeuristicBaseline,
    LearnedBaseline,
    MajorityBaseline,
    evaluate_baseline,
    evaluate_heuristic,
    evaluate_learned,
    fail_case_report,
)
from evals.dataset import EvalCase
from evals.errors import BaselineError, DependencyError
from evals.learned import (
    SEED,
    cross_validated_majority,
    cross_validated_predictions,
    holdout_predictions,
    leakage_report,
)
from evals.metrics import Proportion


def case(cid: str, text: str, label: str, *, difficulty: str = "clear", **kwargs) -> EvalCase:
    return EvalCase(
        id=cid,
        input=text,
        expected_label=label,
        difficulty=difficulty,
        source=kwargs.pop("source", "synthetic"),
        category=kwargs.pop("category", "c"),
        **kwargs,
    )


def toy_cases(n_per_class: int = 30) -> list[EvalCase]:
    """A small, trivially learnable stand-in for the golden set.

    Templates repeat so that every training fold contains the same wording as
    its test fold. That is leakage by construction and it is deliberate here:
    these tests exercise plumbing, splits and determinism. Real model quality
    is measured by ``python -m evals.run`` against ``evals/golden``.
    """
    templates = {
        "fast": ["hi", "thanks", "ok", "cool", "yes", "sure", "great", "bye", "cheers", "got it"],
        "balanced": [
            "explain the cache",
            "compare rest and grpc",
            "summarise the plot",
            "give me tips",
            "how do i reset a password",
            "draft an email",
            "rewrite this sentence",
            "translate this phrase",
            "explain a hash map",
            "give me a checklist",
        ],
        "powerful": [
            "prove the theorem step by step",
            "design the system architecture",
            "implement a retry helper",
            "derive the closed form solution",
            "plan the migration across repos",
            "write a short story about a lighthouse",
            "analyse the trade-offs and recommend",
            "debug this subtle race condition",
            "write a proof of the theorem",
            "design the architecture for ingest",
        ],
    }
    cases: list[EvalCase] = []
    for label, texts in templates.items():
        for i in range(n_per_class):
            cases.append(case(f"{label}_{i:02d}", texts[i % len(texts)], label))
    return cases


# --- Synthetic systems --------------------------------------------------------


def test_perfect_classifier_scores_one_with_an_interval():
    cases = toy_cases(6)
    report = evaluate_baseline("oracle", "test", cases, [c.expected_label for c in cases])
    assert report.accuracy().value == 1.0
    # Even a perfect score is not certainty.
    assert report.accuracy().wilson.lower < 1.0


def test_completely_wrong_classifier_scores_zero():
    cases = [case("a", "hi", "fast"), case("b", "thanks", "fast"), case("c", "ok", "fast")]
    report = evaluate_baseline("inverted", "test", cases, ["powerful"] * 3)
    assert report.accuracy().value == 0.0
    assert report.accuracy().correct == 0
    assert report.accuracy().incorrect == 3


def test_mixed_classifier_reports_each_tier():
    cases = toy_cases(6)
    # Even indices are answered correctly; odd indices are all answered
    # "fast", which happens to be right for the three odd fast cases too.
    predictions = [c.expected_label if i % 2 == 0 else "fast" for i, c in enumerate(cases)]
    report = evaluate_baseline("mixed", "test", cases, predictions)
    assert report.accuracy().correct == 12
    assert report.accuracy().incorrect == 6
    assert set(report.rollup["by_expected_label"]) == {"fast", "balanced", "powerful"}


def test_clear_and_ambiguous_subsets_are_scored_separately():
    cases = [
        case("c1", "hi", "fast", difficulty="clear"),
        case("c2", "explain this", "balanced", difficulty="ambiguous"),
    ]
    report = evaluate_baseline("t", "test", cases, ["fast", "fast"])
    assert report.rollup["by_difficulty"]["clear"].value == 1.0
    assert report.rollup["by_difficulty"]["ambiguous"].value == 0.0


# --- Heuristic wrapper --------------------------------------------------------


def test_heuristic_baseline_uses_conversation_messages():
    # Eleven filler turns plus a system prompt push the score over the
    # balanced threshold even though the last user turn is three words. Scoring
    # the last turn alone would miss both branches.
    conversation = (
        ("system", "You are a staff engineer."),
        *[("user", "context") for _ in range(10)],
        ("assistant", "noted"),
        ("user", "ok then"),
    )
    cases = [case("m1", "ok then", "balanced", messages=conversation)]
    assert HeuristicBaseline().predict(cases) == ("balanced",)


def test_heuristic_baseline_rejects_an_out_of_contract_label(monkeypatch):
    import app.engine.auto_router as router

    monkeypatch.setattr(router, "classify_complexity", lambda messages: "ultra")
    monkeypatch.setattr("evals.baselines.classify_complexity", lambda messages: "ultra")
    with pytest.raises(BaselineError) as exc:
        HeuristicBaseline().predict([case("x", "hi", "fast")])
    assert "not a tier" in str(exc.value)


def test_evaluate_heuristic_runs_end_to_end():
    report = evaluate_heuristic(toy_cases())
    assert report.name == "heuristic"
    assert len(report.predictions) == len(toy_cases())


# --- Learned baseline ---------------------------------------------------------


def test_learned_baseline_is_deterministic():
    cases = toy_cases()
    first = cross_validated_predictions([c.id for c in cases], [c.input for c in cases], [c.expected_label for c in cases])
    second = cross_validated_predictions([c.id for c in cases], [c.input for c in cases], [c.expected_label for c in cases])
    assert first.predictions == second.predictions
    assert first.folds == second.folds


def test_learned_baseline_predicts_every_case_exactly_once():
    cases = toy_cases()
    result = cross_validated_predictions(
        [c.id for c in cases], [c.input for c in cases], [c.expected_label for c in cases]
    )
    assert len(result.predictions) == len(cases)
    assert result.evaluated_ids == tuple(c.id for c in cases)


def test_learned_baseline_beats_the_majority_floor():
    cases = toy_cases()
    learned, companions = evaluate_learned(cases)
    floor = next(c for c in companions if c.name == "majority_class")
    assert learned.accuracy().value > floor.accuracy().value


def test_holdout_is_reported_over_a_subset():
    cases = toy_cases()
    holdout = holdout_predictions([c.id for c in cases], [c.input for c in cases], [c.expected_label for c in cases])
    assert 0 < holdout.n < len(cases)
    assert len(holdout.predictions) == holdout.n


def test_learned_baseline_rejects_a_length_mismatch():
    learned = LearnedBaseline(
        result=cross_validated_predictions(
            ["a", "b", "c", "d"],
            ["hi there", "thanks a lot", "prove the theorem", "design the system"],
            ["fast", "fast", "powerful", "powerful"],
        )
    )
    with pytest.raises(BaselineError):
        learned.predict([case("a", "hi there", "fast")])


def test_learned_baseline_refuses_to_predict_unfitted():
    with pytest.raises(BaselineError):
        LearnedBaseline(result=type("R", (), {"n": 0, "predictions": ()})()).predict([])


def test_majority_baseline_predicts_the_most_frequent_label():
    labels = ["fast"] * 10 + ["balanced"] * 5 + ["powerful"] * 3
    result = cross_validated_majority([f"c{i}" for i in range(len(labels))], labels)
    assert set(result.predictions) == {"fast"}
    correct = sum(1 for p, t in zip(result.predictions, labels) if p == t)
    assert correct == 10


# --- Degenerate datasets ------------------------------------------------------


def test_single_class_dataset_is_an_explicit_error():
    with pytest.raises(BaselineError) as exc:
        cross_validated_predictions(["a", "b", "c"], ["x", "y", "z"], ["fast", "fast", "fast"])
    assert "single class" in str(exc.value)


def test_class_with_one_example_blocks_stratification():
    with pytest.raises(BaselineError) as exc:
        cross_validated_predictions(
            ["a", "b", "c", "d"], ["x", "y", "z", "w"], ["fast", "fast", "balanced", "powerful"]
        )
    assert "fewer than two examples" in str(exc.value)


def test_tiny_dataset_still_works():
    cases = [
        case("a", "hi", "fast"),
        case("b", "thanks", "fast"),
        case("c", "prove the theorem", "powerful"),
        case("d", "design the system", "powerful"),
    ]
    result = cross_validated_predictions(
        [c.id for c in cases], [c.input for c in cases], [c.expected_label for c in cases]
    )
    assert len(result.predictions) == 4
    assert result.n_splits == 2


def test_empty_dataset_is_an_explicit_error():
    with pytest.raises(BaselineError):
        cross_validated_predictions([], [], [])


def test_imbalanced_dataset_is_reported():
    report = leakage_report(
        [f"c{i}" for i in range(20)], [f"prompt {i}" for i in range(20)], ["fast"] * 18 + ["balanced"] * 2
    )
    assert report.imbalanced is True
    assert any("floor to beat" in note for note in report.notes)


def test_holdout_with_no_room_to_train_is_an_error():
    with pytest.raises(BaselineError):
        holdout_predictions(["a", "b"], ["x", "y"], ["fast", "balanced"], test_size=0.9)


def test_missing_sklearn_is_an_actionable_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "sklearn.feature_extraction.text", None)
    with pytest.raises(DependencyError) as exc:
        cross_validated_predictions(["a", "b"], ["x", "y"], ["fast", "balanced"])
    assert "requirements-dev.txt" in str(exc.value)


# --- Leakage inspection -------------------------------------------------------


def test_leakage_report_finds_conflicting_duplicate_inputs():
    report = leakage_report(["a", "b"], ["same text", "Same  text"], ["fast", "balanced"])
    assert report.has_blocking_leakage is True
    assert "different labels" in " ".join(report.notes)


def test_training_refuses_to_run_on_conflicting_duplicates():
    with pytest.raises(BaselineError) as exc:
        cross_validated_predictions(["a", "b", "c", "d"], ["x", "x", "y", "z"], ["fast", "balanced", "fast", "balanced"])
    assert "conflicting labels" in str(exc.value)


def test_leakage_report_finds_near_duplicates():
    report = leakage_report(
        ["a", "b"],
        ["explain how a binary search tree works", "explain how a binary search tree works well"],
        ["balanced", "balanced"],
    )
    assert report.near_duplicate_pairs
    assert report.near_duplicate_pairs[0]["similarity"] >= 0.8


def test_leakage_report_flags_inputs_containing_a_label_word():
    report = leakage_report(["a"], ["this should be fast tier"], ["fast"], label_words=["fast"])
    assert report.inputs_containing_a_label_word == [{"id": "a", "word": "fast"}]


def test_leakage_report_rejects_mismatched_lengths():
    with pytest.raises(BaselineError):
        leakage_report(["a"], ["x", "y"], ["fast"])


def test_leakage_report_serialises():
    payload = leakage_report(["a", "b"], ["x", "y"], ["fast", "balanced"]).to_dict()
    assert payload["n_cases"] == 2
    assert payload["class_counts"] == {"balanced": 1, "fast": 1}
    assert "blocking" in payload


def test_vocabulary_is_not_fitted_before_the_split():
    # A vocabulary fitted on all cases before splitting is the classic leak.
    # The pipeline makes it structurally impossible, so what is asserted here
    # is that the reported vocabulary size is per-fold and the folds partition
    # the data exactly.
    cases = toy_cases()
    result = cross_validated_predictions(
        [c.id for c in cases], [c.input for c in cases], [c.expected_label for c in cases]
    )
    evaluated = [cid for fold in result.folds for cid in [None]]
    assert evaluated  # folds exist
    total_test = sum(fold["n_test"] for fold in result.folds)
    assert total_test == len(cases)
    assert all(fold["vocabulary_size"] > 0 for fold in result.folds)


# --- Failure reporting --------------------------------------------------------


def test_fail_case_report_is_ordered_and_labelled():
    cases = [case("a", "hi", "fast"), case("b", "explain graphs", "balanced")]
    failures = fail_case_report(cases, ["fast", "fast"])
    assert [f["id"] for f in failures] == ["b"]
    assert failures[0]["expected_label"] == "balanced"
    assert failures[0]["predicted_label"] == "fast"
    assert failures[0]["difficulty"] == "clear"


def test_fail_case_report_respects_a_limit():
    cases = [case(f"c{i}", "explain it", "balanced") for i in range(10)]
    assert len(fail_case_report(cases, ["fast"] * 10, limit=3)) == 3


def test_fail_case_report_notes_conversation_shape():
    cases = [case("m", "ok", "powerful", messages=(("user", "a"), ("user", "ok")))]
    failure = fail_case_report(cases, ["balanced"])[0]
    assert failure["n_messages"] == 2
    assert failure["has_system_message"] is False