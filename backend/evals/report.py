"""Assembly and writing of ``results.json``.

Two properties matter more than the layout:

* **Deterministic content.** Keys are inserted in a fixed order, all iteration
  over sets or dicts is sorted, and nothing depends on filesystem enumeration
  order. Two runs on the same commit and dataset produce byte-identical
  reports apart from the timestamp.
* **Atomic write.** The file is written to a sibling temporary path and then
  renamed with ``os.replace``. An interrupted run leaves the previous
  ``results.json`` intact rather than a truncated file that later looks like a
  real result.

What is deliberately *not* in the file: environment variables, credentials,
file paths outside the repo, or any raw case text beyond the ids the caller
explicitly asks to dump. Production-derived text is redacted before it can
reach this module, and ``evals.production`` enforces that.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from evals.baselines import BaselineReport
from evals.dataset import EvalCase, dataset_summary
from evals.errors import ResultsWriteError, ValidationIssue
from evals.metrics import compare_paired, compare_proportions

RESULTS_SCHEMA_VERSION = 2


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_commit(repo_root: Path) -> str | None:
    """Current commit, or ``None``.

    Runs ``git`` with an argument vector and a timeout. No shell, so nothing
    from the environment or the dataset can be interpreted as a command.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        # git missing, not executable, or timed out. Not an eval failure.
        return None
    if completed.returncode != 0:
        return None
    commit = completed.stdout.strip()
    return commit or None


def _sklearn_version() -> str | None:
    try:
        import sklearn
    except ImportError:
        return None
    return sklearn.__version__


def environment_metadata(repo_root: Path) -> dict[str, Any]:
    """Versions needed to reproduce a number.

    Environment variables are excluded on purpose: ``os.environ`` in this
    process contains the database URL and provider API keys.
    """
    metadata: dict[str, Any] = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.system().lower(),
        "executable_name": Path(sys.executable).name,
        "git_commit": git_commit(repo_root),
    }
    sklearn_version = _sklearn_version()
    if sklearn_version:
        metadata["sklearn_version"] = sklearn_version
    return metadata


def build_report(
    *,
    cases: Sequence[EvalCase],
    reports: Mapping[str, BaselineReport],
    dataset_path: str,
    dataset_warnings: Sequence[ValidationIssue] = (),
    configuration: Mapping[str, Any],
    gate_results: Sequence[Any] = (),
    repo_root: Path,
    failures: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the full results document."""
    names = sorted(reports)

    subsets: dict[str, Any] = {}
    tiers: dict[str, Any] = {}
    adversarial: dict[str, Any] = {}
    confusion: dict[str, Any] = {}
    intervals: dict[str, Any] = {}

    for name in names:
        report = reports[name]
        subsets[name] = {
            difficulty: prop.to_dict() for difficulty, prop in sorted(report.rollup["by_difficulty"].items())
        }
        subsets[name]["all"] = report.accuracy().to_dict()
        tiers[name] = {
            tier: prop.to_dict() for tier, prop in sorted(report.rollup["by_expected_label"].items())
        }
        adversarial[name] = {
            "any": report.rollup["adversarial_any"].to_dict(),
            "by_tag": {k: v.to_dict() for k, v in sorted(report.rollup["by_adversarial_tag"].items())},
        }
        confusion[name] = report.rollup["confusion_matrix"].to_dict()
        intervals[name] = {
            "confidence_level": configuration.get("confidence", 0.95),
            "overall": report.accuracy().to_dict(),
            "clear": report.rollup["by_difficulty"]["clear"].to_dict(),
            "ambiguous": report.rollup["by_difficulty"]["ambiguous"].to_dict(),
            "macro_f1": report.rollup["per_class"]["macro_f1"],
        }

    comparisons: dict[str, Any] = {}
    if "heuristic" in reports:
        heuristic = reports["heuristic"]
        heuristic_by_id = dict(zip(heuristic.case_ids, heuristic.predictions))
        expected_by_id = {case.id: case.expected_label for case in cases}

        for other in names:
            if other == "heuristic":
                continue
            other_report = reports[other]
            other_by_id = dict(zip(other_report.case_ids, other_report.predictions))
            shared = [cid for cid in other_report.case_ids if cid in heuristic_by_id]
            paired = compare_paired(
                [heuristic_by_id[cid] == expected_by_id[cid] for cid in shared],
                [other_by_id[cid] == expected_by_id[cid] for cid in shared],
                name_a="heuristic",
                name_b=other,
            )
            clear = compare_proportions(
                heuristic.rollup["by_difficulty"]["clear"],
                other_report.rollup["by_difficulty"]["clear"],
                label_a="heuristic.clear",
                label_b=f"{other}.clear",
            )
            ambiguous = compare_proportions(
                heuristic.rollup["by_difficulty"]["ambiguous"],
                other_report.rollup["by_difficulty"]["ambiguous"],
                label_a="heuristic.ambiguous",
                label_b=f"{other}.ambiguous",
            )
            comparisons[other] = {
                "paired": paired.to_dict(),
                "paired_description": paired.describe(),
                "paired_cases_compared": len(shared),
                "compared_over_full_dataset": len(shared) == len(cases),
                "clear_vs_clear": clear.to_dict(),
                "ambiguous_vs_ambiguous": ambiguous.to_dict(),
            }
        clear_vs_ambiguous = compare_proportions(
            heuristic.rollup["by_difficulty"]["clear"],
            heuristic.rollup["by_difficulty"]["ambiguous"],
            label_a="heuristic.clear",
            label_b="heuristic.ambiguous",
        )
        comparisons["heuristic.clear_vs_ambiguous"] = {
            "difference": clear_vs_ambiguous.to_dict(),
            "description": clear_vs_ambiguous.describe(),
        }

    document: dict[str, Any] = {
        "schema_version": RESULTS_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "environment": environment_metadata(repo_root),
        "dataset": {
            **dataset_summary(cases),
            "path": dataset_path,
            "warnings": [issue.to_dict() for issue in dataset_warnings],
            "warning_count": len(dataset_warnings),
        },
        "configuration": dict(sorted(configuration.items())),
        "baselines": {name: reports[name].to_dict() for name in names},
        "subsets": subsets,
        "tiers": tiers,
        "adversarial": adversarial,
        "confusion_matrix": confusion,
        "confidence_intervals": intervals,
        "comparisons": comparisons,
        "gates": {
            "mode": configuration.get("gate_mode"),
            "system": configuration.get("gate_system"),
            "all_passed": all(result.passed for result in gate_results) if gate_results else None,
            "checked": len(gate_results),
            "results": [result.to_dict() for result in gate_results],
        },
    }
    if failures is not None:
        document["failures"] = failures
    return document


def write_results(path: str | Path, document: Mapping[str, Any]) -> Path:
    """Write results.json atomically.

    Permissions, a read-only filesystem and a full disk all surface as
    :class:`ResultsWriteError` with the path, rather than leaving a partial file
    behind that a later run would treat as a result.
    """
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ResultsWriteError(
            f"could not create the directory for {target}: {exc.strerror or exc}"
        ) from exc

    payload = json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        temp_name = None
    except PermissionError as exc:
        raise ResultsWriteError(
            f"permission denied writing {target}. Check the directory is writable "
            f"by this user."
        ) from exc
    except OSError as exc:
        raise ResultsWriteError(
            f"could not write {target}: {exc.strerror or exc}. A partial file was "
            "not left behind."
        ) from exc
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                # The rename already failed; leaving a stray temp file is less
                # bad than masking the original error.
                pass
    return target


def write_text_atomic(path: str | Path, payload: str) -> Path:
    """Write arbitrary text atomically, with the same failure behaviour.

    Used for JSONL shards, where the caller supplies the exact bytes and
    ``json.dumps`` would double-encode them.
    """
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ResultsWriteError(
            f"could not create the directory for {target}: {exc.strerror or exc}"
        ) from exc

    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(payload)
            if not payload.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        temp_name = None
    except PermissionError as exc:
        raise ResultsWriteError(f"permission denied writing {target}") from exc
    except OSError as exc:
        raise ResultsWriteError(f"could not write {target}: {exc.strerror or exc}") from exc
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
    return target


def load_baseline_file(path: str | Path) -> dict[str, Any]:
    """Read a committed baseline file, with actionable errors."""
    from evals.errors import DatasetError

    resolved = Path(path)
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DatasetError(
            f"baseline file not found: {resolved}. Generate one with "
            "'python -m evals.run --write-baseline <path>'."
        ) from exc
    except PermissionError as exc:
        raise DatasetError(f"baseline file is not readable: {resolved}") from exc
    except OSError as exc:
        raise DatasetError(f"could not read baseline file {resolved}: {exc.strerror or exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetError(
            f"baseline file {resolved} is not valid JSON: {exc.msg} (line {exc.lineno})"
        ) from exc
    if not isinstance(payload, dict):
        raise DatasetError(f"baseline file {resolved} must contain a JSON object")
    return payload


__all__ = [
    "RESULTS_SCHEMA_VERSION",
    "build_report",
    "environment_metadata",
    "git_commit",
    "load_baseline_file",
    "utc_now_iso",
    "write_results",
    "write_text_atomic",
]