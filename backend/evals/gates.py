"""CI quality gates.

A gate is a threshold on a measured number and an explicit decision about
which number. Two decisions are separated on purpose:

* **What is compared** -- ``observed`` uses the point accuracy; ``wilson_lower``
  uses the bottom of the 95% interval. The second is stricter and is the one
  that keeps a lucky run from passing.
* **Where the threshold comes from** -- it must be the measured baseline. A gate
  set above what the system actually achieves is not a gate, it is a way of
  proving the system was never evaluated.

An empty subset fails. That is the whole point of fail-closed: "we did not
measure this" must never read as "this passed".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from evals.baselines import BaselineReport
from evals.errors import GateConfigurationError
from evals.metrics import Proportion

MODE_OBSERVED = "observed"
MODE_WILSON_LOWER = "wilson_lower"
GATE_MODES = (MODE_OBSERVED, MODE_WILSON_LOWER)

DEFAULT_GATE_SYSTEM = "heuristic"

# selector name -> where it lives inside a BaselineReport's rollup
SUBSET_SELECTORS: dict[str, str] = {
    "accuracy": "overall",
    "clear-accuracy": "by_difficulty.clear",
    "ambiguous-accuracy": "by_difficulty.ambiguous",
    "adversarial-accuracy": "adversarial_any",
}


def _resolve(rollup: Mapping[str, Any], path: str) -> Proportion | None:
    node: Any = rollup
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, Proportion) else None


@dataclass(frozen=True)
class GateSpec:
    """One threshold, on one subset, of one system."""

    name: str
    selector: str
    threshold: float
    system: str = DEFAULT_GATE_SYSTEM
    rationale: str = ""

    def metric_path(self) -> str:
        return f"{self.system}.{self.selector.replace('-', '_')}"


@dataclass(frozen=True)
class GateResult:
    name: str
    system: str
    selector: str
    threshold: float
    mode: str
    observed: float | None
    compared: float | None
    ci_lower: float | None
    ci_upper: float | None
    n: int
    passed: bool
    reason: str
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "system": self.system,
            "selector": self.selector,
            "metric_path": self.metric_path(),
            "threshold": self.threshold,
            "mode": self.mode,
            "observed": round(self.observed, 6) if self.observed is not None else None,
            "compared": round(self.compared, 6) if self.compared is not None else None,
            "ci_lower": round(self.ci_lower, 6) if self.ci_lower is not None else None,
            "ci_upper": round(self.ci_upper, 6) if self.ci_upper is not None else None,
            "n": self.n,
            "passed": self.passed,
            "reason": self.reason,
            "rationale": self.rationale,
        }

    def metric_path(self) -> str:
        return f"{self.system}.{self.selector.replace('-', '_')}"

    def describe(self) -> str:
        state = "PASS" if self.passed else "FAIL"
        if self.observed is None:
            return f"{state} {self.name}: {self.reason}"
        interval = ""
        if self.ci_lower is not None and self.ci_upper is not None:
            interval = f", 95% CI {self.ci_lower:.1%}-{self.ci_upper:.1%}"
        compared = f"{self.compared:.1%}" if self.compared is not None else "n/a"
        return (
            f"{state} {self.name}: {self.observed:.1%} (n={self.n}) {interval} "
            f"vs {self.threshold:.1%} on {self.mode}"
            + (f" -- {self.reason}" if self.reason else "")
        )


def _validate_threshold(name: str, threshold: float) -> float:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise GateConfigurationError(f"gate {name}: threshold must be a number, got {threshold!r}")
    value = float(threshold)
    if not 0.0 <= value <= 1.0:
        raise GateConfigurationError(
            f"gate {name}: threshold must be a proportion in [0, 1], got {value}. "
            "If you meant a percentage, pass 0.9 rather than 90."
        )
    return value


def parse_tier_gate(raw: str, *, system: str = DEFAULT_GATE_SYSTEM) -> GateSpec:
    """Parse a ``TIER=THRESHOLD`` gate argument."""
    from evals.dataset import TIERS

    if "=" not in raw:
        raise GateConfigurationError(
            f"--min-tier-accuracy expects TIER=THRESHOLD, got {raw!r}; "
            f"valid tiers are {list(TIERS)}"
        )
    tier, _, threshold_text = raw.partition("=")
    tier = tier.strip()
    if tier not in TIERS:
        raise GateConfigurationError(
            f"--min-tier-accuracy: unknown tier {tier!r}; valid tiers are {list(TIERS)}"
        )
    try:
        threshold = float(threshold_text)
    except ValueError as exc:
        raise GateConfigurationError(
            f"--min-tier-accuracy {tier}: {threshold_text!r} is not a number"
        ) from exc
    return GateSpec(
        name=f"min-{tier}-accuracy",
        selector=f"tier:{tier}",
        threshold=_validate_threshold(tier, threshold),
        system=system,
        rationale=f"per-tier floor for the {tier} tier",
    )


def build_gates(
    *,
    min_accuracy: float | None = None,
    min_clear_accuracy: float | None = None,
    min_ambiguous_accuracy: float | None = None,
    min_adversarial_accuracy: float | None = None,
    tier_gates: Sequence[str] = (),
    system: str = DEFAULT_GATE_SYSTEM,
) -> list[GateSpec]:
    """Build gate specs from CLI arguments, in a stable order."""
    requested = (
        (min_accuracy, "accuracy", "overall routing accuracy"),
        (min_clear_accuracy, "clear-accuracy", "accuracy on cases a human would call obvious"),
        (
            min_ambiguous_accuracy,
            "ambiguous-accuracy",
            "accuracy on cases where two tiers are defensible",
        ),
        (
            min_adversarial_accuracy,
            "adversarial-accuracy",
            "accuracy on cases built to break the router's keyword and length heuristics",
        ),
    )
    specs: list[GateSpec] = []
    for value, selector, rationale in requested:
        if value is None:
            continue
        name = f"min-{selector}"
        specs.append(
            GateSpec(
                name=name,
                selector=selector,
                threshold=_validate_threshold(name, value),
                system=system,
                rationale=rationale,
            )
        )
    for raw in tier_gates:
        specs.append(parse_tier_gate(raw, system=system))
    return specs


def _proportion_for(report: BaselineReport, selector: str) -> Proportion | None:
    if selector in SUBSET_SELECTORS:
        return _resolve(report.rollup, SUBSET_SELECTORS[selector])
    if selector.startswith("tier:"):
        tier = selector.split(":", 1)[1]
        return report.rollup["by_expected_label"].get(tier)
    return None


def evaluate_gates(
    reports: Mapping[str, BaselineReport],
    specs: Iterable[GateSpec],
    *,
    mode: str = MODE_OBSERVED,
) -> list[GateResult]:
    """Evaluate every gate. An unresolvable gate fails, it does not pass."""
    if mode not in GATE_MODES:
        raise GateConfigurationError(f"gate mode must be one of {list(GATE_MODES)}, got {mode!r}")

    results: list[GateResult] = []
    for spec in specs:
        report = reports.get(spec.system)
        if report is None:
            results.append(
                GateResult(
                    name=spec.name,
                    system=spec.system,
                    selector=spec.selector,
                    threshold=spec.threshold,
                    mode=mode,
                    observed=None,
                    compared=None,
                    ci_lower=None,
                    ci_upper=None,
                    n=0,
                    passed=False,
                    reason=(
                        f"system {spec.system!r} was not evaluated, so the gate cannot be "
                        "checked. Failing closed."
                    ),
                    rationale=spec.rationale,
                )
            )
            continue

        proportion = _proportion_for(report, spec.selector)
        if proportion is None:
            results.append(
                GateResult(
                    name=spec.name,
                    system=spec.system,
                    selector=spec.selector,
                    threshold=spec.threshold,
                    mode=mode,
                    observed=None,
                    compared=None,
                    ci_lower=None,
                    ci_upper=None,
                    n=0,
                    passed=False,
                    reason=f"selector {spec.selector!r} does not exist on system {spec.system!r}",
                    rationale=spec.rationale,
                )
            )
            continue

        interval = proportion.wilson
        observed = proportion.value
        if observed is None:
            results.append(
                GateResult(
                    name=spec.name,
                    system=spec.system,
                    selector=spec.selector,
                    threshold=spec.threshold,
                    mode=mode,
                    observed=None,
                    compared=None,
                    ci_lower=None,
                    ci_upper=None,
                    n=0,
                    passed=False,
                    reason=(
                        f"the {spec.selector} subset is empty (n=0), so no accuracy was "
                        "measured. Failing closed rather than reporting a pass."
                    ),
                    rationale=spec.rationale,
                )
            )
            continue

        compared = interval.lower if mode == MODE_WILSON_LOWER else observed
        passed = compared is not None and compared >= spec.threshold
        reason = ""
        if not passed:
            reason = (
                f"{mode} {compared:.4f} is below the threshold {spec.threshold:.4f}"
                if compared is not None
                else "no comparable value"
            )
        results.append(
            GateResult(
                name=spec.name,
                system=spec.system,
                selector=spec.selector,
                threshold=spec.threshold,
                mode=mode,
                observed=observed,
                compared=compared,
                ci_lower=interval.lower if interval.defined else None,
                ci_upper=interval.upper if interval.defined else None,
                n=proportion.n,
                passed=passed,
                reason=reason,
                rationale=spec.rationale,
            )
        )
    return results


def gates_passed(results: Sequence[GateResult]) -> bool:
    return all(result.passed for result in results)


# Selectors a baseline file always covers, in a stable order.
DEFAULT_SELECTORS: tuple[tuple[str, str, str], ...] = (
    ("min-accuracy", "accuracy", "overall routing accuracy"),
    ("min-clear-accuracy", "clear-accuracy", "accuracy on cases a human would call obvious"),
    (
        "min-ambiguous-accuracy",
        "ambiguous-accuracy",
        "accuracy on cases where two tiers are defensible",
    ),
    (
        "min-adversarial-accuracy",
        "adversarial-accuracy",
        "accuracy on cases built to break the router's keyword and length heuristics",
    ),
)


def _round_down(value: float, places: int = 2) -> float:
    factor = 10**places
    return math.floor(value * factor + 1e-9) / factor


def baseline_snapshot(
    reports: Mapping[str, BaselineReport], specs: Sequence[GateSpec], *, mode: str
) -> dict[str, Any]:
    """Measured values that a threshold can legitimately be derived from.

    Written by ``--write-baseline`` and committed, so the origin of every CI
    threshold is a reviewable number rather than a judgement call hidden in a
    workflow diff. The ``thresholds`` list is directly loadable with
    ``--load-baseline``.

    When no gate specs are supplied, thresholds are generated by rounding the
    measurement down to two places and marked ``auto: true``. That is a
    starting point to review, not a finished decision: a human sets the
    committed value.
    """
    measurements: dict[str, Any] = {}
    for name, report in sorted(reports.items()):
        measurements[name] = {
            "overall": report.accuracy().to_dict(),
            "clear": report.rollup["by_difficulty"]["clear"].to_dict(),
            "ambiguous": report.rollup["by_difficulty"]["ambiguous"].to_dict(),
            "adversarial": report.rollup["adversarial_any"].to_dict(),
            "by_tier": {k: v.to_dict() for k, v in sorted(report.rollup["by_expected_label"].items())},
        }

    thresholds: list[dict[str, Any]] = []
    if specs:
        for spec in specs:
            observed = _safe_value(reports.get(spec.system), spec.selector)
            thresholds.append(
                {
                    "name": spec.name,
                    "system": spec.system,
                    "selector": spec.selector,
                    "threshold": spec.threshold,
                    "observed_at_write_time": round(observed, 6) if observed is not None else None,
                    "auto": False,
                    "rationale": spec.rationale,
                }
            )
    else:
        system = DEFAULT_GATE_SYSTEM
        report = reports.get(system)
        for name, selector, rationale in DEFAULT_SELECTORS:
            observed = _safe_value(report, selector)
            thresholds.append(
                {
                    "name": name,
                    "system": system,
                    "selector": selector,
                    "threshold": _round_down(observed) if observed is not None else 0.0,
                    "observed_at_write_time": round(observed, 6) if observed is not None else None,
                    "auto": True,
                    "rationale": rationale,
                }
            )

    return {
        "schema_version": 1,
        "gate_mode": mode,
        "gate_system": DEFAULT_GATE_SYSTEM,
        "measurements": measurements,
        "thresholds": thresholds,
        "note": (
            "Every threshold must be at or below the observed measurement for the same "
            "selector, or the gate fails permanently and people learn to ignore it. "
            "Entries marked auto were rounded down from the measurement and still need a "
            "human decision. Re-measure and re-review whenever the dataset changes."
        ),
    }


def _safe_value(report: BaselineReport | None, selector: str) -> float | None:
    if report is None:
        return None
    proportion = _proportion_for(report, selector)
    return proportion.value if proportion is not None else None


__all__ = [
    "DEFAULT_GATE_SYSTEM",
    "DEFAULT_SELECTORS",
    "GATE_MODES",
    "GateResult",
    "GateSpec",
    "MODE_OBSERVED",
    "MODE_WILSON_LOWER",
    "baseline_snapshot",
    "build_gates",
    "evaluate_gates",
    "gates_passed",
    "parse_tier_gate",
]