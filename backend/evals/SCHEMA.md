"""Golden dataset schema reference.

Two files, two shapes, one schema after loading.

## v2 file (current, `evals/golden.json`)

```json
{
  "version": 2,
  "cases": [ ... ]
}
```

JSONL (one case object per line) is also accepted for future dumps; the loader
tries JSON object, then JSON array, then JSONL, in that order.

## v1 file (legacy, still loadable)

A bare JSON array of `{id, prompt, expected_tier}`. The 18 original cases keep
their ids so old results stay comparable. A v1 file is missing `difficulty`,
so validation reports that as an error rather than guessing -- silently
defaulting the field is exactly how an eval set drifts without anyone noticing.

## Case fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | `[a-z0-9][a-z0-9_.-]*`, <= 64 chars, unique and stable forever |
| `input` | string | yes | Last user message, 1..4000 chars, no control chars or surrogates |
| `expected_label` | string | yes | One of `fast` / `balanced` / `powerful` |
| `difficulty` | string | yes | `clear` or `ambiguous` |
| `source` | string | no | `synthetic` / `production` / `adversarial` / `manually_created`; defaults to `synthetic` |
| `category` | string | no | Free-form coverage bucket; defaults to `uncategorized` |
| `adversarial` | string[] | no | Tags such as `negation`, `typos`, `prompt_injection` |
| `notes` | string | no | Must not restate the label; reasoning lives in `evals/REVIEW.md` |
| `messages` | object[] | no | Full conversation. Must contain `input` as its last user turn |
| `provenance` | object | no | Required for `source: production`; must carry no user identifiers |

Legacy aliases: `prompt` is accepted for `input`, `expected_tier` for
`expected_label`. Defining both spellings is an error, not a silent preference.
A legacy record with no `difficulty` is defaulted to `ambiguous`, not `clear`:
the field exists because a human judged the case, so inferring it from the label
would be circular, and defaulting to `clear` would let an unmigrated file
inflate the clear-case gate.

## Why `messages` exists

`classify_complexity` scores `len(messages) > 10`, `len(messages) > 20`, and
the presence of a system message. A dataset made only of single-message cases
leaves that scoring branch completely unmeasured, so the golden set includes
multi-turn and system-prompt cases.

## Duplicate policy

* Identical ids: **error**.
* Identical normalised input (NFKC + casefold + whitespace collapse): **error**.
* Token Jaccard >= 0.80: **warning**, recorded in `results.json`.

Near-duplicates are a warning rather than an error because templated prompts
legitimately overlap, and refusing to load the dataset is a worse outcome than
recording the overlap for a human to review. Cross-fold leakage in the learned
baseline is checked separately, in `evals/learned.py`.

## Label leakage policy

Any key whose name matches `label|expected|answer|target|gold|tier` is rejected
unless it is a known schema field, and `notes`/`category` are rejected if they
contain the expected label as a whole word. `fastapi` does not trip this: the
check is word-bounded.

## Adding a case

1. Copy a nearby entry in `evals/golden.json`.
2. Give it a new stable id. Never renumber or reuse an id.
3. Write what the prompt needs, not what the router currently does. Do not tune
   an expected label to match a prediction -- that is fitting the test set.
4. Run `python -m evals.run --report` and confirm the new case is counted, not
   that it passes.