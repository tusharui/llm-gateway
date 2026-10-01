"""End-to-end runner tests: exit codes, production failure modes, security.

These exercise ``python -m evals.run`` as a process entry point would, because
that is the only interface CI actually uses.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from evals import report as report_module
from evals.errors import ResultsWriteError
from evals.run import main
from evals.report import build_report, environment_metadata, write_results, write_text_atomic

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = REPO_ROOT / "evals" / "golden"

GOOD_CASE = {
    "id": "unit_clear_01",
    "input": "What is the capital of France?",
    "expected_label": "fast",
    "difficulty": "clear",
    "source": "synthetic",
    "category": "factual_lookup",
}


def write_dataset(path: Path, cases, *, version: int = 2) -> Path:
    if path.suffix == ".jsonl":
        path.write_text("\n".join(json.dumps(c) for c in cases), encoding="utf-8")
    else:
        path.write_text(json.dumps({"version": version, "cases": cases}), encoding="utf-8")
    return path


def run(tmp_path: Path, *args: str) -> int:
    """Invoke the runner with results redirected into tmp_path."""
    argv = ["--results", str(tmp_path / "results.json"), "--quiet", *args]
    return main(argv)


def tiny_dataset(path: Path) -> Path:
    cases = [
        {"id": "a", "input": "hi", "expected_label": "fast", "difficulty": "clear", "category": "greeting"},
        {"id": "b", "input": "thanks", "expected_label": "fast", "difficulty": "clear", "category": "thanks"},
        {"id": "c", "input": "explain the cache", "expected_label": "balanced", "difficulty": "clear", "category": "explanation"},
        {"id": "d", "input": "explain it", "expected_label": "balanced", "difficulty": "ambiguous", "category": "explanation"},
        {"id": "e", "input": "prove the theorem step by step", "expected_label": "powerful", "difficulty": "clear", "category": "code"},
        {"id": "f", "input": "design the architecture", "expected_label": "powerful", "difficulty": "ambiguous", "category": "code"},
    ]
    return write_dataset(path, cases)


# --- Happy path ---------------------------------------------------------------


def test_run_without_gates_succeeds(tmp_path):
    assert run(tmp_path, "--dataset", str(tiny_dataset(tmp_path / "g.jsonl")), "--no-learned") == 0


def test_run_writes_results(tmp_path):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned") == 0
    document = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert document["dataset"]["total_cases"] == 6
    assert document["baselines"]["heuristic"]["overall"]["n"] == 6


def test_no_save_writes_nothing(tmp_path):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned", "--no-save") == 0
    assert not (tmp_path / "results.json").exists()


def test_report_flag_runs(tmp_path, capsys):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    code = main(["--dataset", str(dataset), "--no-learned", "--report", "--no-save"])
    assert code == 0
    output = capsys.readouterr().out
    assert "expected" in output
    assert "disagreement" in output


# --- Gates --------------------------------------------------------------------


def test_gate_passes_and_returns_zero(tmp_path):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned", "--min-accuracy", "0.1") == 0


def test_gate_fails_and_returns_one(tmp_path):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned", "--min-accuracy", "0.99") == 1


def test_clear_gate_fails_and_returns_one(tmp_path):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned", "--min-clear-accuracy", "0.99") == 1


def test_ambiguous_gate_fails_and_returns_one(tmp_path):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned", "--min-ambiguous-accuracy", "0.99") == 1


def test_ambiguous_gate_on_a_dataset_with_no_ambiguous_cases_fails_closed(tmp_path):
    cases = [dict(GOOD_CASE, id=f"c{i}", input=f"prompt {i}") for i in range(4)]
    dataset = write_dataset(tmp_path / "g.jsonl", cases)
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned", "--min-ambiguous-accuracy", "0.1") == 1


def test_wilson_lower_mode_is_stricter(tmp_path):
    # The tiny dataset scores 4/6, so the point estimate clears 0.5 while the
    # lower Wilson bound does not. That gap is exactly what the mode exists for.
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned", "--min-accuracy", "0.5") == 0
    assert (
        run(tmp_path, "--dataset", str(dataset), "--no-learned", "--min-accuracy", "0.5", "--gate-on", "wilson_lower")
        == 1
    )


def test_gate_on_an_unevaluated_system_fails(tmp_path):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned", "--gate-system", "typo", "--min-accuracy", "0.1") == 1


# --- Dataset failure modes ----------------------------------------------------


def test_missing_dataset_returns_two(tmp_path):
    assert run(tmp_path, "--dataset", str(tmp_path / "absent.json")) == 2


def test_empty_dataset_file_returns_two(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert run(tmp_path, "--dataset", str(path)) == 2


def test_corrupt_json_returns_two(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text('{"version": 2, "cases": [ {"id": "a", }]}', encoding="utf-8")
    assert run(tmp_path, "--dataset", str(path)) == 2


def test_broken_jsonl_line_returns_two(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text('{"id":"a"}\n{"id":"b",,}\n', encoding="utf-8")
    assert run(tmp_path, "--dataset", str(path)) == 2


def test_dataset_with_an_invalid_case_returns_two(tmp_path):
    path = write_dataset(tmp_path / "g.jsonl", [{"id": "a", "input": "hi", "expected_label": "ultra", "difficulty": "clear"}])
    assert run(tmp_path, "--dataset", str(path)) == 2


def test_dataset_with_a_duplicate_id_returns_two(tmp_path):
    cases = [dict(GOOD_CASE), dict(GOOD_CASE)]
    assert run(tmp_path, "--dataset", str(write_dataset(tmp_path / "g.jsonl", cases))) == 2


def test_dataset_with_zero_cases_returns_two(tmp_path):
    path = tmp_path / "none.json"
    path.write_text(json.dumps({"version": 2, "cases": []}), encoding="utf-8")
    assert run(tmp_path, "--dataset", str(path)) == 2


def test_shard_directory_with_no_shards_returns_two(tmp_path):
    empty = tmp_path / "shards"
    empty.mkdir()
    assert run(tmp_path, "--dataset", str(empty)) == 2


def test_dataset_written_as_a_file_instead_of_a_directory_returns_two(tmp_path):
    # parent is a file, so mkdir for the temp file must fail cleanly.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    assert run(tmp_path, "--dataset", str(tiny_dataset(tmp_path / "g.jsonl")), "--results", str(blocker / "r.json")) == 2


# --- Configuration failure modes ----------------------------------------------


@pytest.mark.parametrize("flag", ["--min-accuracy", "--min-clear-accuracy", "--min-ambiguous-accuracy"])
def test_out_of_range_threshold_returns_two(tmp_path, flag):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), flag, "1.5") == 2


def test_unknown_tier_gate_returns_two(tmp_path):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--min-tier-accuracy", "bogus=0.5") == 2


def test_invalid_confidence_returns_two(tmp_path):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--confidence", "2.0") == 2


def test_missing_baseline_file_returns_two(tmp_path):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--load-baseline", str(tmp_path / "nope.json")) == 2


def test_corrupt_baseline_file_returns_two(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text("{not json", encoding="utf-8")
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--load-baseline", str(path)) == 2


def test_baseline_file_without_thresholds_returns_two(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--load-baseline", str(path)) == 2


def test_committed_baseline_file_loads_and_passes(tmp_path):
    baseline = REPO_ROOT / "evals" / "baseline_gates.json"
    assert run(tmp_path, "--dataset", str(GOLDEN), "--load-baseline", str(baseline), "--no-save") == 0


# --- Dependency handling ------------------------------------------------------


def test_missing_sklearn_returns_three(tmp_path, monkeypatch):
    import evals.learned as learned

    def boom():
        from evals.errors import DependencyError

        raise DependencyError("scikit-learn is required. pip install -r requirements-dev.txt")

    monkeypatch.setattr(learned, "require_sklearn", boom)
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset)) == 3


def test_no_learned_skips_the_dependency_entirely(tmp_path, monkeypatch):
    import evals.learned as learned

    def boom():
        from evals.errors import DependencyError

        raise DependencyError("sklearn is required")

    monkeypatch.setattr(learned, "require_sklearn", boom)
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned") == 0


# --- Writing results ----------------------------------------------------------


def test_write_failure_does_not_clobber_the_previous_results(tmp_path, monkeypatch):
    target = tmp_path / "results.json"
    target.write_text('{"previous": true}', encoding="utf-8")

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(report_module.os, "replace", refuse)
    with pytest.raises(ResultsWriteError):
        write_results(target, {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"previous": True}


def test_write_failure_removes_its_temporary_file(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(report_module.os, "replace", refuse)
    with pytest.raises(ResultsWriteError):
        write_results(tmp_path / "results.json", {"new": True})
    assert [p.name for p in tmp_path.iterdir()] == []


def test_write_into_an_impossible_directory_raises(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    with pytest.raises(ResultsWriteError):
        write_results(blocker / "nested" / "results.json", {})


def test_write_text_atomic_round_trips(tmp_path):
    target = tmp_path / "shard.jsonl"
    write_text_atomic(target, "line one\nline two")
    assert target.read_text(encoding="utf-8") == "line one\nline two\n"


def test_write_text_atomic_creates_missing_directories(tmp_path):
    target = tmp_path / "a" / "b" / "shard.jsonl"
    write_text_atomic(target, "x")
    assert target.exists()


# --- Reproducibility ----------------------------------------------------------


def test_results_are_identical_apart_from_the_timestamp(tmp_path):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned") == 0
    first = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned") == 0
    second = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


def test_full_dataset_reruns_are_identical(tmp_path):
    """Two runs of everything, including the learned baseline, must agree.

    This is the reproducibility claim: same repository state, same dataset,
    same numbers byte for byte apart from the timestamp. A learned baseline
    whose folds moved between runs would make every gate a coin flip.
    """
    args = ["--dataset", str(GOLDEN), "--results", str(tmp_path / "results.json"), "--quiet"]
    assert main(args) == 0
    first = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert main(args) == 0
    second = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    for document in (first, second):
        document.pop("generated_at")
        document["configuration"].pop("argv")
    assert first == second


def test_results_carry_both_baselines_a_confusion_matrix_and_intervals(tmp_path):
    assert run(tmp_path, "--dataset", str(GOLDEN)) == 0
    document = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))

    assert {"heuristic", "tfidf_logistic_regression"} <= set(document["baselines"])
    assert document["confusion_matrix"]["heuristic"]["orientation"].startswith("rows=expected_label")
    assert set(document["confusion_matrix"]["heuristic"]["counts"]) == {"fast", "balanced", "powerful"}

    intervals = document["confidence_intervals"]["heuristic"]["overall"]
    assert intervals["n"] == document["dataset"]["total_cases"]
    assert intervals["wilson_95"]["lower"] <= intervals["value"] <= intervals["wilson_95"]["upper"]

    assert document["environment"]["python_version"]
    assert document["environment"]["sklearn_version"]
    assert document["dataset"]["fingerprint"]


def test_learned_baseline_reports_its_leakage_inspection(tmp_path):
    assert run(tmp_path, "--dataset", str(GOLDEN)) == 0
    document = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    training = document["baselines"]["tfidf_logistic_regression"]["training"]
    leakage = training["leakage"]
    assert leakage["n_cases"] == document["dataset"]["total_cases"]
    assert leakage["blocking"] is False
    assert training["n_splits"] == 5
    assert training["seed"]
    assert training["train_accuracy"] > 0.9


def test_unequal_numbers_both_systems_are_explained(tmp_path):
    assert run(tmp_path, "--dataset", str(GOLDEN)) == 0
    document = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    holdout = document["comparisons"]["tfidf_logistic_regression_holdout"]
    assert holdout["compared_over_full_dataset"] is False
    assert holdout["paired_cases_compared"] == document["baselines"][
        "tfidf_logistic_regression_holdout"
    ]["overall"]["n"]


# --- Failure dumps ------------------------------------------------------------


def test_dump_failures_writes_misclassifications(tmp_path):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    out = tmp_path / "failures.json"
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned", "--dump-failures", "5", "--failures-out", str(out)) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    rows = payload["by_system"]["heuristic"]
    assert 0 < len(rows) <= 5
    for row in rows:
        assert row["predicted_label"] != row["expected_label"]
        assert {"id", "difficulty", "category", "n_messages"} <= set(row)


def test_failure_dump_is_included_in_results(tmp_path):
    dataset = tiny_dataset(tmp_path / "g.jsonl")
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned", "--dump-failures", "3") == 0
    document = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert "failures" in document
    assert document["failures"]["requested"] == 3


def test_writing_the_full_dataset_failure_dump_gives_readable_cases(tmp_path):
    out = tmp_path / "failures.json"
    assert run(tmp_path, "--dataset", str(GOLDEN), "--dump-failures", "20", "--failures-out", str(out)) == 0
    rows = json.loads(out.read_text(encoding="utf-8"))["by_system"]["heuristic"]
    assert len(rows) == 20
    assert all(row["notes"] or row["adversarial"] or row["category"] for row in rows)


# --- Security -----------------------------------------------------------------


def test_dataset_content_is_never_executed(tmp_path, monkeypatch):
    marker = tmp_path / "pwned"
    hostile = [
        "__import__('os').system(f'touch {marker.as_posix()}')",
        "eval(compile('1+1', '<s>', 'eval'))",
        "$(touch " + marker.as_posix() + ")",
        "`touch " + marker.as_posix() + "`",
        "'; DROP TABLE usage_records; --",
        "__import__('subprocess').run(['echo', 'pwned'])",
    ]
    cases = [
        {
            "id": f"hostile_{i}",
            "input": text,
            "expected_label": "fast",
            "difficulty": "clear",
            "category": "injection",
        }
        for i, text in enumerate(hostile)
    ]
    dataset = write_dataset(tmp_path / "hostile.jsonl", cases)
    assert run(tmp_path, "--dataset", str(dataset), "--no-learned") == 0
    assert not marker.exists(), "dataset content was executed"


def test_shell_metacharacters_in_a_case_do_not_reach_a_shell(tmp_path):
    # The only subprocess call in this package is git rev-parse, with an
    # argument vector and shell=False. Nothing from the dataset is ever an
    # argument to it.
    document = build_report.__doc__
    assert document is not None
    source = (REPO_ROOT / "evals" / "report.py").read_text(encoding="utf-8")
    assert "shell=False" in source
    assert "os.system" not in source
    assert "eval(" not in source
    assert "exec(" not in source


def test_results_do_not_contain_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_supersecretvalue123")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:hunter2@db.example/prod")
    assert run(tmp_path, "--dataset", str(tiny_dataset(tmp_path / "g.jsonl")), "--no-learned") == 0
    text = (tmp_path / "results.json").read_text(encoding="utf-8")
    assert "gsk_supersecretvalue123" not in text
    assert "hunter2" not in text
    assert "GROQ_API_KEY" not in text
    assert "DATABASE_URL" not in text


def test_environment_metadata_is_scoped_to_versions():
    metadata = environment_metadata(REPO_ROOT)
    assert set(metadata) <= {
        "python_version",
        "python_implementation",
        "platform",
        "executable_name",
        "git_commit",
        "sklearn_version",
    }
    assert not any("key" in k.lower() or "token" in k.lower() for k in metadata)


def test_git_commit_is_none_rather_than_fatal_outside_a_repo(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("git is not installed")

    monkeypatch.setattr(report_module.subprocess, "run", refuse)
    assert report_module.git_commit(tmp_path) is None


def test_no_network_calls_in_the_eval_package():
    """The eval harness must run offline; CI has no provider keys."""
    for name in ("dataset.py", "metrics.py", "baselines.py", "learned.py", "gates.py", "report.py", "run.py", "production.py"):
        source = (REPO_ROOT / "evals" / name).read_text(encoding="utf-8")
        for forbidden in ("import requests", "import httpx", "urlopen", "aiohttp"):
            assert forbidden not in source, f"{name} references {forbidden}"


def test_metadata_and_expected_labels_stay_separate_from_the_model_input(tmp_path):
    # The learned baseline must see case.input and nothing else. A case whose
    # difficulty or category would give the answer away must not leak.
    from evals.dataset import EvalCase
    from evals.learned import cross_validated_predictions

    cases = [
        EvalCase(f"c{i}", f"explain topic {i}", "balanced", "clear", "synthetic", "explanation")
        for i in range(6)
    ] + [
        EvalCase(f"f{i}", f"hi there {i}", "fast", "ambiguous", "synthetic", "greeting")
        for i in range(6)
    ]
    result = cross_validated_predictions(
        [c.id for c in cases], [c.input for c in cases], [c.expected_label for c in cases]
    )
    assert len(result.predictions) == len(cases)
    assert result.train_accuracy is not None