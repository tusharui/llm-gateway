"""Safe ingestion of production traffic into the golden set.

The rule this module enforces: **raw production text never reaches the
dataset, and it never reaches a log.** Everything here is designed so that the
person who wires this up next quarter cannot accidentally commit a customer's
API key.

## Pipeline

1. ``redact_for_dataset`` removes credentials (reusing ``app.redact``), then
   PII: emails, phone numbers, payment card and IBAN shapes, IP addresses,
   JWTs, UUIDs, ``@handles``, and long high-entropy tokens that look like keys,
   hashes or bearer values.
2. ``find_residual_risks`` re-inspects the redacted text and raises if anything
   suspicious survived. This is the fail-closed step. Redaction patterns are
   the first line of defence, not the whole defence, and a missed pattern must
   stop the run rather than produce a case that quietly carries a secret.
3. ``stage_production_records`` deterministically samples the survivors and
   emits *unlabelled* candidates. Labels are a human decision; the code never
   invents one.
4. ``ingest_production_cases`` turns labelled candidates into dataset records
   with provenance that describes where the case came from without identifying
   who sent it.

## What is deliberately not carried over

Request ids, user ids, session ids, IP addresses, timestamps to the second,
API keys, account names. Provenance records a capture *date* and a redactor
version, which is what is needed to reproduce and audit, and nothing that
points at a person.

## Determinism

Ids and sampling both come from SHA-256 over content plus a seed, so
re-ingesting the same traffic produces the same case ids. That is what lets
``evals.dataset`` detect a case that is already in the set instead of quietly
adding a near-duplicate.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.redact import REDACTED as CREDENTIAL_PLACEHOLDER
from app.redact import redact as redact_credentials

from evals.dataset import TIERS
from evals.errors import EvalError

REDACTOR_VERSION = "1"
REDACTION_MARKER = f"[{REDACTOR_VERSION}:redacted]"

# Ordered. Secrets first, then identities, then identifiers. Order matters: a
# card number inside a longer digit run must be matched before the generic
# long-digit rule consumes it.
PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{12,}")),
    ("google_key", re.compile(r"\bAIza[A-Za-z0-9_-]{20,}")),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{12,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("private_key_block", re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----")),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,19}\b(?![ -]?\d)")) if False else
    # 13-19 digits, optionally grouped by single spaces or hyphens. The
    # trailing lookahead stops the pattern from swallowing the separator
    # before the next word, which turned "card 4111 1111 1111 1111 expires" into
    # "...expires" with no space -- silent corruption of the dataset.
    ("credit_card", re.compile(r"\b(?:\d{4}[ -]){3}\d{4}\b|\b\d{13,19}\b")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
    # TLD optional: "user@localhost" and "user@internal-host" are the shapes
    # that appear in infrastructure questions, and a required TLD misses both.
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\b")),
    ("phone", re.compile(r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{3,4}[\s.-]\d{3,4}(?:[\s.-]\d{2,4})?(?![\w.])|\+\d{10,15}\b|(?<![\w.])\d{10,15}(?![\w.])")),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("uuid", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("long_hex", re.compile(r"\b[0-9a-fA-F]{24,}\b")),
    ("handle", re.compile(r"(?<![\w@/])@[A-Za-z0-9_]{3,}")),
    ("url_with_credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@]+:[^\s/@]+@[^\s]+", re.IGNORECASE)),
)

# Only these keys are read out of an inbound record. Anything else -- user_id,
# session_id, api_key, ip, headers -- is not copied into the output at all.
ALLOWED_SOURCE_FIELDS = ("prompt", "input", "content", "messages", "captured_at", "tier_hint")

# Subset of ALLOWED_SOURCE_FIELDS that can hold user text. captured_at and
# tier_hint are deliberately not here: a capture timestamp is not a prompt,
# and treating one as text would put "2026-03-04" into the dataset.
TEXT_FIELDS = ("prompt", "input", "content", "messages")

_MIN_ENTROPY_TOKEN_LENGTH = 24
_MIN_ENTROPY_BITS_PER_CHAR = 3.8
_MIN_PASSPHRASE_LENGTH = 28
_MIN_PASSPHRASE_BITS_PER_CHAR = 3.2


class ProductionIngestionError(EvalError):
    """A production record could not be made safe for the dataset."""


@dataclass(frozen=True)
class RedactionResult:
    text: str
    applied: tuple[str, ...]
    residuals: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.residuals


# `_TOKEN_STRIP` is what defeated the first version of this module. Tokenizing
# on whitespace and stripping only punctuation left `key=SECRET` intact,
# because '=' was not in the strip set, so the single most common credential
# shape in a log line survived both redaction and the residual check.
# Assignment separators are stripped so the value is examined on its own.
_TOKEN_STRIP = ".,;:!?\"'()[]{}<>="


def _token_forms(token: str) -> set[str]:
    """Candidate forms of a whitespace-delimited token.

    Both the whole token and its assignment value, so ``token=abc`` is checked
    as ``abc`` and ``abc`` alike.
    """
    stripped = token.strip(_TOKEN_STRIP)
    forms = {stripped}
    if "=" in stripped:
        forms.add(stripped.split("=", 1)[1])
    for form in list(forms):
        forms.add(form.split(":", 1)[-1] if form.count(":") == 1 and "/" not in form else form)
    return {f for f in forms if f}


def shannon_entropy(token: str) -> float:
    if not token:
        return 0.0
    counts = Counter(token)
    length = len(token)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def _looks_like_secret(token: str) -> bool:
    """True when a token looks like a key, token, hash or passphrase.

    Two shapes count. The first is a high-entropy base64/hex run that mixes
    letters and digits. The second is a long lowercase passphrase with no
    digits, which the first rule misses entirely -- ``correcthorsebatterystaple``
    twice over is a real passphrase and has no digit in it.
    """
    if not token:
        return False
    has_letter = any(c.isalpha() for c in token)
    has_digit = any(c.isdigit() for c in token)
    base64_or_hex = re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", token) or re.fullmatch(
        r"[0-9a-fA-F]+", token
    )
    if base64_or_hex and has_letter and has_digit:
        if len(token) >= _MIN_ENTROPY_TOKEN_LENGTH:
            return shannon_entropy(token) >= _MIN_ENTROPY_BITS_PER_CHAR
        # Short mixed alphanumerics are still worth flagging when they sit in an
        # assignment position; the caller decides via the shape, not entropy.
        return False
    if len(token) >= _MIN_PASSPHRASE_LENGTH and has_letter and not has_digit:
        if not re.fullmatch(r"[A-Za-z]+", token):
            return False
        # An internal capital means a CamelCase or PascalCase identifier, not a
        # passphrase. Long class and function names are ordinary prompt text and
        # redacting them destroys the eval signal, which is its own failure.
        if any(c.isupper() for c in token[1:]):
            return False
        return shannon_entropy(token) >= _MIN_PASSPHRASE_BITS_PER_CHAR
    return False


def find_residual_risks(text: str, *, allowlist: Sequence[str] = ()) -> list[str]:
    """Names of the checks that still match after redaction.

    An empty list means the text is judged safe. Anything else must stop the
    ingest: this is the step that catches a credential shape nobody thought to
    write a pattern for.
    """
    risks: list[str] = []
    allowed = {token.casefold() for token in allowlist}

    for name, pattern in PII_PATTERNS:
        for match in pattern.finditer(text):
            if match.group(0).casefold() in allowed:
                continue
            risks.append(name)
            break

    for token in re.findall(r"\S+", text):
        for form in _token_forms(token):
            if form.casefold() in allowed:
                continue
            if _looks_like_secret(form):
                risks.append("high_entropy_token")
                break
        else:
            continue
        break
    return sorted(set(risks))


def redact_for_dataset(
    text: str, *, allowlist: Sequence[str] = (), marker: str = REDACTION_MARKER
) -> RedactionResult:
    """Redact credentials then PII, and report what was touched.

    ``allowlist`` is honoured for real here as well as in the residual check. An
    earlier version accepted the parameter and ignored it, which meant the
    documented instruction "add the value to the allowlist" silently did
    nothing.

    The original text is never returned and never logged. The caller gets the
    redacted text plus the names of the patterns that fired, which is safe to
    record in provenance.
    """
    if not isinstance(text, str):
        raise ProductionIngestionError(
            f"production text must be a string, got {type(text).__name__}"
        )
    original = text
    cleaned = unicodedata.normalize("NFKC", text)
    # app.redact owns credential shapes; this module must not fork them. Its
    # patterns fire first, so a key it already removed is recorded under the
    # aggregate name "credentials" rather than showing up as no redaction at all.
    cleaned = redact_credentials(cleaned)

    allowed = {token.casefold() for token in allowlist}
    if allowed:
        for pattern_value in sorted(allowed, key=len, reverse=True):
            if pattern_value:
                cleaned = re.sub(re.escape(pattern_value), marker + ":allowlisted", cleaned, flags=re.I)

    applied: list[str] = []
    for name, pattern in PII_PATTERNS:
        replacement = f"{marker}:{name}"
        cleaned, count = pattern.subn(replacement, cleaned)
        if count:
            applied.append(name)

    def _replace_secret(match: re.Match[str]) -> str:
        token = match.group(0)
        if any(_looks_like_secret(form) for form in _token_forms(token)):
            return f"{marker}:high_entropy_token"
        return token

    tokens = re.findall(r"\S+", cleaned)
    if any(_looks_like_secret(form) for token in tokens for form in _token_forms(token)):
        cleaned = re.sub(r"\S+", _replace_secret, cleaned)
        applied.append("high_entropy_token")

    if applied == [] and cleaned != original:
        # Only app.redact changed the text, which is still a redaction.
        applied.append("credentials")

    return RedactionResult(text=cleaned, applied=tuple(sorted(set(applied))), residuals=())


def _record_text(record: Mapping[str, Any]) -> str:
    """Extract the user text from an inbound record, without trusting its shape.

    Reads only from TEXT_FIELDS, which is a subset of ALLOWED_SOURCE_FIELDS.
    Every other key on the record is ignored entirely, so a ``user_id`` or
    ``api_key`` field cannot end up in the output even by accident.
    """
    if not isinstance(record, Mapping):
        raise ProductionIngestionError(
            f"production record must be a mapping, got {type(record).__name__}"
        )
    for field_name in TEXT_FIELDS:
        value = record.get(field_name)
        if field_name == "messages":
            if not isinstance(value, list):
                continue
            parts = []
            for message in value:
                if isinstance(message, Mapping) and message.get("role") == "user":
                    content = message.get("content")
                    if isinstance(content, str):
                        parts.append(content)
            if parts:
                return "\n".join(parts)
            continue
        if isinstance(value, str) and value.strip():
            return value
    raise ProductionIngestionError(
        "record has no usable user text: expected a non-empty 'prompt'/'input'/'content' "
        "string, or a 'messages' list containing a user turn"
    )


def stable_id(prefix: str, payload: str, *, length: int = 10) -> str:
    """Content-derived, deterministic id.

    Re-ingesting the same traffic yields the same id, so a case already in the
    set is detected as a duplicate rather than added twice.
    """
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:length]}"


def _capture_date(record: Mapping[str, Any]) -> str | None:
    """Date only. A timestamp would narrow an incident window to the second."""
    captured = record.get("captured_at")
    if isinstance(captured, str) and len(captured) >= 10:
        candidate = captured[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
            return candidate
    return None


def deterministic_sample(
    records: Sequence[Mapping[str, Any]],
    *,
    n: int,
    seed: int,
    strata_key: str | None = None,
) -> list[Mapping[str, Any]]:
    """Hash-ordered sample. Same input and seed gives the same sample, forever.

    Ranking by ``sha256(seed || content)`` rather than by arrival order or a
    random shuffle means the sample does not shift when the log is replayed
    with a different window, and it does not correlate with recency.
    """
    if n <= 0:
        return []

    def rank(_index: int, record: Mapping[str, Any]) -> str:
        try:
            body = _record_text(record)
            stratum = str(record.get(strata_key, "")) if strata_key else ""
        except (ProductionIngestionError, AttributeError, TypeError):
            # A record that cannot even be read is ranked by position so it
            # still occupies a deterministic slot and is reported later.
            body = ""
            stratum = ""
        material = f"{seed}|{strata_key or ''}|{stratum}|{body}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    ordered = sorted(range(len(records)), key=lambda i: (rank(i, records[i]), i))
    if strata_key is None:
        return [records[i] for i in ordered[:n]]

    # Stratified: round-robin across strata so a rare stratum is not crowded out.
    buckets: dict[Any, list[Mapping[str, Any]]] = {}
    for index in ordered:
        record = records[index]
        key = record.get(strata_key) if isinstance(record, Mapping) else None
        buckets.setdefault(key, []).append(record)
    chosen: list[Mapping[str, Any]] = []
    depth = 0
    while len(chosen) < n and any(len(b) > depth for b in buckets.values()):
        for key in sorted(buckets, key=lambda k: (k is None, str(k))):
            if depth < len(buckets[key]) and len(chosen) < n:
                chosen.append(buckets[key][depth])
        depth += 1
    return chosen


@dataclass
class StagedCandidate:
    """A redacted, unlabelled production case awaiting a human label."""

    case_id: str
    redacted_input: str
    captured_date: str | None
    redactions_applied: tuple[str, ...]
    source_index: int
    residual_risks: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "redacted_input": self.redacted_input,
            "captured_date": self.captured_date,
            "redactions_applied": list(self.redactions_applied),
            "residual_risks": list(self.residual_risks),
            "label": None,
            "labeler": None,
            "source_index": self.source_index,
            "notes": list(self.notes),
        }


def stage_production_records(
    records: Sequence[Mapping[str, Any]],
    *,
    n: int = 100,
    seed: int = 0,
    strata_key: str | None = None,
    allowlist: Sequence[str] = (),
    allow_residual: bool = False,
) -> list[StagedCandidate]:
    """Redact, fail closed on residue, and sample deterministically.

    ``allow_residual`` exists so a reviewer can inspect what the redactor
    flagged, and it is deliberately not reachable from the CLI without an
    explicit flag. It is never the default.
    """
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ProductionIngestionError("records must be a sequence of mappings, not a string")

    sampled = deterministic_sample(records, n=n, seed=seed, strata_key=strata_key)
    staged: list[StagedCandidate] = []
    problems: list[str] = []

    for source_index, record in enumerate(sampled):
        if not isinstance(record, Mapping):
            problems.append(f"record {source_index} of type {type(record).__name__} is not a mapping")
            continue
        try:
            body = _record_text(record)
        except ProductionIngestionError as exc:
            # One unreadable record must not abort a batch of ten thousand.
            problems.append(f"record {source_index}: {exc}")
            continue
        result = redact_for_dataset(body, allowlist=allowlist)
        risks = find_residual_risks(result.text, allowlist=allowlist)
        if risks and not allow_residual:
            problems.append(
                f"candidate {stable_id('prod', result.text)} still matches {risks} after "
                "redaction; refusing to stage it. Add a pattern or an allowlist entry "
                "rather than passing --allow-residual."
            )
            continue
        captured = _capture_date(record)
        case_id = stable_id("prod", f"{captured or ''}|{result.text}", length=12)
        staged.append(
            StagedCandidate(
                case_id=case_id,
                redacted_input=result.text,
                captured_date=captured,
                redactions_applied=result.applied,
                source_index=source_index,
                residual_risks=tuple(risks),
                notes=["label pending human review"] if not risks else ["label pending", "redactor flagged residue"],
            )
        )

    if problems and not staged:
        raise ProductionIngestionError(
            "no production record could be staged safely:\n  " + "\n  ".join(problems)
        )
    if problems:
        # Surfaced, not swallowed, and not written anywhere.
        import sys

        print(
            f"warning: skipped {len(problems)} production record(s):\n  "
            + "\n  ".join(problems),
            file=sys.stderr,
        )
    return staged


def ingest_production_cases(
    staged: Sequence[StagedCandidate | Mapping[str, Any]],
    labels: Mapping[str, str],
    *,
    labeler: str,
    difficulty: str = "ambiguous",
    category: str = "production_traffic",
    notes: str = "",
    expected_provenance_fields: Sequence[str] = ("captured_date", "redactor_version"),
) -> list[dict[str, Any]]:
    """Turn labelled candidates into dataset records.

    A candidate with no human label is an error, not a case. The code is not
    permitted to guess: a wrong golden label is worse than a missing one,
    because a missing one is visible.
    """
    if not labeler or not isinstance(labeler, str):
        raise ProductionIngestionError("ingest_production_cases requires the name of the human labeller")
    if difficulty not in ("clear", "ambiguous"):
        raise ProductionIngestionError(f"difficulty must be 'clear' or 'ambiguous', got {difficulty!r}")

    out: list[dict[str, Any]] = []
    missing: list[str] = []
    for candidate in staged:
        record = candidate.to_dict() if isinstance(candidate, StagedCandidate) else dict(candidate)
        case_id = record.get("case_id")
        label = labels.get(case_id) if isinstance(case_id, str) else None
        if label is None:
            missing.append(str(case_id))
            continue
        if label not in TIERS:
            raise ProductionIngestionError(
                f"candidate {case_id} was labelled {label!r}; valid tiers are {list(TIERS)}"
            )
        risks = record.get("residual_risks") or []
        if risks:
            raise ProductionIngestionError(
                f"candidate {case_id} still carries redactor findings {risks}; it must not "
                "enter the dataset until a human resolves them"
            )
        provenance = {
            "source": "production",
            "captured_date": record.get("captured_date"),
            "redactor_version": REDACTOR_VERSION,
            "redactions_applied": list(record.get("redactions_applied") or []),
            "labeler": labeler,
            "contains_user_identifiers": False,
            "note": "no request id, user id, session id, ip or timestamp is retained",
        }
        missing_fields = [f for f in expected_provenance_fields if f not in provenance]
        if missing_fields:
            raise ProductionIngestionError(
                f"candidate {case_id}: provenance is missing {missing_fields}"
            )
        out.append(
            {
                "id": case_id,
                "input": record["redacted_input"],
                "expected_label": label,
                "difficulty": difficulty,
                "source": "production",
                "category": category,
                "notes": notes or "drawn from production traffic, redacted and hand-labelled",
                "provenance": provenance,
            }
        )

    if missing:
        raise ProductionIngestionError(
            f"{len(missing)} staged candidate(s) have no human label: {missing[:8]}. "
            "Label them or drop them; the harness will not guess a tier."
        )
    return out


def write_production_shard(path: str, cases: Sequence[Mapping[str, Any]]) -> int:
    """Append labelled production cases to a JSONL shard.

    Genuinely appends. An earlier version delegated to an atomic *replace*,
    so a second ingest batch silently deleted the first one from the golden
    set -- and the dataset fingerprint changed with no error to notice.

    Writes to a sibling temp file and renames it into place, so an interrupted
    run cannot leave a half-written shard. Existing ids are read first and
    reported rather than duplicated.

    Returns the number of cases written.
    """
    import json

    from evals.report import write_text_atomic

    target = Path(path)
    existing_ids: set[str] = set()
    existing_lines: list[str] = []
    if target.exists():
        try:
            raw = target.read_text(encoding="utf-8")
        except PermissionError as exc:
            raise ProductionIngestionError(f"cannot read existing shard {target}: permission denied") from exc
        except OSError as exc:
            raise ProductionIngestionError(f"cannot read existing shard {target}: {exc}") from exc
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ProductionIngestionError(
                    f"existing shard {target} is not valid JSONL (line {exc.lineno}): {exc.msg}. "
                    "Fix or remove the file before appending; a partially written shard must "
                    "not be extended."
                ) from exc
            existing_lines.append(stripped)
            case_id = record.get("id") if isinstance(record, dict) else None
            if isinstance(case_id, str):
                existing_ids.add(case_id)

    new_lines = [json.dumps(case, ensure_ascii=False, sort_keys=True) for case in cases]
    duplicates = sorted(
        str(case.get("id")) for case in cases if str(case.get("id")) in existing_ids
    )
    if duplicates:
        raise ProductionIngestionError(
            f"{len(duplicates)} case(s) already exist in {target} and would be duplicated: "
            f"{duplicates[:8]}. Ids are content-derived, so this usually means the same traffic "
            "was ingested twice with the same seed."
        )

    write_text_atomic(target, "\n".join(existing_lines + new_lines))
    return len(new_lines)


__all__ = [
    "ALLOWED_SOURCE_FIELDS",
    "PII_PATTERNS",
    "REDACTOR_VERSION",
    "ProductionIngestionError",
    "RedactionResult",
    "StagedCandidate",
    "deterministic_sample",
    "find_residual_risks",
    "ingest_production_cases",
    "redact_for_dataset",
    "shannon_entropy",
    "stable_id",
    "stage_production_records",
    "write_production_shard",
]