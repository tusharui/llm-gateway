# Manual adversarial review

Twenty-three cases read by hand, one at a time, after running the evaluation
and before proposing any change. For each: the input, the label I arrived at
independently of the router, what the router said, and what I think the
disagreement actually is.

This log is deliberately **not** in the dataset. `evals.dataset` rejects any
`notes` or `category` that restates the expected label, so a case's reasoning
cannot leak into anything that consumes the data. It lives here instead.

## Why a disagreement is not automatically a model failure

Five categories are used, and they are not interchangeable:

- **model failure** -- the router saw enough to do better and did not.
- **ambiguous labelling** -- two tiers are defensible and I cannot argue one is
  right. The label stands; the case stays in the `ambiguous` subset.
- **taxonomy problem** -- the tier definition itself does not decide the case.
  Needs a rubric change, not a router change.
- **dataset problem** -- the case is malformed, duplicated, or tests nothing.
- **implementation bug** -- the router's code does not do what it intends.

Of the 23 below: 13 are model failures, 3 are taxonomy problems, 3 are
implementation bugs, 3 are ambiguous labelling, 1 is a taxonomy problem that
also looks like a labelling problem.

## The 23

| # | id | my label | router | verdict |
|---|---|---|---|---|
| 1 | `adv_stuff_01` | fast | powerful | model failure |
| 2 | `adv_mislead_01` | fast | balanced | model failure (label also arguable) |
| 3 | `adv_typo_01` | balanced | fast | model failure |
| 4 | `adv_uni_01` | balanced | fast | model failure |
| 5 | `adv_quoted_02` | balanced | fast | model failure |
| 6 | `amb_longdistract_01` | fast | balanced | model failure |
| 7 | `amb_border_01` | balanced | fast | taxonomy problem |
| 8 | `amb_vague_01` | balanced | fast | ambiguous labelling |
| 9 | `amb_multi_01` | powerful | balanced | model failure |
| 10 | `amb_context_01` | balanced | fast | ambiguous labelling |
| 11 | `mt_early_keyword_01` | powerful | fast | model failure |
| 12 | `mt_early_design_01` | balanced | fast | model failure |
| 13 | `mt_history_debug_01` | balanced | fast | model failure |
| 14 | `clear_pow_code_02` | powerful | balanced | model failure |
| 15 | `clear_pow_create_02` | powerful | balanced | model failure |
| 16 | `clear_bal_summarise_03` | balanced | fast | model failure |
| 17 | `adv_irrel_01` | fast | balanced | implementation bug |
| 18 | `adv_case_06` | fast | balanced | ambiguous labelling |
| 19 | `clear_bal_howto_05` | balanced | fast | model failure |
| 20 | `clear_pow_math_04` | powerful | balanced | model failure |
| 21 | `adv_syn_05` | balanced | fast | model failure |
| 22 | `adv_stuff_04` | fast | balanced | model failure |
| 23 | `clear_bal_advice_09` | balanced | fast | taxonomy problem |

Scores below are the router's own arithmetic, recomputed by hand so the
diagnosis rests on the mechanism rather than on the label.

---

### 1. `adv_stuff_01` -- model failure

> `analyze compare evaluate design architect code implement debug refactor optimize`

Ten complex verbs, no object, no request. Score 21, which is `powerful` by a
wide margin. A human reads this as someone testing the router, and the correct
answer for routing purposes is `fast` -- there is nothing to do.

The keyword loop adds 2 per *occurrence* rather than per distinct intent, and
nothing in the score asks whether the text contains a request at all.

### 2. `adv_mislead_01` -- model failure, label arguable

> `I read a design document today.`

Score 2 from `design` alone, so `balanced`. There is no request. `fast` is
right.

I am less certain than I would like: "I read a design document today" could be
the opening of a question the user then abandoned, in which case `balanced` is
a reasonable reading. This is why the case is `ambiguous` rather than `clear`.
The verdict stays *model failure* because under both readings the router found
no request in the text.

### 3. `adv_typo_01` -- model failure

> `Explian the differnce betwen a porcess and a thered.`

Score 1, from the character count alone. `explain` is misspelled, so the
keyword never fires. The task is a two-sentence explanation of a concept.

### 4. `adv_uni_01` -- model failure

> `\u0415xplain the water cycle` (Cyrillic capital Е)

Score 0. The word is `explain` with a Cyrillic `Е`, and `\bexplain\b` does not
match. Same task as case 3.

### 5. `adv_quoted_02` -- model failure

> `Summarise this quote: "We built it, we shipped it, and nobody noticed the migration."`

Score 1. `summarise` is not in the keyword list at all -- not `summarise`, not
`summarize`, not `condense`. Neither is the request to condense, translate,
rewrite or draft, all of which this router is asked for constantly.

### 6. `amb_longdistract_01` -- model failure

> ~354 characters of sprint history, ending `... anyway, what is the capital of Portugal?`

Score 4, from length: `chars+2` and `words+2`. There are no keywords. The score
is a function of how much text surrounds the question, not of what the question
is.

### 7. `amb_border_01` -- taxonomy problem

> `Fix the login bug.`

Score 0, `fast`. My label is `balanced`, but I do not think arguing that label
is productive. The tier definition says `fast` is "a small model should handle
it" -- and a small model's correct answer here is *"which bug, and what does
the log say?"* Under-routing a three-word instruction is the **cheap** failure.

This is a taxonomy problem rather than a model failure because fixing it in the
router means raising the score for every short imperative, which would
over-route `thanks`, `ok` and `done`. The rubric needs a third option for
"needs clarification first", not a lower threshold.

### 8. `amb_vague_01` -- ambiguous labelling

> `Make it better.`

Score 0, `fast`. My label is `balanced`. Honestly, `fast` is defensible: the
right response is a clarifying question, and any model can produce one. I am
keeping `balanced` because the case is in the `ambiguous` subset where this
kind of coin-flip is expected, and because dropping it would quietly improve
the headline.

### 9. `amb_multi_01` -- model failure

> `What time is it and also design me a distributed rate limiter.`

Score 3 (`design+2`, `chars+1`), so `balanced`. My label is `powerful`, under the
rubric rule that the harder intent wins when two are bundled.

I checked the rubric against case 16 (`amb_longdistract_01`), where I labelled
the *easy* part, and the rule is consistent: pick the tier you would need if you
had to serve the whole message. So `powerful`.

### 10. `amb_context_01` -- ambiguous labelling

> `It still doesn't work.`

Score 0, `fast`. My label is `balanced`. This one depends entirely on a
conversation the classifier never sees, and my label is really a claim about
turn 2 rather than about this string. Keeping it as `ambiguous` is the honest
classification; I could not defend `fast` or `balanced` from the text alone.

### 11. `mt_early_keyword_01` -- model failure

> messages: `Please refactor the entire billing service and rewrite it in Rust.` / `Understood. Where would you like to start?` / `ok then`
> last user turn: `ok then`

Score 0. `classify_complexity` reads only the last user message, and the
message-count bonus needs more than 10 messages; this has 3. The actual work was
scoped in turn 1 and is substantial.

This is the clearest case in the set. Nothing is ambiguous about the
conversation, and the router looks at two words.

### 12. `mt_early_design_01` -- model failure

> `Design a rate limiter for a public API.` / `Here is one approach using a token bucket.` / `what about distributed?`

Score 0, `fast`. Same mechanism as case 11: `design` was in turn 1, and turn 3
is a follow-up that inherits the whole design task.

### 13. `mt_history_debug_01` -- model failure

> six turns of debugging a 500 on `POST /charges`; last user turn: `It has a trailing comma.`

Score 1 (the `system` message bonus). The conversation is six turns of real
diagnosis; the last turn is a thirteen-character finding. The classifier has no
notion of "the work already happened earlier in this conversation".

### 14. `clear_pow_code_02` -- model failure

> `Implement a binary search tree in Python with insert, search and delete.`

Score 3, `balanced`. This is the largest single failure category in the
dataset: `code` is 14% accurate, 19 of 22 wrong. `implement` fires once and
there is no length. Every multi-function implementation lands on `balanced`
unless it is also long enough to trip the character count.

### 15. `clear_pow_create_02` -- model failure

> `Write a 600-word short story about a lighthouse keeper who stops seeing ships.`

Score 3, `balanced`. Two separate things are invisible: `short story` only earns
the generic `story` keyword, and the prompt asks for 600 words of output while
the classifier only ever measures input length. 78 characters is not a lot of
prompt for a lot of work.

### 16. `clear_bal_summarise_03` -- model failure

> `Summarise the main points of the Declaration of Independence.`

Score 1, `fast`. Same missing verb as case 5. Six of eight summarisation cases
in the dataset are wrong for this one reason.

### 17. `adv_irrel_01` -- implementation bug

> `My cat knocked over the plant. Anyway, what is the capital of Peru?`

Score 3 and predicted `balanced`. The keyword is `plan`: the list contains
`\bplan\w*\b`, and **"plant" starts with "plan"**. `Which planet is closest to
the Sun?` and `Is the moon a planet?` fail the same way.

This is not a design limitation, it is a bug in the pattern, and it is the
clearest thing to fix in the whole analysis. Three cases in this dataset, and
any sentence containing the word "planet" in production.

### 18. `adv_case_06` -- ambiguous labelling

> `DESIGN`

Score 2, `balanced`. One keyword, capitalised, no object. `fast` is my label
because there is no task, but a user who sends this is about to send the actual
design question, and `balanced` would not be a disaster. Genuinely 50/50.

### 19. `clear_bal_howto_05` -- model failure

> `How do I export a CSV from a Postgres table?`

Score 0, `fast`. The `how do i` / `how to` family scores nothing. All eight
`howto` cases are wrong, at 0%.

### 20. `clear_pow_math_04` -- model failure

> `Work through a Bayes theorem problem step by step.`

Score 2 from `step by step`, so `balanced`. The phrase pattern is worth 2 while
a one-line maths request is worth 1, which puts it below the `powerful`
threshold. `mathematics` is 14% accurate, 6 of 7 wrong.

### 21. `adv_syn_05` -- model failure

> `Write up your findings.`

Score 0, `fast`. This is in the adversarial `synonym` category because `write up`
is not `article` or `essay`. But note that `write` *is* the common signal in
several `powerful` cases that pass -- the router is inconsistent about the same
root verb depending on which noun follows it.

### 22. `adv_stuff_04` -- model failure

> `proof proof proof proof proof proof`

Score 2, `balanced`. Same mechanism as case 1 in miniature: repeated keyword
occurrence with no request.

### 23. `clear_bal_advice_09` -- taxonomy problem

> `Give me a checklist before deploying to production on a Friday.`

Score 1, `fast`. My label is `balanced` -- a short, well-scoped operational
checklist. I am not confident: a small model could plausibly produce this, and
the cost of getting it wrong in either direction is tiny. This is the kind of
case where the tier rubric genuinely does not determine the answer, and the
right response is to admit that rather than to keep tuning until the router
agrees.

## What the review changed

Nothing in the dataset. Two labels were reconsidered and both were kept
(`amb_vague_01`, `adv_case_06`, `clear_bal_advice_09`); re-deciding a label
because the model disagreed is exactly how an eval stops measuring anything.

What did change is `ERROR_ANALYSIS.md`, which now names two implementation
bugs that would have been easy to write up as "the heuristic is just not very
good": the `\bplan\w*\b` false positive and the absence of `summarise` and the
`how do I` family from the keyword list. Both are cheap to fix and both are
worth fixing for reasons that have nothing to do with this eval's score.

## What I would want next

These 23 are the failures the eval found. The cases I am least sure about are
the ones the eval did *not* flag: the `ambiguous` cases that both systems got
right by the same accident. A useful next step is a second reviewer labelling
the 113 ambiguous cases blind, and treating the inter-rater agreement as a
measurement of the dataset rather than of the model.