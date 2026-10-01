"""Systems under evaluation.

A baseline is anything that maps a list of cases to a list of tier labels,
positionally aligned with the input. Keeping that to one method is what makes
the comparison fair: every system sees the identical case list, and any
difference in score comes from the system rather than from the sampling.

Three are provided:

* ``heuristic`` -- the router's own rule-based classifier, the incumbent.
* ``tfidf_logistic_regression`` -- the learned baseline, scored out-of-fold.
* ``majority_class`` -- a floor. Without it, an accuracy of 0.53 has no
  interpretation; with it, 0.53 versus a floor of 0.39 is a claim and 0.53
  versus a floor of 0.60 is a warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

from app.engine.auto_router import classify_complexity

from evals.dataset import TIERS, EvalCase
from evals.errors import BaselineError
from evals.learned import (
    LearnedResult,
    cross_validated_majority,
    cross_validated_predictions,
    holdout_predictions,
)
from evals.metrics import Proportion, rollup, serialise_rollup


@runtime_checkable
class Baseline(Protocol):
    """Anything that can predict a tier per case, in order."""

    name: str
    kind: str

    def predict(self, cases: Sequence[EvalCase]) -> tuple[str, ...]: ...


@dataclass
class HeuristicBaseline:
    """The production router, wrapped so it is measured like any other system."""

    name: str = "heuristic"
    kind: str = "rule_based"

    def predict(self, cases: Sequence[EvalCase]) -> tuple[str, ...]:
        predictions = []
        for case in cases:
            prediction = classify_complexity(case.chat_messages())
            if prediction not in TIERS:
                # A router that invented a label would silently vanish from
                # every rollup and look like a wrong answer rather than a bug.
                raise BaselineError(
                    f"case {case.id}: classifier returned {prediction!r}, which is not a tier"
                )
            predictions.append(prediction)
        return tuple(predictions)


@dataclass
class LearnedBaseline:
    """TF-IDF + logistic regression, wrapping a fitted result."""

    result: LearnedResult
    name: str = "tfidf_logistic_regression"
    kind: str = "learned"

    def predict(self, cases: Sequence[EvalCase]) -> tuple[str, ...]:
        if self.result.n == 0:
            raise BaselineError("learned baseline has no predictions; it was never fitted")
        if len(self.result.predictions) != len(cases):
            raise BaselineError(
                f"learned baseline was fitted for {len(self.result.predictions)} cases but "
                f"asked to predict {len(cases)}"
            )
        return self.result.predictions


@dataclass
class MajorityBaseline:
    """Floor reference, so a headline accuracy has something to be compared to."""

    result: LearnedResult
    name: str = "majority_class"
    kind: str = "trivial_reference"

    def predict(self, cases: Sequence[EvalCase]) -> tuple[str, ...]:
        if len(self.result.predictions) != len(cases):
            raise BaselineError(
                f"majority baseline covers {len(self.result.predictions)} cases, asked for {len(cases)}"
            )
        return self.result.predictions


@dataclass
class BaselineReport:
    """One system's predictions plus every metric derived from them."""

    name: str
    kind: str
    predictions: tuple[str, ...]
    rollup: dict[str, Any]
    detail: dict[str, Any]
    training: dict[str, Any] | None = None

    def accuracy(self) -> Proportion:
        return self.rollup["overall"]

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "overall": self.rollup["overall"].to_dict(),
            "by_difficulty": {k: v.to_dict() for k, v in sorted(self.rollup["by_difficulty"].items())},
            "by_expected_label": {k: v.to_dict() for k, v in sorted(self.rollup["by_expected_label"].items())},
            "by_difficulty_and_label": {
                difficulty: {tier: prop.to_dict() for tier, prop in sorted(row.items())}
                for difficulty, row in sorted(self.rollup["by_difficulty_and_label"].items())
            },
            "by_category": {k: v.to_dict() for k, v in sorted(self.rollup["by_category"].items())},
            "by_adversarial_tag": {
                k: v.to_dict() for k, v in sorted(self.rollup["by_adversarial_tag"].items())
            },
            "per_class": self.rollup["per_class"],
            "confusion_matrix": self.rollup["confusion_matrix"].to_dict(),
        }
        if self.training is not None:
            payload["training"] = self.training
        return payload


def evaluate_baseline(
    name: str,
    kind: str,
    cases: Sequence[EvalCase],
    predictions: Sequence[str],
    *,
    training: dict[str, Any] | None = None,
    confidence: float = 0.95,
) -> BaselineReport:
    result = rollup(cases, predictions, confidence=confidence)
    return BaselineReport(
        name=name,
        kind=kind,
        predictions=tuple(predictions),
        rollup=result,
        detail=serialise_rollup(result),
        training=training,
    )


def evaluate_heuristic(cases: Sequence[EvalCase], *, confidence: float = 0.95) -> BaselineReport:
    baseline = HeuristicBaseline()
    return evaluate_baseline(
        baseline.name, baseline.kind, cases, baseline.predict(cases), confidence=confidence
    )


def evaluate_learned(
    cases: Sequence[EvalCase],
    *,
    seed: int | None = None,
    n_splits: int | None = None,
    confidence: float = 0.95,
    with_holdout: bool = True,
    with_majority: bool = True,
) -> tuple[BaselineReport, list[BaselineReport]]:
    """Train and score the learned baseline, plus optional companions.

    Returns ``(learned, companions)``. The learned baseline's headline comes
    from out-of-fold predictions; the holdout and the majority floor are
    returned separately so they can never be mistaken for it.
    """
    from evals.learned import SEED

    effective_seed = SEED if seed is None else seed
    ids = [case.id for case in cases]
    texts = [case.input for case in cases]
    labels = [case.expected_label for case in cases]

    result = cross_validated_predictions(
        ids, texts, labels, n_splits=n_splits, seed=effective_seed, label_words=list(TIERS)
    )
    learned = evaluate_baseline(
        "tfidf_logistic_regression",
        "learned",
        cases,
        result.predictions,
        training=result.to_dict(),
        confidence=confidence,
    )

    companions: list[BaselineReport] = []
    if with_holdout:
        holdout = holdout_predictions(ids, texts, labels, seed=effective_seed)
        by_id = dict(zip(holdout.evaluated_ids, holdout.predictions))
        holdout_cases = [case for case in cases if case.id in by_id]
        holdout_report = evaluate_baseline(
            "tfidf_logistic_regression_holdout",
            "learned",
            holdout_cases,
            [by_id[case.id] for case in holdout_cases],
            training=holdout.to_dict(),
            confidence=confidence,
        )
        companions.append(holdout_report)

    if with_majority:
        majority = cross_validated_majority(ids, labels, seed=effective_seed)
        companions.append(
            evaluate_baseline(
                "majority_class",
                "trivial_reference",
                cases,
                majority.predictions,
                training=majority.to_dict(),
                confidence=confidence,
            )
        )
    return learned, companions


def fail_case_report(
    cases: Sequence[EvalCase], predictions: Sequence[str], *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Misclassified cases, for reading rather than counting.

    Sorted deterministically (dataset order) so a manual pass over them is
    reproducible and a diff between two runs shows only what changed.
    """
    failures = [
        {
            "id": case.id,
            "input": case.input,
            "expected_label": case.expected_label,
            "predicted_label": predicted,
            "difficulty": case.difficulty,
            "source": case.source,
            "category": case.category,
            "adversarial": list(case.adversarial),
            "notes": case.notes,
            "n_messages": len(case.chat_messages()),
            "has_system_message": any(m["role"] == "system" for m in case.chat_messages()),
        }
        for case, predicted in zip(cases, predictions)
        if predicted != case.expected_label
    ]
    return failures if limit is None else failures[:limit]


__all__ = [
    "Baseline",
    "BaselineReport",
    "HeuristicBaseline",
    "LearnedBaseline",
    "MajorityBaseline",
    "evaluate_baseline",
    "evaluate_heuristic",
    "evaluate_learned",
    "fail_case_report",
]