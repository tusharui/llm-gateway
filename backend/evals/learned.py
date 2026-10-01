"""TF-IDF + Logistic Regression baseline, with the leakage checks that make
its score mean anything.

The baseline is intentionally simple. The point is not to build the best
possible classifier -- it is to have a second, independent opinion that can be
compared against the router on identical cases, so that "the heuristic is at
0.53" is a statement about the heuristic and not about the measurement.

## Why out-of-fold predictions are the headline

Every case is predicted by a model that never saw it: ``StratifiedKFold`` with
a fixed seed, fitting the vectoriser and the classifier on training folds only.
That makes the learned baseline's numbers directly comparable with the
heuristic's, because both are scored over all cases.

A single 80/20 holdout is also reported, but it is reported separately and
labelled as such. It uses fewer cases, its interval is wider, and if anyone
ever tunes against it, it stops being an estimate of anything.

## Leakage controls

* The sklearn ``Pipeline`` fits the vectoriser inside ``fit`` on the training
  fold only. A vocabulary fitted on all 379 documents before splitting is the
  single most common way an eval of this shape leaks, and it is structurally
  impossible here rather than merely avoided.
* No feature is derived from the label. The vectoriser sees ``case.input`` and
  nothing else -- not the tier, not the difficulty, not the category, not the
  shard the case came from.
* Exact duplicates are rejected upstream in ``evals.dataset``; this module
  re-checks and reports rather than trusting that.
* Near-duplicate pairs are reported per fold. If a pair lands on opposite sides
  of a split, the fold's score is inflated and that is stated.
* The gap between training accuracy and out-of-fold accuracy is reported. A
  large gap means the model memorised, and the out-of-fold number is the
  pessimistic and correct one.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Sequence

from evals.dataset import normalize_for_duplicates
from evals.errors import BaselineError, DependencyError

# Fixed so two runs on the same data produce identical predictions.
SEED = 20250101

# word-boundary token pattern that keeps single-character words, unlike the
# sklearn default of 2+. Prompts like "a" and "R" carry signal.
_TOKEN_PATTERN = r"(?u)\b\w+\b"


def require_sklearn() -> dict[str, Any]:
    """Import sklearn or raise an actionable error.

    A missing optional dependency must never be turned into a silently
    skipped baseline: a report that quietly omits half its numbers is worse
    than one that fails.
    """
    try:
        import sklearn
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import StratifiedKFold, train_test_split
        from sklearn.pipeline import Pipeline
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise DependencyError(
            "scikit-learn is required for the learned baseline. Install the eval "
            "dependencies with: pip install -r requirements-dev.txt  "
            f"(import failed: {exc})"
        ) from exc
    return {
        "version": sklearn.__version__,
        "TfidfVectorizer": TfidfVectorizer,
        "LogisticRegression": LogisticRegression,
        "StratifiedKFold": StratifiedKFold,
        "train_test_split": train_test_split,
        "Pipeline": Pipeline,
    }


def _tokens(text: str) -> frozenset[str]:
    import re

    return frozenset(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


@dataclass
class LeakageReport:
    """Findings from the pre-training inspection pass."""

    n: int
    class_counts: dict[str, int]
    duplicate_input_groups: list[list[str]] = field(default_factory=list)
    duplicate_input_with_conflicting_labels: list[dict[str, Any]] = field(default_factory=list)
    near_duplicate_pairs: list[dict[str, Any]] = field(default_factory=list)
    cross_fold_near_duplicates: list[dict[str, Any]] = field(default_factory=list)
    inputs_containing_a_label_word: list[dict[str, str]] = field(default_factory=list)
    imbalanced: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def has_blocking_leakage(self) -> bool:
        return bool(self.duplicate_input_with_conflicting_labels)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_cases": self.n,
            "class_counts": dict(sorted(self.class_counts.items())),
            "class_imbalance_ratio": (
                round(max(self.class_counts.values()) / min(self.class_counts.values()), 3)
                if self.class_counts and min(self.class_counts.values()) > 0
                else None
            ),
            "imbalanced": self.imbalanced,
            "duplicate_input_groups": self.duplicate_input_groups,
            "duplicate_input_with_conflicting_labels": self.duplicate_input_with_conflicting_labels,
            "near_duplicate_pair_count": len(self.near_duplicate_pairs),
            "near_duplicate_pairs": self.near_duplicate_pairs,
            "cross_fold_near_duplicates": self.cross_fold_near_duplicates,
            "inputs_containing_a_label_word": self.inputs_containing_a_label_word,
            "blocking": self.has_blocking_leakage,
            "notes": list(self.notes),
        }


def leakage_report(
    ids: Sequence[str],
    texts: Sequence[str],
    labels: Sequence[str],
    *,
    label_words: Sequence[str] = (),
    near_duplicate_threshold: float = 0.8,
) -> LeakageReport:
    """Inspect the data for leakage before anything is fitted.

    Run before training, not after, so a finding can stop the run rather than
    annotate a number that is already wrong.
    """
    if not (len(ids) == len(texts) == len(labels)):
        raise BaselineError(
            f"leakage_report needs equal lengths, got {len(ids)}/{len(texts)}/{len(labels)}"
        )
    counts = Counter(labels)
    report = LeakageReport(n=len(ids), class_counts=dict(counts))

    by_text: dict[str, list[int]] = {}
    for index, text in enumerate(texts):
        by_text.setdefault(normalize_for_duplicates(text), []).append(index)

    for key, indices in sorted(by_text.items()):
        if len(indices) < 2:
            continue
        group_ids = [ids[i] for i in indices]
        report.duplicate_input_groups.append(group_ids)
        group_labels = {labels[i] for i in indices}
        if len(group_labels) > 1:
            report.duplicate_input_with_conflicting_labels.append(
                {"ids": group_ids, "labels": sorted(group_labels)}
            )

    token_sets = [_tokens(text) for text in texts]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if min(len(token_sets[i]), len(token_sets[j])) < 4:
                continue
            score = _jaccard(token_sets[i], token_sets[j])
            if score >= near_duplicate_threshold:
                report.near_duplicate_pairs.append(
                    {"a": ids[i], "b": ids[j], "similarity": round(score, 4)}
                )

    if label_words:
        import re

        for case_id, text in zip(ids, texts):
            for word in label_words:
                if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE):
                    report.inputs_containing_a_label_word.append({"id": case_id, "word": word})
                    break

    if counts and min(counts.values()) > 0:
        ratio = max(counts.values()) / min(counts.values())
        report.imbalanced = ratio >= 3.0
        if report.imbalanced:
            report.notes.append(
                f"class ratio {ratio:.1f}:1; a majority-class predictor would reach "
                f"{max(counts.values()) / len(labels):.1%}, which is the floor to beat"
            )
    if report.duplicate_input_with_conflicting_labels:
        report.notes.append(
            "identical inputs carry different labels: either the label is wrong or the "
            "tier definition does not determine it. The learned baseline cannot be trusted "
            "until this is resolved."
        )
    if report.inputs_containing_a_label_word:
        report.notes.append(
            f"{len(report.inputs_containing_a_label_word)} input(s) contain a label word; "
            "the vectoriser could learn a shortcut that has nothing to do with complexity"
        )
    return report


def annotate_cross_fold_near_duplicates(
    report: LeakageReport,
    fold_case_ids: Sequence[Sequence[str]],
) -> LeakageReport:
    """Record near-duplicate pairs that straddle a fold boundary."""
    fold_of: dict[str, int] = {}
    for index, fold in enumerate(fold_case_ids):
        for case_id in fold:
            fold_of[case_id] = index
    for pair in report.near_duplicate_pairs:
        left, right = fold_of.get(pair["a"]), fold_of.get(pair["b"])
        if left is None or right is None or left == right:
            continue
        report.cross_fold_near_duplicates.append(
            {
                "a": pair["a"],
                "b": pair["b"],
                "similarity": pair["similarity"],
                "fold_a": left,
                "fold_b": right,
                "effect": "the same or near-same text appears in both train and test",
            }
        )
    if report.cross_fold_near_duplicates:
        report.notes.append(
            f"{len(report.cross_fold_near_duplicates)} near-duplicate pair(s) straddle a fold "
            "boundary; out-of-fold accuracy on those cases is optimistic"
        )
    return report


@dataclass
class LearnedResult:
    """Out-of-fold or holdout predictions plus the evidence around them."""

    name: str
    method: str
    predictions: tuple[str, ...]
    n: int
    seed: int
    n_splits: int | None
    vocabulary_size: int | None
    train_accuracy: float | None
    evaluated_ids: tuple[str, ...]
    folds: list[dict[str, Any]] = field(default_factory=list)
    leakage: LeakageReport | None = None
    notes: list[str] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "method": self.method,
            "n_evaluated": self.n,
            "seed": self.seed,
            "n_splits": self.n_splits,
            "vocabulary_size": self.vocabulary_size,
            "train_accuracy": round(self.train_accuracy, 6) if self.train_accuracy is not None else None,
            "evaluated_ids": list(self.evaluated_ids),
            "folds": self.folds,
            "leakage": self.leakage.to_dict() if self.leakage else None,
            "notes": list(self.notes),
            "versions": dict(sorted(self.versions.items())),
        }


def _make_pipeline(sk: dict[str, Any]):
    """Word 1-2 gram TF-IDF into L2-regularised multinomial logistic regression.

    Unigrams catch the signal that matters ("explain", "hi"); bigrams catch
    "system design" without needing a phrase list. ``strip_accents="unicode"``
    is left on deliberately so accented prompts are not split into fragments.

    ``lbfgs`` with ``C=1.0``: deterministic for a fixed data order and seed,
    and fast enough to refit five times per evaluation. ``newton-cholesky``
    was tried for its cleaner warning profile and rejected -- it forms a dense
    Hessian over every n-gram feature, which is far too slow at this
    vocabulary size.
    """
    return sk["Pipeline"](
        [
            (
                "tfidf",
                sk["TfidfVectorizer"](
                    lowercase=True,
                    strip_accents="unicode",
                    ngram_range=(1, 2),
                    min_df=1,
                    sublinear_tf=True,
                    token_pattern=_TOKEN_PATTERN,
                ),
            ),
            (
                "clf",
                sk["LogisticRegression"](
                    C=1.0,
                    max_iter=5000,
                    solver="lbfgs",
                    random_state=SEED,
                ),
            ),
        ]
    )


def cross_validated_predictions(
    ids: Sequence[str],
    texts: Sequence[str],
    labels: Sequence[str],
    *,
    n_splits: int | None = None,
    seed: int = SEED,
    label_words: Sequence[str] = (),
) -> LearnedResult:
    """Out-of-fold predictions for every case.

    Every returned prediction comes from a model that never saw that case, so
    the resulting accuracy is comparable with any other system's accuracy over
    the same case list.
    """
    sk = require_sklearn()
    if len(ids) != len(texts) or len(texts) != len(labels):
        raise BaselineError(
            f"cross_validated_predictions needs equal lengths, got {len(ids)}/{len(texts)}/{len(labels)}"
        )
    if not ids:
        raise BaselineError("cannot train on an empty dataset")

    label_list = [str(label) for label in labels]
    counts = Counter(label_list)
    if len(counts) < 2:
        raise BaselineError(
            f"cannot train with a single class present (found {sorted(counts)}); "
            "a classifier needs at least two to have anything to learn"
        )
    smallest = min(counts.values())
    if smallest < 2:
        rare = sorted(k for k, v in counts.items() if v < 2)
        raise BaselineError(
            f"class(es) {rare} have fewer than two examples, so a stratified split is "
            "impossible; add cases for those tiers before training"
        )

    report = leakage_report(ids, texts, label_list, label_words=label_words)
    if report.has_blocking_leakage:
        raise BaselineError(
            "identical inputs carry conflicting labels, so no model can be fitted to this "
            f"data honestly: {report.duplicate_input_with_conflicting_labels}"
        )

    effective_splits = n_splits if n_splits is not None else min(5, smallest, len(ids))
    effective_splits = max(2, min(int(effective_splits), smallest, len(ids)))

    out_of_fold: list[str | None] = [None] * len(ids)
    fold_case_ids: list[list[str]] = []
    folds: list[dict[str, Any]] = []
    train_correct = train_total = 0
    vocab_sizes: list[int] = []

    splitter = sk["StratifiedKFold"](n_splits=effective_splits, shuffle=True, random_state=seed)
    for fold_index, (train_index, test_index) in enumerate(splitter.split(list(texts), label_list)):
        pipeline = _make_pipeline(sk)
        pipeline.fit([texts[i] for i in train_index], [label_list[i] for i in train_index])
        fold_test = [texts[i] for i in test_index]
        fold_labels = [label_list[i] for i in test_index]
        fold_predictions = pipeline.predict(fold_test)
        for position, prediction in zip(test_index, fold_predictions):
            out_of_fold[position] = str(prediction)

        train_predictions = pipeline.predict([texts[i] for i in train_index])
        train_correct += sum(
            1 for p, t in zip(train_predictions, [label_list[i] for i in train_index]) if str(p) == t
        )
        train_total += len(train_index)
        vocab = len(pipeline.named_steps["tfidf"].vocabulary_)
        vocab_sizes.append(vocab)
        fold_case_ids.append([ids[i] for i in test_index])
        folds.append(
            {
                "fold": fold_index,
                "n_train": len(train_index),
                "n_test": len(test_index),
                "train_label_counts": dict(sorted(Counter(label_list[i] for i in train_index).items())),
                "test_label_counts": dict(sorted(Counter(fold_labels).items())),
                "test_correct": sum(1 for p, t in zip(fold_predictions, fold_labels) if str(p) == t),
                "vocabulary_size": vocab,
            }
        )

    missing = [ids[i] for i, p in enumerate(out_of_fold) if p is None]
    if missing:
        # StratifiedKFold covers every index exactly once; an uncovered index
        # would mean a silent hole in the score.
        raise BaselineError(f"internal error: {len(missing)} case(s) were never predicted: {missing[:5]}")

    annotate_cross_fold_near_duplicates(report, fold_case_ids)

    train_accuracy = train_correct / train_total if train_total else None
    notes = list(report.notes)
    if train_accuracy is not None:
        notes.append(
            "train_accuracy is measured on the fitted folds and is optimistic by "
            "construction; the out-of-fold score is the one to quote"
        )
    if effective_splits < 5:
        notes.append(
            f"only {effective_splits} folds were possible (smallest class has {smallest} "
            "cases), so each fold's test set is small and the interval is wider"
        )

    return LearnedResult(
        name="tfidf_logistic_regression",
        method="stratified k-fold out-of-fold predictions (vectoriser fitted per training fold)",
        predictions=tuple(str(p) for p in out_of_fold),
        n=len(ids),
        seed=seed,
        n_splits=effective_splits,
        vocabulary_size=max(vocab_sizes) if vocab_sizes else None,
        train_accuracy=train_accuracy,
        evaluated_ids=tuple(ids),
        folds=folds,
        leakage=report,
        notes=notes,
        versions={
            "python_implementation": "evals.learned",
            "sklearn": sk["version"],
        },
    )


def holdout_predictions(
    ids: Sequence[str],
    texts: Sequence[str],
    labels: Sequence[str],
    *,
    test_size: float = 0.25,
    seed: int = SEED,
) -> LearnedResult:
    """Single stratified holdout, reported separately from the headline.

    Fewer evaluated cases and a wider interval than the out-of-fold number.
    If this is ever used to make a decision, it stops being a holdout.
    """
    sk = require_sklearn()
    if not 0.0 < test_size < 1.0:
        raise BaselineError(f"test_size must be in (0, 1), got {test_size}")
    label_list = [str(label) for label in labels]
    counts = Counter(label_list)
    if len(counts) < 2:
        raise BaselineError(f"cannot split a dataset with a single class (found {sorted(counts)})")

    n_test = max(1, int(round(len(ids) * test_size)))
    n_test = max(n_test, len(counts))  # one per class at minimum
    if n_test >= len(ids):
        raise BaselineError(
            f"test_size={test_size} leaves no training data for {len(ids)} case(s)"
        )

    train_index, test_index = sk["train_test_split"](
        list(range(len(ids))),
        test_size=n_test,
        random_state=seed,
        stratify=label_list if min(counts.values()) >= 2 else None,
    )
    pipeline = _make_pipeline(sk)
    pipeline.fit([texts[i] for i in train_index], [label_list[i] for i in train_index])
    predictions = pipeline.predict([texts[i] for i in test_index])
    train_predictions = pipeline.predict([texts[i] for i in train_index])
    train_correct = sum(
        1 for p, t in zip(train_predictions, [label_list[i] for i in train_index]) if str(p) == t
    )

    return LearnedResult(
        name="tfidf_logistic_regression_holdout",
        method=f"single stratified holdout, test_size={test_size}",
        predictions=tuple(str(p) for p in predictions),
        n=len(test_index),
        seed=seed,
        n_splits=None,
        vocabulary_size=len(pipeline.named_steps["tfidf"].vocabulary_),
        train_accuracy=train_correct / len(train_index) if len(train_index) else None,
        evaluated_ids=tuple(ids[i] for i in test_index),
        folds=[
            {
                "n_train": len(train_index),
                "n_test": len(test_index),
                "test_label_counts": dict(sorted(Counter(label_list[i] for i in test_index).items())),
            }
        ],
        leakage=None,
        notes=[
            "reported separately from the out-of-fold score; it evaluates "
            f"{len(test_index)} case(s) and must not be used for tuning"
        ],
        versions={"sklearn": sk["version"]},
    )


def cross_validated_majority(
    ids: Sequence[str], labels: Sequence[str], *, seed: int = SEED
) -> LearnedResult:
    """Majority-class floor, out-of-fold like everything else.

    Included so a headline accuracy has a reference point: a classifier that
    cannot beat the most frequent tier has not learned anything.

    Uses the same stratified folds as the learned baseline, so the two floor
    and headline numbers are measured over the same held-out sets. An
    unstratified split would make the floor artificially weak or strong
    depending on which labels happened to land in which chunk, which is the
    opposite of a useful reference.
    """
    sk = require_sklearn()
    label_list = [str(label) for label in labels]
    if not label_list:
        raise BaselineError("cannot compute a majority baseline on an empty dataset")
    counts = Counter(label_list)
    if len(counts) < 2:
        raise BaselineError("a majority-class baseline needs at least two classes")
    smallest = min(counts.values())
    if smallest < 2:
        rare = sorted(k for k, v in counts.items() if v < 2)
        raise BaselineError(f"class(es) {rare} cannot be stratified; add cases first")

    n_splits = max(2, min(5, smallest, len(label_list)))
    predictions: list[str | None] = [None] * len(label_list)
    fold_case_ids: list[list[str]] = []
    folds: list[dict[str, Any]] = []

    splitter = sk["StratifiedKFold"](n_splits=n_splits, shuffle=True, random_state=seed)
    for fold_index, (train_index, test_index) in enumerate(splitter.split(list(label_list), label_list)):
        train_labels = [label_list[i] for i in train_index]
        majority = Counter(train_labels).most_common(1)[0][0]
        for i in test_index:
            predictions[i] = majority
        fold_case_ids.append([ids[i] for i in test_index])
        folds.append(
            {
                "fold": fold_index,
                "n_train": len(train_index),
                "n_test": len(test_index),
                "predicted_label": majority,
                "train_label_counts": dict(sorted(Counter(train_labels).items())),
            }
        )

    if any(p is None for p in predictions):
        raise BaselineError("internal error: majority baseline left a case unpredicted")

    return LearnedResult(
        name="majority_class",
        method=f"most frequent training label per fold, {n_splits} stratified folds (floor reference)",
        predictions=tuple(str(p) for p in predictions),
        n=len(label_list),
        seed=seed,
        n_splits=n_splits,
        vocabulary_size=None,
        train_accuracy=None,
        evaluated_ids=tuple(ids),
        folds=folds,
        leakage=None,
        notes=["floor reference: anything at or below this has learned nothing about complexity"],
        versions={"sklearn": sk["version"]},
    )


__all__ = [
    "LeakageReport",
    "LearnedResult",
    "SEED",
    "annotate_cross_fold_near_duplicates",
    "cross_validated_majority",
    "cross_validated_predictions",
    "holdout_predictions",
    "leakage_report",
    "require_sklearn",
]