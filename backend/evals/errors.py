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
    reports the whole list of problems.
    """

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = list(issues)
        errors = [i for i in self.issues if i.severity == ERROR]
        detail = "\n".join(f"  - {i}" for i in self.issues)
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
    """Accumulates validation issues without deduplicating or discarding them."""

    issues: list[ValidationIssue] = field(default_factory=list)

    def error(self, code: str, location: str, message: str) -> None:
        self.issues.append(ValidationIssue(code, location, message, ERROR))

    def warn(self, code: str, location: str, message: str) -> None:
        self.issues.append(ValidationIssue(code, location, message, WARNING))

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == WARNING]