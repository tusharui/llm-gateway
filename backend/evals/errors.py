"""Error and issue types shared by the eval package.

Every failure in this package is explicit. Nothing is swallowed, and no
problem is reported by silently dropping the affected case -- a dataset that
cannot be trusted cannot be scored.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ERROR = "error"
WARNING = "warning"


class EvalError(Exception):
    """Base class for every error raised by the eval harness."""


@dataclass(frozen=True)
class ValidationIssue:
    """One problem found in the dataset.

    ``severity`` is either ``error`` (evaluation must not run) or ``warning``
    (evaluation runs, but the problem is recorded in ``results.json``).
    """

    code: str
    location: str
    message: str
    severity: str = ERROR

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "location": self.location,
            "message": self.message,
            "severity": self.severity,
        }

    def __str__(self) -> str:
        return f"[{self.severity}] {self.location}: {self.message} ({self.code})"


class DatasetError(EvalError):
    """The dataset file is missing, unreadable, or structurally unusable."""


class DatasetValidationError(EvalError):
    """The dataset is readable but contains cases that must not be evaluated.

    Carries every issue found rather than only the first, so a single run
    reports the whole list of problems. The list is capped: a dataset with
    hundreds of thousands of malformed records would otherwise produce a
    hundred-megabyte exception message that CI cannot display and nobody can
    read. What is dropped is reported, not hidden.
    """

    MAX_LISTED = 50

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = list(issues)
        errors = [i for i in self.issues if i.severity == ERROR]
        shown = self.issues[: self.MAX_LISTED]
        detail = "\n".join(f"  - {i}" for i in shown)
        if len(self.issues) > len(shown):
            detail += (
                f"\n  ... and {len(self.issues) - len(shown)} more "
                f"({len(errors)} error(s) total). Narrow the dataset or fix the first "
                "issues and re-run."
            )
        super().__init__(
            f"{len(errors)} dataset validation error(s) in "
            f"{len(self.issues)} issue(s) total:\n{detail}"
        )


class MetricsError(EvalError):
    """A metric was requested with counts that cannot describe a proportion."""


class BaselineError(EvalError):
    """A baseline could not be fitted or evaluated."""


class DependencyError(EvalError):
    """A required dependency is missing or unusable."""


class GateConfigurationError(EvalError):
    """Quality gates were configured with values that cannot be compared."""


class ResultsWriteError(EvalError):
    """``results.json`` could not be written."""


@dataclass
class IssueCollector:
    """Accumulates validation issues without deduplicating or discarding them.

    Capped, because a systematically malformed dataset produces one issue per
    record and the cap is what keeps a CI log readable. ``dropped`` records how
    many were not stored, so nothing is silently lost.
    """

    issues: list[ValidationIssue] = field(default_factory=list)
    max_issues: int = 20_000
    dropped: int = 0

    def error(self, code: str, location: str, message: str) -> None:
        self._add(ValidationIssue(code, location, message, ERROR))

    def warn(self, code: str, location: str, message: str) -> None:
        self._add(ValidationIssue(code, location, message, WARNING))

    def _add(self, issue: ValidationIssue) -> None:
        if len(self.issues) < self.max_issues:
            self.issues.append(issue)
        else:
            self.dropped += 1

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == WARNING]