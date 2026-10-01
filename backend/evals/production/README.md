# Bringing production traffic into the golden set

The golden set is synthetic today. This is the path to adding real traffic
without committing a customer's API key, an email address, or the fact that
somebody asked a question at 03:14 on a Tuesday.

## The workflow

```
production logs  ->  redact  ->  fail closed on residue  ->  deterministic sample
                ->  stage (unlabelled)  ->  human labels  ->  evals/golden/07_production.jsonl
```

Each arrow is a separate, reviewable step. Nothing skips ahead.

## 1. Redact and stage

```python
from evals.production import stage_production_records, write_production_shard
import json

staged = stage_production_records(
    records,          # list of dicts with at least a prompt/input/content or messages
    n=100,
    seed=20250101,    # change this to draw a different sample
    strata_key="tier_hint",   # optional; keeps rare strata represented
)
write_production_shard("evals/production/staged.jsonl", [s.to_dict() for s in staged])
```

`evals/production/*.staged.jsonl` is gitignored. It is redacted, but it is
still derived from real people and is still unlabelled.

`stage_production_records` **raises** if anything suspicious survives
redaction. If a record is skipped for that reason it is named on stderr, and
if nothing at all survives it raises rather than returning an empty list that
looks like "no traffic today".

## 2. A human labels each candidate

Open the staged file and fill in `label` (`fast` / `balanced` / `powerful`),
`labeler` (a person, not a script), and `difficulty`. Label from the tier
rubric in `../SCHEMA.md`, not from what the router currently does. A human who
sees the router's prediction first will anchor on it, and then the eval is
measuring the router against itself.

`label: null` is the expected starting state and is not an oversight.

## 3. Ingest

```python
from evals.production import ingest_production_cases

cases = ingest_production_cases(
    staged_or_dicts,
    labels={case_id: "balanced", ...},
    labeler="your-name",
    difficulty="ambiguous",
)
write_production_shard("evals/golden/07_production.jsonl", cases)
```

`ingest_production_cases` refuses to invent a label. A candidate with no label
is an error, because a wrong golden label is worse than a missing one: a
missing one is visible.

## What provenance does and does not contain

Contains: capture **date** (not timestamp), redactor version, the names of
the patterns that fired, the labeller, and `contains_user_identifiers: false`.

Does not contain: request id, user id, session id, ip address, account name,
api key, or any second-level timestamp. Those are not in the record at all --
`_record_text` reads only from an allowlist of field names, so an unexpected
key cannot be copied by accident.

## Determinism

Case ids are `sha256(captured_date | redacted_text)` and the sample is
ranked by `sha256(seed | stratum | text)`. Two consequences worth relying on:

- Re-running with the same seed over the same traffic gives the same cases, so
  `evals.dataset` flags an already-present case as a duplicate instead of
  quietly doubling it.
- A different seed gives a genuinely different sample, so the set can grow
  over time without re-reviewing cases already labelled.

## Residual risk

`find_residual_risks` runs after redaction and looks for credential shapes
the patterns missed, including base64 and hex tokens with high Shannon
entropy. It is a heuristic, not a proof. Treat a clean result as "nothing
obvious survived", never as "this is safe to publish", and read a sample of
the staged file before committing it.

If a case legitimately contains something the redactor flags -- a customer's
own commit hash, a public IP in a bug report -- add it to `allowlist` for that
run rather than passing `allow_residual=True`, which skips the check entirely
and exists only for debugging the redactor itself.