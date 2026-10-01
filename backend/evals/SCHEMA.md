"""Golden dataset schema reference.

Two files, two shapes, one schema after loading.

## What the labels are, and what they are not

Read this before labelling anything.

**Every `expected_label` in this dataset is one person's judgement.** There is
no external ground truth: no production traffic, no reference answers, no
second labeller. A score out of 391 therefore means "how often the router
agrees with me", not "how often the router is correct". The tier rubric below is
the whole basis, and it is short enough to argue with.

**Labels are not fitted to predictions.** If they were, 391/391 would be the
result. 184 cases disagree, in both directions, and three contested labels were
re-examined after seeing a prediction and kept anyway (`evals/REVIEW.md`).

---

## Tier rubric

Anchored on the tier descriptions already in `app/engine/auto_router.py` and on
what each tier actually serves:

| tier | description in code | cost/1k | serves |
|---|---|---|---|
| `fast` | "Simple, factual, or short queries" | $0.0001 | gpt-oss-20b, flash-lite |
| `balanced` | "Moderate complexity, general purpose" | $0.0005 | qwen-27b, gpt-4o-mini |
| `powerful` | "Complex reasoning, analysis, code, creative writing" | $0.002 | gpt-oss-120b, llama-70b |

"Powerful" means a 70B-class model is **required**, not that the prompt is long.

### `fast` — a 20B model answers this completely

- greeting, acknowledgement, thanks, small talk
- single-fact lookup (capital of X, who wrote Y, when did Z)
- one-line definition or glossary term
- single-step arithmetic or unit conversion
- one-word translation lookup
- yes/no factual question
- status check on the user's own data ("is my order shipped")

### `balanced` — general purpose, 27B-class

- explaining one concept to a non-expert
- comparing two options on stated criteria
- summarising text the user supplied
- troubleshooting with a symptom but no codebase
- step-by-step how-to
- advice, tips, checklists
- rewriting or drafting short text
- translating a sentence

### `powerful` — 70B-class required

- implementing a function, class or module
- architecture and data-model design
- formal proof or multi-step derivation
- multi-constraint analysis ending in a recommendation
- long-form generation (explicit word, line or page counts)
- debugging where the root cause is not obvious from the symptom
- anything where being wrong is expensive

### Cross-cutting rules

These decide cases the three lists above do not.

1. **Output size beats input size.** "Write a 600-word story" is `powerful`
   even though the prompt is 78 characters. Input length is not evidence.
2. **Two intents bundled: take the harder one.** "What time is it and also
   design me a rate limiter" is `powerful`. Applied consistently, including to
   `amb_longdistract_01`, where the easy part is the question being asked.
3. **A statement is not a request.** "I read a design document today" is `fast`
   however many keywords it contains. Keywords do not imply a task.
4. **Repetition is not emphasis.** A keyword repeated six times is worth what it
   is worth once.
5. **The conversation is the request.** "ok" after a scoped refactor inherits
   the refactor. Label the request, not the last message.
6. **Typos and synonyms do not move the tier.** The task is the task.
7. **Length is a tiebreaker, never a signal.** A 350-character preamble ending
   in "what is the capital of Portugal?" is `fast`.

### Where the rubric does not decide

Three known gaps, all recorded in `ERROR_ANALYSIS.md` as taxonomy problems
rather than model failures:

- **Short imperative with implicit work.** "Fix the login bug." has no
  principled answer. The correct response is a clarifying question, which any
  model can produce. Labelled `balanced` on the grounds that under-routing a
  real task is the costlier error, but `fast` is defensible.
- **Output-length-only work.** The `powerful` description does not mention
  generation length, so a case that is hard only because of output size has no
  home until the description is extended.
- **The `balanced`/`powerful` line.** "Work through a Bayes theorem problem step
  by step" is 50 characters and one phrase hit. `powerful` by content,
  `balanced` by every signal the code looks at. This line needs a second
  opinion, not more tuning.

### `clear` vs `ambiguous`

`clear` means two competent reviewers would land on the same tier.
`ambiguous` means I could not argue it, including cases where I kept my label
despite the router disagreeing. 114 of 391 are `ambiguous`.

These are kept as separate subsets precisely so that a disagreement on one
cannot be averaged away by the other.

---

## v2 file (current, `evals/golden/`)

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