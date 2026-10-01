"""Golden dataset schema, loading and validation.

Design goals, in priority order:

1. **Fail loudly.** A malformed case is an error, never a silently dropped row.
   Dropping rows quietly is how an eval harness starts lying: accuracy is
   reported over whatever subset happened to parse.
2. **Deterministic.** Loading the same file twice produces equal
   :class:`EvalCase` objects in the same order, with stable ids and a
   content fingerprint.
3. **Backward compatible.** The v1 golden file (a bare JSON list of
   ``{id, prompt, expected_tier}``) still loads. The 18 original cases keep
   their ids so historical results stay comparable.

Schema (v2 file)::

    {
      "version": 2,
      "cases": [
        {
          "id": "clear_fast_greeting_01",     # stable, unique, [a-z0-9][a-z0-9_.-]+
          "input": "Hi",                      # last user message
          "expected_label": "fast",           # one of TIERS
          "difficulty": "clear",              # clear | ambiguous
          "source": "synthetic",              # synthetic|production|adversarial|manually_created
          "category": "greeting",             # free-form, for coverage reporting
          "adversarial": ["casing"],          # optional list of adversarial tags
          "notes": "free text, never the label",
          "messages": [                       # optional; defaults to one user message
            {"role": "user", "content": "Hi"}
          ]
        }
      ]
    }

``messages`` exists because :func:`app.engine.auto_router.classify_complexity`
scores message count and the presence of a system message. A dataset of only
single-message cases leaves that code path completely unevaluated.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from evals.errors import (
    DatasetError,
    DatasetValidationError,
    IssueCollector,
    ValidationIssue,
)

# --- Contract constants -------------------------------------------------------
# Kept as module constants rather than imported from the router so that a change
# to the router's tiers is a deliberate, reviewed edit here too: the golden set
# must not silently accept a label the gateway can no longer produce.

TIERS: tuple[str, ...] = ("fast", "balanced", "powerful")
DIFFICULTIES: tuple[str, ...] = ("clear", "ambiguous")
SOURCES: tuple[str, ...] = ("synthetic", "production", "adversarial", "manually_created")
ROLES: tuple[str, ...] = ("system", "user", "assistant")

SCHEMA_VERSION = 2

MAX_INPUT_CHARS = 4000
MAX_ID_LEN = 64
MAX_CASES = 20000

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_LEAK_KEY_RE = re.compile(r"(label|expected|answer|target|gold|tier)", re.IGNORECASE)

_ALLOWED_KEYS = frozenset(
    {
        "id",
        "input",
        "prompt",
        "expected_label",
        "expected_tier",
        "difficulty",
        "source",
        "category",
        "adversarial",
        "notes",
        "messages",
        "provenance",
    }
)


# --- Case model ---------------------------------------------------------------


@dataclass(frozen=True)
class EvalCase:
    """One validated golden case."""

    id: str
    input: str
    expected_label: str
    difficulty: str
    source: str
    category: str
    adversarial: tuple[str, ...] = ()
    notes: str = ""
    messages: tuple[tuple[str, str], ...] | None = None
    provenance: dict[str, Any] | None = None

    def chat_messages(self) -> list[dict[str, str]]:
        """Messages in the shape ``classify_complexity`` consumes.

        Defaults to a single user message so the common case needs no special
        handling anywhere downstream.
        """
        if self.messages:
            return [{"role": role, "content": content} for role, content in self.messages]
        return [{"role": "user", "content": self.input}]

    def fingerprint_payload(self) -> dict[str, Any]:
        """Canonical, order-stable payload used for the dataset hash.

        Excludes anything host-specific so the fingerprint is reproducible on
        CI, on a laptop, and in a container.
        """
        return {
            "id": self.id,
            "input": self.input,
            "expected_label": self.expected_label,
            "difficulty": self.difficulty,
            "source": self.source,
            "category": self.category,
            "adversarial": list(self.adversarial),
            "messages": [list(m) for m in self.messages] if self.messages else None,
        }


# --- Normalisation helpers ----------------------------------------------------


def normalize_for_duplicates(text: str) -> str:
    """Aggressively normalise text so duplicate detection is not defeated by
    casing, punctuation, or whitespace."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _WS_RE.sub(" ", folded).strip()


def _word_tokens(text: str) -> frozenset[str]:
    return frozenset(_WORD_RE.findall(unicodedata.normalize("NFKC", text).casefold()))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# --- Parsing ------------------------------------------------------------------


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DatasetError(f"Dataset not found: {path}") from exc
    except IsADirectoryError as exc:
        raise DatasetError(f"Dataset path is a directory, not a file: {path}") from exc
    except PermissionError as exc:
        raise DatasetError(f"Dataset file is not readable (permissions): {path}") from exc
    except UnicodeDecodeError as exc:
        raise DatasetError(
            f"Dataset file is not valid UTF-8: {path} ({exc.reason} at byte {exc.start})"
        ) from exc
    except OSError as exc:
        raise DatasetError(f"Could not read dataset {path}: {exc.strerror or exc}") from exc


def parse_dataset_text(text: str, *, path: str = "<string>") -> list[Any]:
    """Parse a JSON array, a ``{version, cases}`` object, or JSONL.

    Raises :class:`DatasetError` with the line number for malformed JSONL.
    """
    stripped = text.lstrip()
    if not stripped:
        raise DatasetError(f"Dataset {path} is empty")

    if stripped[0] == "{":
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) > 1:
            # Multi-line input beginning with '{' is far more likely to be
            # JSONL than a truncated document. Try the document form first so
            # a pretty-printed v2 file still works, then fall back to JSONL and
            # report JSONL's line number, which is the actionable message.
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return _parse_jsonl(text, path)
        else:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise DatasetError(
                    f"Dataset {path} is not valid JSON: {exc.msg} "
                    f"(line {exc.lineno}, column {exc.colno})"
                ) from exc
        if not isinstance(payload, dict):
            raise DatasetError(f"Dataset {path}: expected a JSON object, got {type(payload).__name__}")
        if "cases" not in payload:
            raise DatasetError(f"Dataset {path}: object form requires a 'cases' key")
        version = payload.get("version", 1)
        if not isinstance(version, int) or isinstance(version, bool):
            raise DatasetError(f"Dataset {path}: 'version' must be an integer, got {version!r}")
        cases = payload["cases"]
        if not isinstance(cases, list):
            raise DatasetError(f"Dataset {path}: 'cases' must be a list, got {type(cases).__name__}")
        return cases

    if stripped[0] == "[":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DatasetError(
                f"Dataset {path} is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
            ) from exc
        if not isinstance(payload, list):
            raise DatasetError(f"Dataset {path}: expected a JSON array of cases")
        return payload

    # JSONL fallback: one JSON object per non-empty line.
    return _parse_jsonl(text, path)


def _parse_jsonl(text: str, path: str) -> list[Any]:
    cases = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            cases.append(json.loads(candidate))
        except json.JSONDecodeError as exc:
            raise DatasetError(
                f"Dataset {path}: line {lineno} is not valid JSON: {exc.msg} (column {exc.colno})"
            ) from exc
    if not cases:
        raise DatasetError(f"Dataset {path} contains no JSONL records")
    return cases


# --- Validation ---------------------------------------------------------------


def _validate_messages(raw: Any, location: str, collector: IssueCollector) -> tuple[tuple[str, str], ...] | None:
    if not isinstance(raw, list) or not raw:
        collector.error("messages.type", location, f"'messages' must be a non-empty list, got {type(raw).__name__}")
        return None
    out: list[tuple[str, str]] = []
    for i, message in enumerate(raw):
        loc = f"{location}[{i}]"
        if not isinstance(message, dict):
            collector.error("messages.type", f"{loc}", f"message must be an object, got {type(message).__name__}")
            continue
        role = message.get("role")
        content = message.get("content")
        if role not in ROLES:
            collector.error("messages.role", f"{loc}.role", f"role must be one of {list(ROLES)}, got {role!r}")
        if not isinstance(content, str):
            collector.error("messages.content", f"{loc}.content", f"content must be a string, got {type(content).__name__}")
        elif not content.strip():
            collector.error("messages.empty", f"{loc}.content", "content is empty or whitespace-only")
        elif len(content) > MAX_INPUT_CHARS:
            collector.error(
                "messages.too_long",
                f"{loc}.content",
                f"content is {len(content)} chars, limit is {MAX_INPUT_CHARS}",
            )
        if role in ROLES and isinstance(content, str) and content.strip():
            out.append((str(role), content))
    if not out:
        return None
    return tuple(out)


def _check_label_leakage(case: dict[str, Any], expected_label: str | None, location: str, collector: IssueCollector) -> None:
    """Reject metadata that restates the answer.

    A ``notes`` field containing "expected_label: powerful" turns a dataset
    into a place where the answer is written twice, and invites leakage into
    any future model that consumes metadata. Also reject unexpected keys whose
    names look like an answer field, so a typo cannot smuggle one in.
    """
    if expected_label is None:
        return
    for key in sorted(case):
        if key in _ALLOWED_KEYS:
            continue
        if _LEAK_KEY_RE.search(key):
            collector.error(
                "metadata.leaky_key",
                f"{location}.{key}",
                f"unexpected key {key!r}; keys matching label/expected/answer/target/gold/tier "
                "are rejected to prevent label leakage",
            )
    if expected_label not in case.get("notes", "").casefold():
        return
    # Only flag when the label appears as a whole word, so a note mentioning
    # "fastapi" is not mistaken for a leak of the "fast" tier.
    pattern = re.compile(rf"(?<!\w){re.escape(expected_label)}(?!\w)", re.IGNORECASE)
    for field_name in ("notes", "category"):
        value = case.get(field_name)
        if isinstance(value, str) and pattern.search(value):
            collector.error(
                "metadata.label_leakage",
                f"{location}.{field_name}",
                f"{field_name!r} restates the expected label {expected_label!r}; "
                "reasons belong in evals/REVIEW.md, not in the dataset",
            )


def _check_unicode(value: str, location: str, collector: IssueCollector) -> None:
    if _CONTROL_RE.search(value):
        collector.error(
            "input.control_chars",
            location,
            "contains control characters; escape them or use printable text only",
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        collector.error("input.unencodable", location, "contains unpaired surrogates and is not encodable as UTF-8")
    if any(unicodedata.category(ch) == "Cs" for ch in value):
        collector.error("input.surrogate", location, "contains Unicode surrogate code points")


def _coerce_case(raw: Any, index: int, collector: IssueCollector) -> EvalCase | None:
    location = f"cases[{index}]"
    if isinstance(raw, str):
        collector.error("case.type", location, f"case must be an object, got the string {raw[:40]!r}")
        return None
    if not isinstance(raw, dict):
        collector.error("case.type", location, f"case must be an object, got {type(raw).__name__}")
        return None

    # --- id ---
    case_id = raw.get("id")
    if case_id is None:
        collector.error("id.missing", f"{location}.id", "required field 'id' is missing")
    elif not isinstance(case_id, str):
        collector.error("id.type", f"{location}.id", f"id must be a string, got {type(case_id).__name__}")
    elif not case_id.strip():
        collector.error("id.empty", f"{location}.id", "id is empty or whitespace-only")
    elif len(case_id) > MAX_ID_LEN:
        collector.error("id.too_long", f"{location}.id", f"id is {len(case_id)} chars, limit is {MAX_ID_LEN}")
    elif not _ID_RE.match(case_id):
        collector.error(
            "id.format",
            f"{location}.id",
            f"id {case_id!r} must match [a-z0-9][a-z0-9_.-]*",
        )

    # --- input (accept v1 'prompt') ---
    has_input = "input" in raw
    has_prompt = "prompt" in raw
    if has_input and has_prompt:
        collector.error(
            "input.ambiguous",
            location,
            "case defines both 'input' and 'prompt'; use 'input'",
        )
    text = raw.get("input", raw.get("prompt"))
    if text is None:
        collector.error("input.missing", f"{location}.input", "required field 'input' is missing")
    elif not isinstance(text, str):
        collector.error("input.type", f"{location}.input", f"input must be a string, got {type(text).__name__}")
    else:
        if not text:
            collector.error("input.empty", f"{location}.input", "input is empty")
        elif not text.strip():
            collector.error("input.blank", f"{location}.input", "input is whitespace-only")
        elif len(text) > MAX_INPUT_CHARS:
            collector.error(
                "input.too_long",
                f"{location}.input",
                f"input is {len(text)} chars, limit is {MAX_INPUT_CHARS}",
            )
        else:
            _check_unicode(text, f"{location}.input", collector)

    # --- expected label (accept v1 'expected_tier') ---
    has_label = "expected_label" in raw
    has_tier = "expected_tier" in raw
    if has_label and has_tier:
        collector.error(
            "expected_label.ambiguous",
            location,
            "case defines both 'expected_label' and 'expected_tier'; use 'expected_label'",
        )
    label = raw.get("expected_label", raw.get("expected_tier"))
    if label is None:
        collector.error(
            "expected_label.missing",
            f"{location}.expected_label",
            "required field 'expected_label' is missing",
        )
        label = None
    elif not isinstance(label, str):
        collector.error(
            "expected_label.type",
            f"{location}.expected_label",
            f"expected_label must be a string, got {type(label).__name__}",
        )
        label = None
    elif label not in TIERS:
        collector.error(
            "expected_label.invalid",
            f"{location}.expected_label",
            f"expected_label {label!r} is not a known tier {list(TIERS)}",
        )
        label = None

    # --- difficulty ---
    # A v1 record has no 'difficulty'. Inferring it from the label would be
    # circular -- the whole point of the field is that a human decided the case
    # is ambiguous, and no code can recover that decision. Legacy records get
    # 'ambiguous', the conservative value, so a legacy file cannot silently
    # inflate a clear-case gate.
    is_legacy_shape = "difficulty" not in raw and "prompt" in raw
    difficulty = raw.get("difficulty")
    if difficulty is None:
        if is_legacy_shape:
            difficulty = "ambiguous"
        else:
            collector.error("difficulty.missing", f"{location}.difficulty", "required field 'difficulty' is missing")
            difficulty = None
    elif not isinstance(difficulty, str):
        collector.error(
            "difficulty.type",
            f"{location}.difficulty",
            f"difficulty must be a string, got {type(difficulty).__name__}",
        )
        difficulty = None
    elif difficulty not in DIFFICULTIES:
        collector.error(
            "difficulty.invalid",
            f"{location}.difficulty",
            f"difficulty {difficulty!r} must be one of {list(DIFFICULTIES)}",
        )
        difficulty = None

    # --- source ---
    source = raw.get("source", "synthetic")
    if not isinstance(source, str):
        collector.error("source.type", f"{location}.source", f"source must be a string, got {type(source).__name__}")
        source = None
    elif source not in SOURCES:
        collector.error("source.invalid", f"{location}.source", f"source {source!r} must be one of {list(SOURCES)}")
        source = None

    # --- category ---
    category = raw.get("category", "uncategorized")
    if not isinstance(category, str) or not category.strip():
        collector.error("category.type", f"{location}.category", "category must be a non-empty string")
        category = None

    # --- adversarial tags ---
    adversarial_raw = raw.get("adversarial", [])
    adversarial: tuple[str, ...] = ()
    if adversarial_raw is None:
        adversarial_raw = []
    if not isinstance(adversarial_raw, list):
        collector.error(
            "adversarial.type",
            f"{location}.adversarial",
            f"adversarial must be a list, got {type(adversarial_raw).__name__}",
        )
    else:
        tags = []
        for i, tag in enumerate(adversarial_raw):
            if not isinstance(tag, str) or not tag.strip():
                collector.error(
                    "adversarial.tag",
                    f"{location}.adversarial[{i}]",
                    f"adversarial tag must be a non-empty string, got {tag!r}",
                )
                continue
            tags.append(tag)
        adversarial = tuple(tags)

    # --- notes ---
    notes = raw.get("notes", "")
    if notes is None:
        notes = ""
    if not isinstance(notes, str):
        collector.error("notes.type", f"{location}.notes", f"notes must be a string, got {type(notes).__name__}")
        notes = ""

    # --- messages ---
    messages: tuple[tuple[str, str], ...] | None = None
    if "messages" in raw:
        messages = _validate_messages(raw["messages"], f"{location}.messages", collector)
        if messages and isinstance(text, str) and text.strip():
            last_user = next((c for r, c in reversed(messages) if r == "user"), None)
            if last_user is None:
                collector.error(
                    "messages.no_user",
                    f"{location}.messages",
                    "messages contain no user turn, but 'input' is the last user message",
                )
            elif last_user != text:
                collector.error(
                    "messages.input_mismatch",
                    f"{location}.messages",
                    "the last user message in 'messages' differs from 'input'",
                )

    _check_label_leakage(raw, label, location, collector)

    if None in (case_id, text, label, difficulty, source, category):
        return None
    if not isinstance(case_id, str) or not isinstance(text, str) or not isinstance(notes, str):
        return None

    provenance = raw.get("provenance")
    if provenance is not None and not isinstance(provenance, dict):
        collector.error("provenance.type", f"{location}.provenance", "provenance must be an object when present")
        provenance = None

    return EvalCase(
        id=case_id,
        input=text,
        expected_label=label,
        difficulty=difficulty,
        source=source,
        category=category,
        adversarial=adversarial,
        notes=notes,
        messages=messages,
        provenance=provenance,
    )


def _check_duplicates(cases: Sequence[EvalCase], collector: IssueCollector) -> None:
    by_id: dict[str, list[int]] = {}
    by_input: dict[str, list[str]] = {}
    for position, case in enumerate(cases):
        by_id.setdefault(case.id, []).append(position)
        by_input.setdefault(normalize_for_duplicates(case.input), []).append(case.id)

    for case_id, positions in sorted(by_id.items()):
        if len(positions) > 1:
            collector.error(
                "id.duplicate",
                case_id,
                f"id appears {len(positions)} times at cases {positions}; ids must be unique",
            )

    for norm, ids in sorted(by_input.items()):
        if len(ids) > 1:
            shown = ", ".join(sorted(ids)[:6])
            more = "" if len(ids) <= 6 else f" (+{len(ids) - 6} more)"
            collector.error(
                "input.duplicate",
                shown,
                f"{len(ids)} cases share the same normalised input{more}; "
                "duplicates inflate accuracy and leak across train/test folds",
            )

    # Near-duplicates: warn, do not error. Templated prompts legitimately
    # overlap, and refusing to load the dataset over a warning would be a
    # worse failure than recording it.
    token_map = [(case.id, _word_tokens(case.input)) for case in cases]
    reported: set[tuple[str, str]] = set()
    for i in range(len(token_map)):
        id_a, tokens_a = token_map[i]
        if len(tokens_a) < 4:
            continue
        for j in range(i + 1, len(token_map)):
            id_b, tokens_b = token_map[j]
            if len(tokens_b) < 4:
                continue
            if abs(len(tokens_a) - len(tokens_b)) > max(len(tokens_a), len(tokens_b)) * 0.5:
                continue
            score = jaccard(tokens_a, tokens_b)
            if score >= 0.8:
                key = (id_a, id_b) if id_a < id_b else (id_b, id_a)
                if key in reported:
                    continue
                reported.add(key)
                collector.warn(
                    "input.near_duplicate",
                    f"{id_a} ~ {id_b}",
                    f"token Jaccard similarity {score:.2f} (>= 0.80); "
                    "review for templated padding and cross-fold leakage",
                )


def validate_cases(raw_cases: Iterable[Any], *, limit: int | None = MAX_CASES) -> tuple[list[EvalCase], list[ValidationIssue]]:
    """Validate raw records into :class:`EvalCase` objects.

    Returns the valid cases plus every issue found. Raises
    :class:`DatasetValidationError` only when at least one issue is an error;
    warnings are returned to the caller for recording.
    """
    collector = IssueCollector()
    raw_list = list(raw_cases)

    if limit is not None and len(raw_list) > limit:
        raise DatasetError(f"Dataset has {len(raw_list)} cases, limit is {limit}")

    cases: list[EvalCase] = []
    for index, raw in enumerate(raw_list):
        case = _coerce_case(raw, index, collector)
        if case is not None:
            cases.append(case)

    if cases:
        _check_duplicates(cases, collector)

    if collector.errors:
        raise DatasetValidationError(collector.issues)
    return cases, collector.issues


def load_dataset(path: str | Path) -> tuple[list[EvalCase], list[ValidationIssue]]:
    """Load and validate a golden dataset file.

    Returns ``(cases, warnings)``. Raises :class:`DatasetError` for I/O and
    parse failures, :class:`DatasetValidationError` for content failures.
    """
    resolved = Path(path)
    text = _read_text(resolved)
    raw_cases = parse_dataset_text(text, path=str(resolved))
    cases, issues = validate_cases(raw_cases)
    if not cases:
        raise DatasetError(f"Dataset {resolved} contains zero usable cases")
    return cases, issues


def dataset_fingerprint(cases: Sequence[EvalCase]) -> str:
    """SHA-256 over the canonical, sorted case payloads.

    Order-independent so re-sorting the file does not change the hash, and
    host-independent so CI and a laptop agree.
    """
    canonical = json.dumps(
        [case.fingerprint_payload() for case in sorted(cases, key=lambda c: c.id)],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def dataset_summary(cases: Sequence[EvalCase]) -> dict[str, Any]:
    """Deterministic distribution report, sorted by key."""
    from collections import Counter

    by_difficulty = Counter(c.difficulty for c in cases)
    by_label = Counter(c.expected_label for c in cases)
    by_source = Counter(c.source for c in cases)
    by_category = Counter(c.category for c in cases)
    adversarial_tags: Counter[str] = Counter(tag for c in cases for tag in c.adversarial)
    multi_turn = sum(1 for c in cases if c.messages and len(c.messages) > 1)

    return {
        "total_cases": len(cases),
        "schema_version": SCHEMA_VERSION,
        "fingerprint": dataset_fingerprint(cases),
        "by_difficulty": {k: by_difficulty[k] for k in DIFFICULTIES if by_difficulty[k]},
        "by_expected_label": {k: by_label[k] for k in TIERS if by_label[k]},
        "by_source": {k: by_source[k] for k in SOURCES if by_source[k]},
        "by_category": dict(sorted(by_category.items())),
        "adversarial_tags": dict(sorted(adversarial_tags.items())),
        "multi_turn_cases": multi_turn,
        "unique_inputs": len({normalize_for_duplicates(c.input) for c in cases}),
    }


__all__ = [
    "DIFFICULTIES",
    "EvalCase",
    "MAX_INPUT_CHARS",
    "ROLES",
    "SCHEMA_VERSION",
    "SOURCES",
    "TIERS",
    "ValidationIssue",
    "dataset_fingerprint",
    "dataset_summary",
    "load_dataset",
    "normalize_for_duplicates",
    "parse_dataset_text",
    "validate_cases",
]