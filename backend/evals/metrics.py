"""Accuracy metrics with honest uncertainty.

Three rules this module exists to enforce:

1. **No bare proportions.** Every reported accuracy carries ``n``, ``correct``
   and a Wilson score interval. ``300/300`` is reported as 1.00 with a lower
   bound of 0.987, not as "100%".
2. **No silent division by zero.** An empty subset produces
   ``value: null`` and ``defined: false``. It never produces 0.0, because a
   0.0 would be read as "failed every case in this subset" instead of "we
   measured nothing".
3. **Differences are tested, not eyeballed.** Two subsets and two systems get
   an interval and a p-value, because "looks better on clear cases" is not a
   finding.

Wilson score interval, for ``correct`` successes out of ``n`` at ``z``::

    centre = (p + z^2 / 2n) / (1 + z^2 / n)
    half   = z / (1 + z^2 / n) * sqrt( p(1 - p)/n + z^2 / 4n^2 )

Wilson was chosen over Wald because Wald collapses to zero width at ``p = 0``
and ``p = 1``, which is exactly where a small golden set lives.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any, Iterable, Mapping, Sequence

from evals.dataset import DIFFICULTIES, TIERS, EvalCase
from evals.errors import MetricsError

DEFAULT_CONFIDENCE = 0.95
_ALPHA = 1.0 - DEFAULT_CONFIDENCE

# Rows are what the dataset says, columns are what the system predicted.
# Stated in the output as well as here, because a transposed confusion matrix
# is indistinguishable from a correct one at a glance.
ORIENTATION = "rows=expected_label(actual), columns=predicted_label"


def z_for(confidence: float = DEFAULT_CONFIDENCE) -> float:
    """Two-sided z multiplier for a confidence level."""
    if not 0.0 < confidence < 1.0:
        raise MetricsError(f"confidence must be in (0, 1), got {confidence!r}")
    return NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)


@dataclass(frozen=True)
class WilsonInterval:
    lower: float
    upper: float
    confidence: float
    z: float
    defined: bool

    def to_dict(self) -> dict[str, Any]:
        key = f"wilson_{int(round(self.confidence * 100))}"
        payload: dict[str, Any] = {"confidence": round(self.confidence, 6), "z": round(self.z, 6)}
        if not self.defined:
            # Explicitly absent rather than 0.0/1.0: an undefined interval is a
            # different fact from a maximally uncertain one.
            payload.update({"lower": None, "upper": None, "defined": False})
            return {key: payload}
        payload.update({"lower": round(self.lower, 6), "upper": round(self.upper, 6), "defined": True})
        return {key: payload}


def wilson_interval(correct: int, n: int, *, confidence: float = DEFAULT_CONFIDENCE) -> WilsonInterval:
    """Wilson score interval for a binomial proportion.

    ``n == 0`` returns an undefined interval rather than raising: an empty
    subset is a legitimate reportable state, and the caller decides whether
    that is a gate failure.
    """
    _validate_counts(correct, n)
    z = z_for(confidence)
    if n == 0:
        return WilsonInterval(lower=0.0, upper=1.0, confidence=confidence, z=z, defined=False)

    p = correct / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denominator
    margin = z / denominator * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return WilsonInterval(
        lower=max(0.0, centre - margin),
        upper=min(1.0, centre + margin),
        confidence=confidence,
        z=z,
        defined=True,
    )


def _validate_counts(correct: int, n: int) -> None:
    if isinstance(correct, bool) or isinstance(n, bool):
        raise MetricsError("counts must be integers, not booleans")
    if not isinstance(correct, int) or not isinstance(n, int):
        raise MetricsError(f"counts must be integers, got correct={correct!r} n={n!r}")
    if n < 0:
        raise MetricsError(f"n must be non-negative, got {n}")
    if correct < 0:
        raise MetricsError(f"correct must be non-negative, got {correct}")
    if correct > n:
        raise MetricsError(f"correct ({correct}) cannot exceed n ({n})")


@dataclass(frozen=True)
class Proportion:
    """An accuracy together with the evidence behind it."""

    correct: int
    n: int
    confidence: float = DEFAULT_CONFIDENCE

    @property
    def value(self) -> float | None:
        """Observed accuracy, or ``None`` when the subset is empty."""
        if self.n == 0:
            return None
        return self.correct / self.n

    @property
    def wilson(self) -> WilsonInterval:
        return wilson_interval(self.correct, self.n, confidence=self.confidence)

    @property
    def incorrect(self) -> int:
        return self.n - self.correct

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "n": self.n,
            "correct": self.correct,
            "incorrect": self.incorrect,
            "value": round(self.value, 6) if self.value is not None else None,
            "defined": self.n > 0,
        }
        payload.update(self.wilson.to_dict())
        return payload

    def __str__(self) -> str:
        if self.n == 0:
            return f"n/a (n=0)"
        interval = self.wilson
        return (
            f"{self.value:.1%} ({self.correct}/{self.n}) "
            f"[{interval.confidence:.0%} CI {interval.lower:.1%}-{interval.upper:.1%}]"
        )


# --- Confusion matrix ---------------------------------------------------------


@dataclass(frozen=True)
class ConfusionMatrix:
    """Counts of expected-vs-predicted, with an explicit orientation."""

    labels: tuple[str, ...]
    cells: dict[str, dict[str, int]]
    orientation: str = ORIENTATION
    support: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(sum(row.values()) for row in self.cells.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "orientation": self.orientation,
            "labels": list(self.labels),
            "rows": list(self.labels),
            "columns": list(self.labels),
            "support": {label: self.support.get(label, 0) for label in self.labels},
            "counts": {row: dict(self.cells[row]) for row in self.labels},
        }

    def off_diagonal_pairs(self) -> list[tuple[str, str, int]]:
        """``(expected, predicted, count)`` for every non-diagonal cell, sorted."""
        pairs = [
            (row, column, self.cells[row][column])
            for row in self.labels
            for column in self.labels
            if row != column and self.cells[row][column] > 0
        ]
        pairs.sort(key=lambda item: (-item[2], item[0], item[1]))
        return pairs

    def accuracy(self) -> float | None:
        if self.total == 0:
            return None
        correct = sum(self.cells[label][label] for label in self.labels)
        return correct / self.total


def confusion_matrix(
    pairs: Sequence[tuple[str, str]],
    *,
    labels: Sequence[str] | None = None,
) -> ConfusionMatrix:
    """Build a confusion matrix from ``(expected, predicted)`` pairs.

    ``labels`` fixes the label universe so the matrix keeps a row and column
    even for a class that was never predicted or never expected. Without it the
    universe is the union of observed labels, sorted, so the output is stable.
    """
    observed = {expected for expected, _ in pairs} | {predicted for _, predicted in pairs}
    if labels is None:
        universe = tuple(sorted(observed))
    else:
        universe = tuple(labels)
        missing = observed - set(universe)
        if missing:
            raise MetricsError(
                f"labels {list(universe)} does not cover observed labels {sorted(missing)}; "
                "refusing to silently drop them"
            )
    if any(not isinstance(label, str) or not label for label in universe):
        raise MetricsError(f"labels must be non-empty strings, got {universe!r}")

    cells: dict[str, dict[str, int]] = {row: {column: 0 for column in universe} for row in universe}
    support: Counter[str] = Counter({label: 0 for label in universe})
    for expected, predicted in pairs:
        if expected not in cells or predicted not in cells:
            raise MetricsError(f"pair ({expected!r}, {predicted!r}) is outside the label universe")
        cells[expected][predicted] += 1
        support[expected] += 1
    return ConfusionMatrix(labels=universe, cells=cells, support=dict(support))


# --- Per-class detail ---------------------------------------------------------


def per_class_report(pairs: Sequence[tuple[str, str]], *, labels: Sequence[str] | None = None) -> dict[str, Any]:
    """Precision, recall, F1 and support per label.

    Reported as supplementary detail. It does not replace accuracy, which is
    what the requested gates are defined on.
    """
    if not pairs:
        return {"per_class": {}, "macro_f1": None, "micro_f1": None, "notes": "no predictions"}

    matrix = confusion_matrix(pairs, labels=labels)
    per_class: dict[str, dict[str, Any]] = {}
    for label in matrix.labels:
        tp = matrix.cells[label][label]
        predicted = sum(matrix.cells[row][label] for row in matrix.labels)
        actual = matrix.support.get(label, 0)
        precision = tp / predicted if predicted else None
        recall = tp / actual if actual else None
        if precision is None or recall is None or (precision + recall) == 0:
            f1 = None
        else:
            f1 = 2 * precision * recall / (precision + recall)
        per_class[label] = {
            "support": actual,
            "predicted": predicted,
            "true_positives": tp,
            "precision": round(precision, 6) if precision is not None else None,
            "recall": round(recall, 6) if recall is not None else None,
            "f1": round(f1, 6) if f1 is not None else None,
        }

    f1_values = [entry["f1"] for entry in per_class.values() if entry["f1"] is not None]
    macro_f1 = sum(f1_values) / len(f1_values) if f1_values else None
    total_correct = sum(entry["true_positives"] for entry in per_class.values())
    micro_f1 = total_correct / matrix.total if matrix.total else None
    return {
        "per_class": per_class,
        "macro_f1": round(macro_f1, 6) if macro_f1 is not None else None,
        "micro_f1": round(micro_f1, 6) if micro_f1 is not None else None,
    }


# --- Rollups ------------------------------------------------------------------


def _proportion(correct: int, n: int, confidence: float) -> Proportion:
    return Proportion(correct=correct, n=n, confidence=confidence)


def rollup(
    cases: Sequence[EvalCase],
    predictions: Sequence[str],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
) -> dict[str, Any]:
    """Overall, per-subset and per-tier accuracy for one system.

    Every group reports ``n`` and a confidence interval, including empty
    groups, which report ``defined: false``. Silently omitting an empty tier
    would hide the fact that a tier was never exercised.
    """
    if len(cases) != len(predictions):
        raise MetricsError(
            f"cases and predictions differ in length: {len(cases)} vs {len(predictions)}; "
            "the score would be computed over a mismatched population"
        )
    for case, predicted in zip(cases, predictions):
        if not isinstance(predicted, str) or not predicted:
            raise MetricsError(f"case {case.id}: predicted label must be a non-empty string")

    correct_flags = [predicted == case.expected_label for case, predicted in zip(cases, predictions)]
    pairs = [(case.expected_label, predicted) for case, predicted in zip(cases, predictions)]

    def group(key) -> dict[str, Proportion]:
        counts: Counter[str] = Counter()
        totals: Counter[str] = Counter()
        for case, is_correct in zip(cases, correct_flags):
            totals[key(case)] += 1
            counts[key(case)] += int(is_correct)
        keys = sorted(totals)
        return {k: _proportion(counts[k], totals[k], confidence) for k in keys}

    by_difficulty: dict[str, Proportion] = {}
    for difficulty in DIFFICULTIES:
        subset = [
            (case, is_correct)
            for case, is_correct in zip(cases, correct_flags)
            if case.difficulty == difficulty
        ]
        by_difficulty[difficulty] = _proportion(
            sum(1 for _, ok in subset if ok), len(subset), confidence
        )

    by_label: dict[str, Proportion] = {}
    for tier in TIERS:
        subset = [
            (case, is_correct)
            for case, is_correct in zip(cases, correct_flags)
            if case.expected_label == tier
        ]
        by_label[tier] = _proportion(sum(1 for _, ok in subset if ok), len(subset), confidence)

    by_difficulty_and_label: dict[str, dict[str, Proportion]] = {}
    for difficulty in DIFFICULTIES:
        row: dict[str, Proportion] = {}
        for tier in TIERS:
            subset = [
                (case, is_correct)
                for case, is_correct in zip(cases, correct_flags)
                if case.difficulty == difficulty and case.expected_label == tier
            ]
            row[tier] = _proportion(sum(1 for _, ok in subset if ok), len(subset), confidence)
        by_difficulty_and_label[difficulty] = row

    adversarial: Counter[str] = Counter()
    adversarial_correct: Counter[str] = Counter()
    adversarial_total_cases = 0
    adversarial_correct_cases = 0
    for case, is_correct in zip(cases, correct_flags):
        if case.adversarial:
            adversarial_total_cases += 1
            adversarial_correct_cases += int(is_correct)
        for tag in case.adversarial:
            adversarial[tag] += 1
            adversarial_correct[tag] += int(is_correct)

    def serialise(mapping: Mapping[str, Proportion]) -> dict[str, Any]:
        return {key: value.to_dict() for key, value in sorted(mapping.items())}

    total_correct = sum(1 for flag in correct_flags if flag)
    matrix = confusion_matrix(pairs, labels=list(TIERS))
    detail = per_class_report(pairs, labels=list(TIERS))

    return {
        "overall": _proportion(total_correct, len(cases), confidence),
        "by_difficulty": by_difficulty,
        "by_expected_label": by_label,
        "by_difficulty_and_label": by_difficulty_and_label,
        "by_category": group(lambda c: c.category),
        "by_source": group(lambda c: c.source),
        "by_adversarial_tag": {
            tag: _proportion(adversarial_correct[tag], adversarial[tag], confidence)
            for tag in sorted(adversarial)
        },
        "adversarial_any": _proportion(
            adversarial_correct_cases, adversarial_total_cases, confidence
        ),
        "confusion_matrix": matrix,
        "per_class": detail,
        "_correct_flags": correct_flags,
    }


def serialise_rollup(result: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-safe view of :func:`rollup` output, in a fixed key order."""
    return {
        "overall": result["overall"].to_dict(),
        "by_difficulty": {k: v.to_dict() for k, v in sorted(result["by_difficulty"].items())},
        "by_expected_label": {k: v.to_dict() for k, v in sorted(result["by_expected_label"].items())},
        "by_difficulty_and_label": {
            difficulty: {tier: prop.to_dict() for tier, prop in sorted(row.items())}
            for difficulty, row in sorted(result["by_difficulty_and_label"].items())
        },
        "by_category": {k: v.to_dict() for k, v in sorted(result["by_category"].items())},
        "by_source": {k: v.to_dict() for k, v in sorted(result["by_source"].items())},
        "by_adversarial_tag": {k: v.to_dict() for k, v in sorted(result["by_adversarial_tag"].items())},
        "adversarial_any": result["adversarial_any"].to_dict(),
        "confusion_matrix": result["confusion_matrix"].to_dict(),
        "per_class": result["per_class"],
    }


# --- Significance -------------------------------------------------------------


@dataclass(frozen=True)
class ProportionDifference:
    """Difference between two independent proportions, with an interval."""

    label_a: str
    label_b: str
    a: Proportion
    b: Proportion
    difference: float | None
    ci_lower: float | None
    ci_upper: float | None
    p_value: float | None
    significant: bool
    method: str = "Newcombe hybrid score interval + pooled two-proportion z-test"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "a": {"name": self.label_a, **self.a.to_dict()},
            "b": {"name": self.label_b, **self.b.to_dict()},
            "difference": round(self.difference, 6) if self.difference is not None else None,
            "difference_ci_lower": round(self.ci_lower, 6) if self.ci_lower is not None else None,
            "difference_ci_upper": round(self.ci_upper, 6) if self.ci_upper is not None else None,
            "p_value": round(self.p_value, 8) if self.p_value is not None else None,
            "significant_at_95": self.significant,
            "method": self.method,
            "note": self.note,
        }

    def describe(self) -> str:
        if self.difference is None:
            return f"{self.label_a} vs {self.label_b}: not comparable ({self.note})"
        verdict = "significant" if self.significant else "not significant"
        return (
            f"{self.label_a} {self.a.value:.1%} vs {self.label_b} {self.b.value:.1%}: "
            f"difference {self.difference:+.1%} "
            f"[{self.ci_lower:+.1%}, {self.ci_upper:+.1%}], {verdict} (p={self.p_value:.4f})"
        )


def compare_proportions(
    a: Proportion,
    b: Proportion,
    *,
    label_a: str = "a",
    label_b: str = "b",
    alpha: float = _ALPHA,
) -> ProportionDifference:
    """Test whether two independent proportions differ.

    Wald intervals for a difference are badly behaved at proportions near 0
    or 1, so the interval is Newcombe's hybrid score method built from the two
    Wilson intervals. The p-value comes from the pooled two-proportion z-test.
    """
    _validate_counts(a.correct, a.n)
    _validate_counts(b.correct, b.n)
    if a.n == 0 or b.n == 0:
        return ProportionDifference(
            label_a, label_b, a, b, None, None, None, None, False,
            note="one subset is empty, so no difference can be estimated",
        )

    p1, p2 = a.value, b.value
    difference = p1 - p2

    l1, u1 = a.wilson.lower, a.wilson.upper
    l2, u2 = b.wilson.lower, b.wilson.upper
    # Newcombe method 10: build the difference interval from the component
    # Wilson intervals rather than from a standard error of the difference.
    ci_lower = difference - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    ci_upper = difference + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    ci_lower = max(-1.0, ci_lower)
    ci_upper = min(1.0, ci_upper)

    pooled = (a.correct + b.correct) / (a.n + b.n)
    standard_error = math.sqrt(pooled * (1.0 - pooled) * (1.0 / a.n + 1.0 / b.n))
    if standard_error == 0.0:
        # Both proportions sit at 0 or 1 with no variance. The difference is
        # exactly the gap between them, and no z-test is defined.
        return ProportionDifference(
            label_a, label_b, a, b, difference, ci_lower, ci_upper, None, False,
            note="degenerate case: both proportions are at the boundary, z-test undefined",
        )

    z = difference / standard_error
    p_value = 2.0 * (1.0 - NormalDist().cdf(abs(z)))
    return ProportionDifference(
        label_a, label_b, a, b, difference, ci_lower, ci_upper, p_value, p_value < alpha
    )


@dataclass(frozen=True)
class PairedComparison:
    """McNemar's exact test between two systems scored on the same cases.

    The systems are not independent -- they predict the identical case list --
    so an unpaired test would overstate the evidence. Discordant pairs carry
    all the information: ``b`` is cases the first system got right and the
    second got wrong.
    """

    name_a: str
    name_b: str
    both_correct: int
    a_only: int
    b_only: int
    both_wrong: int
    p_value: float | None
    significant: bool
    method: str = "exact binomial (McNemar)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "a": self.name_a,
            "b": self.name_b,
            "both_correct": self.both_correct,
            "a_only_correct": self.a_only,
            "b_only_correct": self.b_only,
            "both_wrong": self.both_wrong,
            "discordant": self.a_only + self.b_only,
            "p_value": round(self.p_value, 8) if self.p_value is not None else None,
            "significant_at_95": self.significant,
            "method": self.method,
            "note": "no discordant cases: the systems agree on every case"
            if self.a_only + self.b_only == 0
            else "",
        }

    def describe(self) -> str:
        if self.a_only + self.b_only == 0:
            return f"{self.name_a} vs {self.name_b}: identical outcomes on every case"
        verdict = "significant" if self.significant else "not significant"
        return (
            f"{self.name_a} wins {self.a_only}, {self.name_b} wins {self.b_only} "
            f"on {self.a_only + self.b_only} discordant cases: {verdict} (p={self.p_value:.4f})"
        )


def compare_paired(
    a_correct: Sequence[bool],
    b_correct: Sequence[bool],
    *,
    name_a: str = "a",
    name_b: str = "b",
    alpha: float = _ALPHA,
) -> PairedComparison:
    """Exact McNemar test between two systems over the same cases."""
    if len(a_correct) != len(b_correct):
        raise MetricsError(
            f"paired comparison needs equal lengths, got {len(a_correct)} vs {len(b_correct)}"
        )
    both_correct = sum(1 for a, b in zip(a_correct, b_correct) if a and b)
    a_only = sum(1 for a, b in zip(a_correct, b_correct) if a and not b)
    b_only = sum(1 for a, b in zip(a_correct, b_correct) if b and not a)
    both_wrong = sum(1 for a, b in zip(a_correct, b_correct) if not a and not b)

    discordant = a_only + b_only
    if discordant == 0:
        return PairedComparison(
            name_a, name_b, both_correct, a_only, b_only, both_wrong, None, False
        )
    # Two-sided exact binomial against p = 0.5 on the discordant pairs.
    tail = sum(math.comb(discordant, k) for k in range(0, min(a_only, b_only) + 1))
    p_value = min(1.0, 2.0 * tail / (2.0**discordant))
    return PairedComparison(
        name_a, name_b, both_correct, a_only, b_only, both_wrong, p_value, p_value < alpha
    )


__all__ = [
    "ConfusionMatrix",
    "DEFAULT_CONFIDENCE",
    "ORIENTATION",
    "PairedComparison",
    "Proportion",
    "ProportionDifference",
    "WilsonInterval",
    "compare_paired",
    "compare_proportions",
    "confusion_matrix",
    "per_class_report",
    "rollup",
    "serialise_rollup",
    "wilson_interval",
    "z_for",
]