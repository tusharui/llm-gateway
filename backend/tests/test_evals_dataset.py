"""Dataset validation tests.

These are the tests that matter most: an eval harness that accepts a corrupt
dataset will report confident numbers about the wrong thing.
"""

from __future__ import annotations

import json

import pytest

from evals.dataset import (
    MAX_INPUT_CHARS,
    EvalCase,
    dataset_fingerprint,
    dataset_summary,
    load_dataset,
    parse_dataset_text,
    validate_cases,
)
from evals.errors import DatasetError, DatasetValidationError


def make_case(**overrides) -> dict:
    case = {
        "id": "clear_fast_01",
        "input": "Hi",
        "expected_label": "fast",
        "difficulty": "clear",
        "source": "synthetic",
        "category": "greeting",
    }
    case.update(overrides)
    return case


def codes(exc: DatasetValidationError) -> set[str]:
    return {issue.code for issue in exc.issues}


# --- happy path ---------------------------------------------------------------


def test_valid_case_loads():
    cases, issues = validate_cases([make_case()])
    assert len(cases) == 1
    assert cases[0].expected_label == "fast"
    assert cases[0].chat_messages() == [{"role": "user", "content": "Hi"}]
    assert issues == []


def test_legacy_v1_case_loads_with_aliases():
    cases, _ = validate_cases([{"id": "greeting", "prompt": "Hi", "expected_tier": "fast"}])
    assert cases[0].input == "Hi"
    assert cases[0].expected_label == "fast"
    # difficulty is required; a v1 file must be migrated, not guessed at.
    assert cases[0].difficulty == "ambiguous"


def test_v1_case_without_difficulty_is_inferred_as_ambiguous():
    # Not an error: legacy records are migrated with the conservative value so
    # an unmigrated file cannot inflate the clear-case gate.
    cases, issues = validate_cases([{"id": "greeting", "prompt": "Hi", "expected_tier": "fast"}])
    assert cases[0].difficulty == "ambiguous"
    assert issues == []


def test_messages_default_to_single_user_turn():
    cases, _ = validate_cases([make_case()])
    assert cases[0].messages is None


def test_messages_are_preserved_and_must_contain_input():
    cases, _ = validate_cases(
        [
            make_case(
                messages=[
                    {"role": "system", "content": "You are terse."},
                    {"role": "user", "content": "Hi"},
                ]
            )
        ]
    )
    assert cases[0].chat_messages() == [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "Hi"},
    ]


# --- required fields ----------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    ["id", "input", "expected_label", "difficulty"],
)
def test_missing_required_field_is_an_error(missing):
    case = make_case()
    del case[missing]
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([case])
    assert f"{missing}.missing" in codes(exc.value)


def test_both_aliases_at_once_is_an_error():
    case = make_case()
    case["prompt"] = case["input"]
    case["expected_tier"] = case["expected_label"]
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([case])
    assert "input.ambiguous" in codes(exc.value)
    assert "expected_label.ambiguous" in codes(exc.value)


# --- invalid values -----------------------------------------------------------


def test_invalid_difficulty_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(difficulty="medium")])
    assert "difficulty.invalid" in codes(exc.value)


def test_invalid_label_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(expected_label="ultra")])
    assert "expected_label.invalid" in codes(exc.value)


@pytest.mark.parametrize("difficulty", ["clear", "ambiguous"])
def test_valid_difficulties_accepted(difficulty):
    cases, _ = validate_cases([make_case(difficulty=difficulty)])
    assert cases[0].difficulty == difficulty


def test_invalid_source_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(source="reddit")])
    assert "source.invalid" in codes(exc.value)


def test_invalid_id_format_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(id="Has Spaces")])
    assert "id.format" in codes(exc.value)


def test_invalid_message_role_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(messages=[{"role": "tool", "content": "x"}])])
    assert "messages.role" in codes(exc.value)


def test_messages_input_mismatch_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases(
            [make_case(messages=[{"role": "user", "content": "something else"}])]
        )
    assert "messages.input_mismatch" in codes(exc.value)


# --- duplicate detection ------------------------------------------------------


def test_duplicate_id_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(), make_case()])
    assert "id.duplicate" in codes(exc.value)


def test_duplicate_input_is_an_error_even_with_different_ids():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(id="a1"), make_case(id="a2")])
    assert "input.duplicate" in codes(exc.value)


def test_duplicate_detection_ignores_casing_and_whitespace():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(id="a1", input="Hi there"), make_case(id="a2", input="  hi   THERE ")])
    assert "input.duplicate" in codes(exc.value)


def test_near_duplicate_is_a_warning_not_an_error():
    cases, issues = validate_cases(
        [
            make_case(id="n1", input="Explain how a binary search tree works"),
            make_case(id="n2", input="Explain how a binary search tree works well"),
        ]
    )
    assert len(cases) == 2
    assert {i.code for i in issues} == {"input.near_duplicate"}
    assert all(i.severity == "warning" for i in issues)


# --- empty / oversized / malformed text ---------------------------------------


def test_empty_input_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(input="")])
    assert "input.empty" in codes(exc.value)


def test_whitespace_only_input_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(input="   \t\n  ")])
    assert "input.blank" in codes(exc.value)


def test_overlong_input_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(input="x" * (MAX_INPUT_CHARS + 1))])
    assert "input.too_long" in codes(exc.value)


def test_input_with_control_characters_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(input="Hello\x00world")])
    assert "input.control_chars" in codes(exc.value)


def test_input_with_unpaired_surrogate_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(input="Hello\ud800world")])
    assert "input.control_chars" in codes(exc.value) or "input.surrogate" in codes(exc.value)


def test_unicode_lookalike_input_is_accepted_not_rejected():
    # Cyrillic 'а' in "caf\u0435" must not be auto-rejected: the dataset is
    # supposed to contain lookalike cases, not be filtered of them.
    cases, _ = validate_cases([make_case(input="caf\u0435 review")])
    assert cases[0].input == "caf\u0435 review"


# --- wrong types --------------------------------------------------------------


def test_null_case_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([None])
    assert "case.type" in codes(exc.value)


def test_string_case_is_an_error_and_is_not_executed():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases(["__import__('os').system('echo pwned')"])
    assert "case.type" in codes(exc.value)


def test_numeric_input_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(input=42)])
    assert "input.type" in codes(exc.value)


def test_numeric_label_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(expected_label=1)])
    assert "expected_label.type" in codes(exc.value)


def test_nan_label_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(expected_label=float("nan"))])
    assert "expected_label.type" in codes(exc.value)


def test_case_limit_is_enforced():
    many = [make_case(id=f"c{i:04d}", input=f"unique prompt number {i}") for i in range(5)]
    with pytest.raises(DatasetError) as exc:
        validate_cases(many, limit=3)
    assert "limit is 3" in str(exc.value)


# --- label leakage ------------------------------------------------------------


def test_notes_restating_the_label_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(notes="This one should be fast tier")])
    assert "metadata.label_leakage" in codes(exc.value)


def test_notes_mentioning_a_substring_are_fine():
    cases, issues = validate_cases([make_case(notes="Mentions fastapi and powerfulserver")])
    assert issues == []
    assert cases[0].notes


def test_unknown_answer_like_key_is_an_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(gold_label="fast")])
    assert "metadata.leaky_key" in codes(exc.value)


# --- aggregation --------------------------------------------------------------


def test_all_issues_are_reported_not_just_the_first():
    with pytest.raises(DatasetValidationError) as exc:
        validate_cases([make_case(input=""), make_case(id="Bad Id", difficulty="nope")])
    assert len(exc.value.issues) >= 3


def test_dataset_file_not_found():
    with pytest.raises(DatasetError) as exc:
        load_dataset("does-not-exist.json")
    assert "not found" in str(exc.value)


def test_dataset_directory_is_an_error(tmp_path):
    with pytest.raises(DatasetError) as exc:
        load_dataset(tmp_path)
    assert "directory" in str(exc.value)


def test_empty_dataset_file():
    with pytest.raises(DatasetError) as exc:
        parse_dataset_text("   \n  ", path="empty.json")
    assert "empty" in str(exc.value)


def test_corrupted_json_reports_line_and_column():
    with pytest.raises(DatasetError) as exc:
        parse_dataset_text('{"cases": [ {"id": "x", }]}', path="corrupt.json")
    assert "line" in str(exc.value)


def test_jsonl_with_a_broken_line_reports_the_line_number():
    text = '{"id":"a"}\n{"id":"b",,}\n'
    with pytest.raises(DatasetError) as exc:
        parse_dataset_text(text, path="broken.jsonl")
    assert "line 2" in str(exc.value)


def test_jsonl_round_trip():
    text = "\n".join(json.dumps(make_case(id=f"n{i}", input=f"prompt number {i}")) for i in range(3))
    cases, _ = validate_cases(parse_dataset_text(text, path="x.jsonl"))
    assert [c.id for c in cases] == ["n0", "n1", "n2"]


def test_object_without_cases_key_is_an_error():
    with pytest.raises(DatasetError) as exc:
        parse_dataset_text('{"version": 2}', path="x.json")
    assert "'cases' key" in str(exc.value)


def test_zero_usable_cases_is_an_error(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text('{"version": 2, "cases": []}', encoding="utf-8")
    with pytest.raises(DatasetError) as exc:
        load_dataset(path)
    assert "zero usable cases" in str(exc.value)


# --- determinism --------------------------------------------------------------


def test_fingerprint_is_order_independent():
    a = [EvalCase("a", "one", "fast", "clear", "synthetic", "x")]
    b = [EvalCase("b", "two", "balanced", "ambiguous", "synthetic", "y")]
    assert dataset_fingerprint(a + b) == dataset_fingerprint(b + a)


def test_fingerprint_changes_when_a_label_changes():
    a = [EvalCase("a", "one", "fast", "clear", "synthetic", "x")]
    b = [EvalCase("a", "one", "balanced", "clear", "synthetic", "x")]
    assert dataset_fingerprint(a) != dataset_fingerprint(b)


def test_summary_is_deterministic_and_counts_distribution():
    cases = [
        EvalCase("a", "one", "fast", "clear", "synthetic", "greeting"),
        EvalCase("b", "two", "balanced", "ambiguous", "synthetic", "compare"),
        EvalCase("c", "three", "fast", "ambiguous", "production", "greeting"),
    ]
    first = dataset_summary(cases)
    assert first == dataset_summary(list(reversed(cases)))
    assert first["by_expected_label"] == {"fast": 2, "balanced": 1}
    assert first["by_difficulty"] == {"clear": 1, "ambiguous": 2}
    assert first["unique_inputs"] == 3


def test_repeated_loads_are_equal(tmp_path):
    path = tmp_path / "g.json"
    path.write_text(
        json.dumps({"version": 2, "cases": [make_case(id="a"), make_case(id="b", input="other")]}),
        encoding="utf-8",
    )
    first, _ = load_dataset(path)
    second, _ = load_dataset(path)
    assert first == second