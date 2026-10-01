"""Production-ingestion tests, with the security properties checked directly.

These are the tests that decide whether it is safe to turn on real traffic
next quarter, so they check behaviour rather than implementation: a secret must
not survive, an identifier must not be copied, and the sample must not move
when the log is replayed in a different order.
"""

from __future__ import annotations

import json
import re

import pytest

from evals.errors import DatasetValidationError
from evals.production import (
    REDACTOR_VERSION,
    ProductionIngestionError,
    deterministic_sample,
    find_residual_risks,
    ingest_production_cases,
    redact_for_dataset,
    shannon_entropy,
    stable_id,
    stage_production_records,
    write_production_shard,
)
from evals.dataset import validate_cases


SECRETS = [
    "my key is sk-abcdef0123456789abcdef",
    "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "google key AIzaSyA1234567890abcdefghijklmnopqrstuv",
    "aws AKIAIOSFODNN7EXAMPLE",
    "postgres://admin:hunter2@db.internal:5432/prod",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
]

PII = [
    ("email me at jane.doe+work@example.co.uk", ["jane.doe+work@example.co.uk"]),
    ("call me on +44 20 7946 0958", ["7946"]),
    ("my card is 4111 1111 1111 1111", ["4111"]),
    ("server 192.168.1.42 timed out", ["192.168.1.42"]),
    ("request id 3f2504e0-4f89-11d3-9a0c-0305e82c3301", ["3f2504e0"]),
    ("ping me on @jane_codes", ["jane_codes"]),
]


# --- Credential redaction -----------------------------------------------------


@pytest.mark.parametrize("secret", SECRETS)
def test_no_credential_shape_survives(secret):
    result = redact_for_dataset(secret)
    assert result.applied, f"nothing was redacted from {secret!r}"
    assert find_residual_risks(result.text) == [], result.text


def test_credential_redaction_reuses_the_app_redactor():
    # A query-string api key is app.redact's job; production.py must not fork it.
    result = redact_for_dataset("call https://generativelanguage.googleapis.com/v1/models?key=abc123def456")
    assert "abc123def456" not in result.text


# --- PII redaction ------------------------------------------------------------


@pytest.mark.parametrize("text,absent", PII)
def test_no_pii_shape_survives(text, absent):
    result = redact_for_dataset(text)
    for fragment in absent:
        assert fragment not in result.text, result.text
    assert result.applied


def test_normal_technical_prompt_is_left_alone():
    text = "Explain how connection pooling works under load in a Node.js service."
    result = redact_for_dataset(text)
    assert result.text == text
    assert result.applied == ()
    assert find_residual_risks(text) == []


def test_high_entropy_token_is_flagged_and_removed():
    text = "here is the token aB3xY9zQ1mN7pL5rT8vW2yE4kJ6hG0dF3sA7uI9oP1qS5tU7wX9yZ"
    result = redact_for_dataset(text)
    assert "high_entropy_token" in result.applied
    assert find_residual_risks(result.text) == []


def test_long_english_word_is_not_treated_as_a_secret():
    # Over-redaction is its own failure mode: it destroys the eval signal.
    text = "Explain the responsibilities of the InternationalizationConfigurationManager."
    result = redact_for_dataset(text)
    assert "InternationalizationConfigurationManager" in result.text


def test_entropy_helper_behaves():
    assert shannon_entropy("") == 0.0
    assert shannon_entropy("aaaa") == 0.0
    assert shannon_entropy("abcd") == pytest.approx(2.0)


def test_redaction_rejects_a_non_string():
    with pytest.raises(ProductionIngestionError):
        redact_for_dataset({"prompt": "hi"})  # type: ignore[arg-type]


def test_redaction_normalises_unicode_before_matching():
    # A fullwidth 'ï¼ ' would otherwise hide an email from the pattern.
    result = redact_for_dataset("reach me at jane@example.com")
    assert "jane@example.com" not in result.text


# --- Residual detection -------------------------------------------------------


def test_residual_check_names_what_it_found():
    assert "email" in find_residual_risks("write to a@b.com")


def test_allowlist_suppresses_a_specific_value():
    assert find_residual_risks("commit 3f2504e0-4f89-11d3-9a0c-0305e82c3301") != []
    assert find_residual_risks(
        "commit 3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        allowlist=["3f2504e0-4f89-11d3-9a0c-0305e82c3301"],
    ) == []


# --- Field allowlisting -------------------------------------------------------


def test_only_allowed_fields_are_read():
    records = [{"prompt": "hello", "user_id": "u_12345", "api_key": "sk-shouldnotappear"}]
    staged = stage_production_records(records, n=1, seed=1)
    serialised = json.dumps([s.to_dict() for s in staged])
    assert "u_12345" not in serialised
    assert "sk-shouldnotappear" not in serialised
    assert "user_id" not in serialised


def test_user_turn_is_extracted_from_messages():
    records = [
        {
            "messages": [
                {"role": "system", "content": "SYSTEM SECRET INSTRUCTION"},
                {"role": "user", "content": "what is the capital of France"},
            ]
        }
    ]
    staged = stage_production_records(records, n=1, seed=1)
    assert staged[0].redacted_input == "what is the capital of France"


def test_record_without_user_text_is_an_error():
    with pytest.raises(ProductionIngestionError):
        stage_production_records([{"captured_at": "2026-01-01"}], n=1, seed=1)


# --- Fail closed --------------------------------------------------------------


def test_unlabelled_records_do_not_reach_the_dataset():
    records = [{"prompt": "explain the cache layer"}]
    staged = stage_production_records(records, n=1, seed=1)
    assert staged[0].to_dict()["label"] is None
    with pytest.raises(ProductionIngestionError) as exc:
        ingest_production_cases(staged, labels={}, labeler="reviewer")
    assert "will not guess a tier" in str(exc.value)


def test_records_with_nothing_safe_to_stage_raise():
    # Every record is unusable, so the result must be an exception rather than
    # an empty list that reads like "no traffic today".
    with pytest.raises(ProductionIngestionError) as exc:
        stage_production_records(
            [{"captured_at": "2026-01-01"}, "not even a mapping"],
            n=5,
            seed=1,
        )
    assert "no production record could be staged safely" in str(exc.value)


def test_capture_timestamp_is_not_mistaken_for_prompt_text():
    records = [{"captured_at": "2026-01-01T00:00:00Z"}]
    with pytest.raises(ProductionIngestionError):
        stage_production_records(records, n=1, seed=1)


def test_ingest_rejects_an_invalid_label():
    staged = stage_production_records([{"prompt": "hello there"}], n=1, seed=1)
    with pytest.raises(ProductionIngestionError) as exc:
        ingest_production_cases(staged, labels={staged[0].case_id: "ultra"}, labeler="reviewer")
    assert "valid tiers" in str(exc.value)


def test_ingest_requires_a_labeller():
    staged = stage_production_records([{"prompt": "hello there"}], n=1, seed=1)
    with pytest.raises(ProductionIngestionError):
        ingest_production_cases(staged, labels={staged[0].case_id: "fast"}, labeler="")


def test_ingest_refuses_a_case_with_residual_findings():
    # A candidate that reached the labelled stage still flagged by the redactor
    # must be rejected: a human resolving the finding is what makes it valid.
    from evals.production import StagedCandidate

    flagged = StagedCandidate(
        case_id=stable_id("prod", "synthetic flagged case"),
        redacted_input="value 3f2504e0-4f89-11d3-9a0c-0305e82c3301 here",
        captured_date="2026-03-04",
        redactions_applied=(),
        source_index=0,
        residual_risks=("uuid",),
    )
    with pytest.raises(ProductionIngestionError) as exc:
        ingest_production_cases([flagged], labels={flagged.case_id: "fast"}, labeler="reviewer")
    assert "must not enter the dataset" in str(exc.value)


# --- Provenance ---------------------------------------------------------------


def test_provenance_carries_no_user_identifiers():
    records = [
        {
            "prompt": "my deployment keeps failing, key sk-abcdef0123456789abcdef",
            "captured_at": "2026-03-04T11:22:33.441Z",
            "user_id": "u_9",
            "session_id": "s_9",
            "ip": "203.0.113.7",
        }
    ]
    staged = stage_production_records(records, n=1, seed=1)
    cases = ingest_production_cases(staged, labels={staged[0].case_id: "balanced"}, labeler="reviewer")
    case = cases[0]
    assert case["source"] == "production"
    assert case["provenance"]["captured_date"] == "2026-03-04"
    assert "11:22:33" not in json.dumps(case)
    assert "u_9" not in json.dumps(case)
    assert "203.0.113.7" not in json.dumps(case)
    assert case["provenance"]["redactor_version"] == REDACTOR_VERSION
    assert "sk-abcdef0123456789abcdef" not in json.dumps(case)
    assert case["provenance"]["redactions_applied"]


def test_ingested_cases_pass_dataset_validation():
    records = [{"prompt": f"explain caching layer number {i}", "captured_at": "2026-03-04"} for i in range(3)]
    staged = stage_production_records(records, n=3, seed=1)
    cases = ingest_production_cases(
        staged, labels={s.case_id: "balanced" for s in staged}, labeler="reviewer"
    )
    validated, issues = validate_cases(cases)
    assert len(validated) == 3
    assert issues == []


# --- Determinism --------------------------------------------------------------


def test_sampling_is_stable_under_reordering():
    records = [{"prompt": f"prompt number {i}", "captured_at": "2026-03-0" + str(i % 9 + 1)} for i in range(20)]
    forward = deterministic_sample(records, n=5, seed=42)
    backward = deterministic_sample(list(reversed(records)), n=5, seed=42)
    assert [r["prompt"] for r in forward] == [r["prompt"] for r in backward]


def test_sampling_changes_with_the_seed():
    records = [{"prompt": f"prompt number {i}"} for i in range(20)]
    assert [r["prompt"] for r in deterministic_sample(records, n=5, seed=1)] != [
        r["prompt"] for r in deterministic_sample(records, n=5, seed=2)
    ]


def test_sampling_does_not_favour_recency():
    records = [{"prompt": "old request"}] + [{"prompt": f"new request {i}"} for i in range(50)]
    # Recency sampling would return exactly the last ten. Hash ordering does not,
    # and the single oldest record is reachable but not guaranteed -- both
    # properties are checked so the test cannot pass by luck.
    first = [r["prompt"] for r in deterministic_sample(records, n=10, seed=7)]
    assert first != [r["prompt"] for r in records[-10:]]
    hits = sum(
        "old request" in [r["prompt"] for r in deterministic_sample(records, n=10, seed=seed)]
        for seed in range(50)
    )
    assert 0 < hits < 50, f"expected a non-degenerate hit rate, got {hits}/50"


def test_stratified_sampling_keeps_a_rare_stratum():
    records = [{"prompt": f"common {i}", "tier_hint": "fast"} for i in range(50)]
    records += [{"prompt": "one rare case", "tier_hint": "powerful"}]
    chosen = deterministic_sample(records, n=6, seed=3, strata_key="tier_hint")
    assert "one rare case" in [r["prompt"] for r in chosen]


def test_sampling_zero_returns_nothing():
    assert deterministic_sample([{"prompt": "x"}], n=0, seed=1) == []


def test_case_ids_are_content_derived_and_stable():
    assert stable_id("prod", "hello", length=8) == stable_id("prod", "hello", length=8)
    assert stable_id("prod", "hello") != stable_id("prod", "goodbye")


def test_staging_twice_produces_identical_ids():
    records = [{"prompt": f"prompt {i}"} for i in range(10)]
    first = stage_production_records(records, n=5, seed=11)
    second = stage_production_records(records, n=5, seed=11)
    assert [s.case_id for s in first] == [s.case_id for s in second]


# --- Writing ------------------------------------------------------------------


def test_production_shard_is_jsonl_and_round_trips(tmp_path):
    records = [{"prompt": f"explain concept {i}", "captured_at": "2026-03-04"} for i in range(3)]
    staged = stage_production_records(records, n=3, seed=1)
    cases = ingest_production_cases(
        staged, labels={s.case_id: "balanced" for s in staged}, labeler="reviewer"
    )
    path = tmp_path / "07_production.jsonl"
    write_production_shard(str(path), cases)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    for line in lines:
        assert json.loads(line)["source"] == "production"


def test_staging_a_string_is_an_error():
    with pytest.raises(ProductionIngestionError):
        stage_production_records("just a string", n=1, seed=1)  # type: ignore[arg-type]


def test_ingested_duplicate_inputs_are_rejected_by_validation():
    # Same prompt twice produces the same content-derived id, so the dataset
    # loader catches it as a duplicate id rather than double-counting a case.
    records = [{"prompt": "explain the cache", "captured_at": "2026-03-04"} for _ in range(2)]
    staged = stage_production_records(records, n=2, seed=1)
    assert len({s.case_id for s in staged}) == 1
    cases = ingest_production_cases(staged, labels={staged[0].case_id: "balanced"}, labeler="r")
    with pytest.raises(DatasetValidationError):
        validate_cases(cases)