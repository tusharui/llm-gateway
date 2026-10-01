"""Regression tests for defects found in review.

Each test names the bug it prevents from returning. They are grouped here
rather than spread across the feature test files so that the reason a line
exists stays legible next to the assertion.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from evals.dataset import EvalCase, load_dataset, validate_cases
from evals.errors import DatasetError, DatasetValidationError, GateConfigurationError, MetricsError
from evals.gates import suggested_threshold, standard_error
from evals.learned import cross_validated_predictions
from evals.metrics import compare_paired, per_class_report
from evals.production import (
    find_residual_risks,
    redact_for_dataset,
    stage_production_records,
    write_production_shard,
)
from evals.run import main

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = REPO_ROOT / "evals" / "golden"


# --- run.py: --report crashed and cost the run its results.json ---------------


def test_report_flag_works_with_a_system_that_covers_fewer_cases(tmp_path):
    """--report used positional indexing into every system's predictions.

    The holdout predicts 95 of 379 cases, so the table raised IndexError part
    way down. Worse, the table was printed *before* results.json was written,
    so the crash also lost the run's results. Regression: both fixes.
    """
    results = tmp_path / "results.json"
    code = main(["--dataset", str(GOLDEN), "--results", str(results), "--report", "--quiet"])
    # --quiet suppresses the table but must still write results.
    assert code == 0
    assert results.exists()

    code = main(["--dataset", str(GOLDEN), "--results", str(results), "--report"])
    assert code == 0
    document = json.loads(results.read_text(encoding="utf-8"))
    assert "tfidf_logistic_regression_holdout" in document["baselines"]


def test_report_table_marks_cases_a_system_did_not_evaluate(tmp_path, capsys):
    from evals.baselines import evaluate_baseline
    from evals.run import _print_table

    cases = [EvalCase("a", "hi", "fast", "clear", "synthetic", "c")]
    full = evaluate_baseline("full", "test", cases, ["fast"])
    partial = evaluate_baseline("partial", "test", [], [])
    _print_table({"full": full, "partial": partial}, cases, {"full": full.predictions, "partial": partial.predictions})
    output = capsys.readouterr().out
    assert "not evaluated by at least one system" in output
    assert "1 case(s) where the scored systems disagree" not in output


# --- metrics.py: McNemar overflowed past ~1100 discordant pairs --------------


@pytest.mark.parametrize("discordant", [1100, 5000, 15000])
def test_mcnemar_does_not_overflow_on_large_datasets(discordant):
    a = [True] * discordant + [False]
    b = [False] * discordant + [True]
    result = compare_paired(a, b)
    assert result.p_value is not None
    assert 0.0 <= result.p_value <= 1.0
    assert result.significant is True


def test_mcnemar_switches_method_past_the_exact_limit():
    from evals.metrics import EXACT_MCNEMAR_MAX_DISCORDANT

    small = compare_paired([True] * 20 + [False] * 5, [False] * 20 + [True] * 5)
    assert small.method == "exact binomial (McNemar)"
    n = EXACT_MCNEMAR_MAX_DISCORDANT + 2
    large = compare_paired([True] * n + [False], [False] * n + [True])
    assert "chi-square" in large.method


def test_mcnemar_approximation_agrees_with_exact_near_the_boundary():
    from evals.metrics import EXACT_MCNEMAR_MAX_DISCORDANT, _approx_mcnemar, _exact_mcnemar

    for a_only, b_only in [(400, 100), (300, 200), (250, 250), (450, 50)]:
        n = a_only + b_only
        exact = _exact_mcnemar(a_only, b_only)
        approx = _approx_mcnemar(a_only, b_only)
        # The continuity correction makes the approximation slightly\n        # anti-conservative at a true zero difference (1.0 exact vs 0.96), which\n        # is the documented behaviour of the correction, not a sign error.\n        assert abs(exact - approx) < 0.06, (a_only, b_only, exact, approx)


def test_large_p_value_is_not_printed_as_zero():
    from evals.metrics import _exact_mcnemar

    # A tiny p-value underflows to exactly 0.0, which reads as "impossible".
    value = _exact_mcnemar(600, 0) if 600 <= 500 else None
    assert value is None or value >= 0.0


# --- metrics.py: macro-F1 excluded undefined classes -------------------------


def test_macro_f1_counts_an_undefined_class_as_zero():
    report = per_class_report([("a", "a"), ("a", "a")], labels=["a", "b", "c"])
    # b and c are never predicted, so their F1 is undefined. Standard macro-F1
    # averages over all three with 0.0 for the undefined ones: 1/3.
    assert report["per_class"]["b"]["f1"] is None
    assert report["macro_f1"] == pytest.approx(1 / 3, abs=1e-6)
    assert "counted as 0.0" in report["macro_f1_convention"]


def test_majority_floor_macro_f1_is_not_inflated():
    report = per_class_report([("fast", "balanced")] * 10, labels=["fast", "balanced", "powerful"])
    assert report["per_class"]["powerful"]["f1"] is None
    assert report["macro_f1"] < 0.2


# --- gates.py: --load-baseline skipped threshold validation ------------------


@pytest.mark.parametrize("threshold", [90, -1, 1.5, True])
def test_baseline_file_thresholds_are_validated(tmp_path, threshold):
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(
            {
                "thresholds": [
                    {"name": "min-accuracy", "selector": "accuracy", "threshold": threshold}
                ]
            }
        ),
        encoding="utf-8",
    )
    from evals.run import _specs_from_baseline_file

    with pytest.raises(GateConfigurationError):
        _specs_from_baseline_file(str(path))


def test_baseline_file_with_a_non_numeric_threshold_is_a_config_error(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps({"thresholds": [{"name": "g", "selector": "accuracy", "threshold": "high"}]}),
        encoding="utf-8",
    )
    from evals.run import _specs_from_baseline_file

    with pytest.raises(GateConfigurationError) as exc:
        _specs_from_baseline_file(str(path))
    assert "not a number" in str(exc.value)


def test_gate_system_on_the_command_line_overrides_the_file(tmp_path):
    """The file's per-system value used to win, silently redirecting the gate."""
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(
            {
                "thresholds": [
                    {
                        "name": "min-accuracy",
                        "selector": "accuracy",
                        "threshold": 0.5,
                        "system": "majority_class",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    from evals.run import _specs_from_baseline_file

    specs = _specs_from_baseline_file(str(path), system="heuristic")
    assert specs[0].system == "heuristic"
    assert _specs_from_baseline_file(str(path), system=None)[0].system == "majority_class"


def test_reported_gate_systems_match_the_thresholds_actually_run(tmp_path):
    results = tmp_path / "results.json"
    assert (
        main(
            [
                "--dataset",
                str(GOLDEN),
                "--results",
                str(results),
                "--load-baseline",
                str(REPO_ROOT / "evals" / "baseline_gates.json"),
                "--quiet",
            ]
        )
        == 0
    )
    document = json.loads(results.read_text(encoding="utf-8"))
    effective = set(document["gates"]["systems_effective"])
    per_result = {r["system"] for r in document["gates"]["results"]}
    assert effective == per_result
    assert effective == {"heuristic", "tfidf_logistic_regression"}


# --- gates.py: generated thresholds contradicted the stated policy ------------


def test_suggested_threshold_uses_the_documented_policy():
    observed, n = 0.532982, 379
    expected = math.floor((observed - 1.3 * standard_error(observed, n)) * 100) / 100
    assert suggested_threshold(observed, n) == pytest.approx(expected)
    # The old behaviour rounded the measurement down to two places instead,
    # which put gates 0.1 SE below the measurement.
    assert suggested_threshold(observed, n) < round(observed, 2)


def test_suggested_threshold_is_never_above_the_measurement():
    for observed, n in [(0.99, 300), (0.533, 379), (0.247, 89), (0.469, 113), (1.0, 50)]:
        assert suggested_threshold(observed, n) <= observed


# --- gates.py: --write-baseline deleted the human-written sections -----------


def test_regenerating_the_committed_baseline_preserves_its_prose(tmp_path):
    committed = REPO_ROOT / "evals" / "baseline_gates.json"
    before = json.loads(committed.read_text(encoding="utf-8"))
    copy = tmp_path / "baseline_gates.json"
    copy.write_text(json.dumps(before), encoding="utf-8")

    assert main(["--dataset", str(GOLDEN), "--write-baseline", str(copy), "--quiet", "--no-save"]) == 0
    after = json.loads(copy.read_text(encoding="utf-8"))

    for key in ("threshold_policy", "why_the_gate_is_low", "dataset", "deliberately_not_gated"):
        assert key in after, f"regeneration dropped {key}"
    assert after["thresholds"]
    # The regenerated measurements cover every system the run evaluated, which
    # is a superset of the two the committed file records by hand.
    assert set(after["measurements"]) >= set(before["measurements"])


# --- learned.py: near-duplicates straddled folds ------------------------------


def test_near_duplicates_are_kept_in_one_fold():
    """Two near-duplicate pairs straddled a plain stratified split.

    The out-of-fold score on both was optimistic: a model had seen a twin of the
    test case. Grouped folds make that structurally impossible.
    """
    from evals.run import main as run_main

    results = Path(__file__).resolve().parents[1] / "evals" / "results.json"
    run_main(["--dataset", str(GOLDEN), "--results", str(results), "--quiet"])
    document = json.loads(results.read_text(encoding="utf-8"))
    training = document["baselines"]["tfidf_logistic_regression"]["training"]

    assert "group k-fold" in training["method"]
    assert training["leakage"]["cross_fold_near_duplicates"] == []
    assert training["leakage"]["near_duplicate_pair_count"] > 0


def test_grouping_is_applied_end_to_end():
    cases = [EvalCase(f"c{i}", f"explain caching concept number {i}", "balanced", "clear", "synthetic", "c") for i in range(8)]
    cases.append(EvalCase("twin_a", "prove the theorem step by step", "powerful", "clear", "synthetic", "c"))
    cases.append(EvalCase("twin_b", "prove the theorem step by step!", "powerful", "clear", "synthetic", "c"))
    cases.append(EvalCase("third", "hi", "fast", "clear", "synthetic", "c"))
    cases.append(EvalCase("fourth", "thanks", "fast", "clear", "synthetic", "c"))
    result = cross_validated_predictions(
        [c.id for c in cases], [c.input for c in cases], [c.expected_label for c in cases]
    )
    assert result.n_splits >= 2
    assert len(result.predictions) == len(cases)


# --- learned.py: O(n^2) scan could exhaust memory -----------------------------


def test_near_duplicate_scan_is_skipped_and_reported_for_a_large_dataset(monkeypatch):
    import evals.learned as learned

    monkeypatch.setattr(learned, "LEAKAGE_SCAN_MAX_CASES", 4)
    ids = [f"c{i}" for i in range(6)]
    texts = [f"explain caching topic {i} in detail" for i in range(6)]
    labels = ["balanced"] * 6
    report = learned.leakage_report(ids, texts, labels)
    assert report.near_duplicate_scan_skipped is True
    assert report.to_dict()["near_duplicate_scan_skipped"] is True
    assert any("near-duplicate scan skipped" in note for note in report.notes)


def test_near_duplicate_pair_list_is_capped_and_the_cap_is_reported(monkeypatch):
    import evals.learned as learned

    monkeypatch.setattr(learned, "NEAR_DUPLICATE_PAIR_CAP", 5)
    ids = [f"c{i}" for i in range(12)]
    texts = ["explain the caching layer in distributed systems"] * 6 + [
        "explain the caching layer of distributed systems" for _ in range(6)
    ]
    report = learned.leakage_report(ids, texts, ["balanced"] * 12)
    payload = report.to_dict()
    assert payload["near_duplicate_pair_count"] <= 5
    assert payload["near_duplicate_pairs_truncated"] is True
    assert any("truncated" in note for note in report.notes)


def test_issue_list_is_capped_and_the_truncation_is_reported():
    """A systematically malformed dataset produced a 327 MB exception message."""
    cases = [{"id": "same", "input": "hi", "expected_label": "nope", "difficulty": "clear"} for _ in range(500)]
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases(cases)
    message = str(exc.value)
    assert len(message) < 20_000
    assert "more" in message


# --- production.py: the redactor was defeated by one '=' ---------------------


@pytest.mark.parametrize(
    "text,secret",
    [
        ("token=Zx8Kq2Lm9Rt4Yw7Bd1Hc3Nf5Pg6Sj ok", "Zx8Kq2Lm9Rt4Yw7Bd1Hc3Nf5Pg6Sj"),
        ("api_key: aB3xY9zQ1mN7pL5rT8vW2yE4kJ6 ok", "aB3xY9zQ1mN7pL5rT8vW2yE4kJ6"),
        ("password=correcthorsebatterystaplexyz ok", "correcthorsebatterystaplexyz"),
    ],
)
def test_assignment_shaped_secrets_are_caught(text, secret):
    """The token strip set did not include '=', so `key=SECRET` survived both
    redaction and the residual check. That is the most common credential shape
    in a log line."""
    result = redact_for_dataset(text)
    assert secret not in result.text
    assert find_residual_risks(result.text) == []


def test_passphrase_with_no_digits_is_caught():
    assert "correcthorsebatterystaplecorrecthorsebatterystaple" not in redact_for_dataset(
        "value correcthorsebatterystaplecorrecthorsebatterystaple here"
    ).text


def test_camel_case_identifiers_are_not_redacted():
    """Over-redaction is its own failure: it destroys the eval signal."""
    for word in (
        "InternationalizationConfigurationManager",
        "AuthenticationServiceFactory",
        "getUserAuthenticationConfiguration",
    ):
        result = redact_for_dataset(f"explain the role of {word} in this codebase")
        assert word in result.text, word
        assert "high_entropy_token" not in result.applied


def test_email_without_a_tld_is_caught():
    result = redact_for_dataset("connect to postgres as admin@localhost")
    assert "admin@localhost" not in result.text


def test_long_unseparated_phone_number_is_caught():
    result = redact_for_dataset("call 5551234567 about the outage")
    assert "5551234567" not in result.text


def test_credit_card_redaction_does_not_swallow_the_following_space():
    """The old pattern's trailing [ -]? ate the separator: '4111 1111 1111 1111
    expires' became '...expires', silently corrupting the dataset."""
    result = redact_for_dataset("card 4111 1111 1111 1111 expires soon")
    assert "expires" in result.text
    assert result.text.endswith("expires soon")
    assert "1111" not in result.text.replace("[1:redacted]:credit_card", "")


def test_allowlist_is_honoured_by_the_redactor_not_only_the_residual_check():
    uuid = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    plain = redact_for_dataset(f"commit {uuid} is the one")
    allowed = redact_for_dataset(f"commit {uuid} is the one", allowlist=[uuid])
    assert uuid not in plain.text
    assert uuid not in allowed.text
    assert "allowlisted" in allowed.text


def test_redaction_result_is_clean_reflects_the_residual_check():
    result = redact_for_dataset("nothing sensitive here at all")
    assert find_residual_risks(result.text) == []
    assert result.is_clean is True


def test_staging_copes_with_non_mapping_records_when_stratifying():
    """strata_key was read outside the try that guarded _record_text."""
    staged = stage_production_records(
        [{"prompt": "explain the cache", "tier_hint": "fast"}, 42, None],
        n=2,
        seed=1,
        strata_key="tier_hint",
    )
    assert all(isinstance(s.redacted_input, str) for s in staged)


# --- production.py: appending silently overwrote -----------------------------


def test_write_production_shard_appends(tmp_path):
    path = tmp_path / "07_production.jsonl"
    first = [{"id": "p_1", "input": "one", "expected_label": "fast", "difficulty": "clear",
              "source": "production", "category": "c"}]
    second = [{"id": "p_2", "input": "two", "expected_label": "balanced", "difficulty": "clear",
               "source": "production", "category": "c"}]

    assert write_production_shard(str(path), first) == 1
    assert write_production_shard(str(path), second) == 1

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["p_1", "p_2"]


def test_write_production_shard_refuses_to_duplicate_an_id(tmp_path):
    from evals.errors import EvalError

    path = tmp_path / "07_production.jsonl"
    case = {"id": "p_1", "input": "one", "expected_label": "fast", "difficulty": "clear",
            "source": "production", "category": "c"}
    write_production_shard(str(path), [case])
    with pytest.raises(EvalError) as exc:
        write_production_shard(str(path), [case])
    assert "already exist" in str(exc.value)


def test_write_production_shard_refuses_to_extend_a_corrupt_shard(tmp_path):
    from evals.errors import EvalError

    path = tmp_path / "07_production.jsonl"
    path.write_text('{"id": "p_1",,}\n', encoding="utf-8")
    with pytest.raises(EvalError) as exc:
        write_production_shard(str(path), [{"id": "p_2"}])
    assert "valid JSONL" in str(exc.value)


# --- dataset.py: a one-record JSONL shard was rejected -----------------------


def test_single_record_jsonl_shard_loads(tmp_path):
    path = tmp_path / "one.jsonl"
    path.write_text(
        json.dumps({"id": "a", "input": "hi", "expected_label": "fast", "difficulty": "clear"}),
        encoding="utf-8",
    )
    cases, _ = load_dataset(path)
    assert [c.id for c in cases] == ["a"]


def test_multi_line_object_without_a_cases_key_is_an_actionable_error(tmp_path):
    """A pretty-printed object is a document, so a missing 'cases' key is a
    mistake worth naming. A single line is a JSONL record, which is legal."""
    path = tmp_path / "doc.json"
    path.write_text(json.dumps({"id": "a", "input": "hi"}, indent=2), encoding="utf-8")
    with pytest.raises(DatasetError) as exc:
        load_dataset(path)
    assert "cases" in str(exc.value)
    assert "JSONL" in str(exc.value)


# --- dataset.py: non-string notes crashed instead of reporting ---------------


def test_non_string_notes_is_a_validation_error_not_a_crash():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases(
            [{"id": "a", "input": "hi", "expected_label": "fast", "difficulty": "clear", "notes": ["x"]}]
        )
    assert {i.code for i in exc.value.issues} == {"notes.type"}


def test_string_case_error_does_not_echo_the_raw_text():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases(["my email is secret@example.com"])
    assert "secret@example.com" not in str(exc.value)


def test_control_characters_in_a_message_are_rejected():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases(
            [
                {
                    "id": "a",
                    "input": "hi",
                    "expected_label": "fast",
                    "difficulty": "clear",
                    "messages": [{"role": "user", "content": "hello\x00world"}],
                }
            ]
        )
    assert "input.control_chars" in {i.code for i in exc.value.issues}


# --- report.py / run.py: host-specific paths in results.json -----------------


def test_dataset_path_in_results_is_repo_relative(tmp_path):
    results = tmp_path / "results.json"
    assert main(["--dataset", str(GOLDEN), "--results", str(results), "--quiet", "--no-learned"]) == 0
    document = json.loads(results.read_text(encoding="utf-8"))
    assert document["dataset"]["path"] == "evals/golden"
    assert ":" not in document["dataset"]["path"]
    assert document["configuration"]["dataset"] == "evals/golden"


def test_argv_values_are_not_recorded_verbatim(tmp_path):
    results = tmp_path / "results.json"
    secret_path = tmp_path / "a-very-distinctive-name.jsonl"
    assert main(["--dataset", str(secret_path), "--results", str(results), "--quiet"]) == 2
    # The run failed, so nothing was written; check the parser helper directly.
    from evals.run import _scrub_argv

    scrubbed = _scrub_argv(["--dataset", str(secret_path), "--min-accuracy", "0.5"])
    assert scrubbed == ["--dataset", "<value>", "--min-accuracy", "<value>"]
    assert "a-very-distinctive-name" not in " ".join(scrubbed)


# --- report.py: comparisons must use like-for-like populations ----------------


def test_nested_populations_do_not_get_an_unpaired_proportion_test(tmp_path):
    results = tmp_path / "results.json"
    assert main(["--dataset", str(GOLDEN), "--results", str(results), "--quiet"]) == 0
    document = json.loads(results.read_text(encoding="utf-8"))
    holdout = document["comparisons"]["tfidf_logistic_regression_holdout"]
    assert "clear_vs_clear" not in holdout
    assert "nested" in holdout["proportion_comparisons_omitted"]


def test_like_for_like_comparison_excludes_multi_turn(tmp_path):
    results = tmp_path / "results.json"
    assert main(["--dataset", str(GOLDEN), "--results", str(results), "--quiet"]) == 0
    document = json.loads(results.read_text(encoding="utf-8"))
    learned = document["comparisons"]["tfidf_logistic_regression"]
    assert learned["paired"]["input_identical"] is True
    assert learned["paired_all_cases"]["input_identical"] is False
    assert learned["paired_cases_compared"] < document["dataset"]["total_cases"]


# --- gates.py: confidence level was hardcoded in the message -----------------


def test_gate_description_reports_the_configured_confidence_level(tmp_path):
    from evals.baselines import evaluate_baseline
    from evals.gates import build_gates, evaluate_gates

    cases = [EvalCase(f"c{i}", "hi", "fast", "clear", "synthetic", "c") for i in range(10)]
    report = evaluate_baseline("heuristic", "test", cases, ["fast"] * 9 + ["balanced"], confidence=0.8)
    result = evaluate_gates({"heuristic": report}, build_gates(min_accuracy=0.5))[0]
    assert "80% CI" in result.describe()
    assert result.to_dict()["interval_confidence"] == 0.8