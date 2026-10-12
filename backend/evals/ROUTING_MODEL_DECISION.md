# Should the learned model ship as a versioned JSON artifact?

Status: **decided — no.** The heuristic router ships. This file records why,
with the measurements, because the question is not closed and will be asked
again by anyone who reads `evals/results.json` and sees the TF-IDF number.

Date: 2026-10-12. Commit range: `ed68e0f`..`360da6f`.

---

## The question

`evals/learned.py` fits a TF-IDF + logistic regression baseline. It scores
higher than the router the gateway actually ships. A learned model can be
exported to JSON (vocabulary, idf, coefficients, intercept) and served with
stdlib alone — no sklearn in the runtime image, which `requirements.txt`
currently keeps minimal on purpose.

So: export it and ship it, or document why not?

## Answer: ship the router. Three reasons, in order of weight.

### 1. On comparable input the two systems are indistinguishable

The headline comparison is unfair, and the unfairness was worth 1.7 points of
the 2.8-point gap.

The router reads `case.chat_messages()` — the whole thread. The learned model
reads `case.input` — the last user turn only. `evals/ERROR_ANALYSIS.md`
flags this asymmetry. Restricted to the 365 single-message cases, where both
systems received byte-identical input:

| system | accuracy | |
|---|---|---|
| heuristic router | **0.696** (254/365) | after this branch's fixes |
| TF-IDF out-of-fold | **0.707** (258/365) | |

Discordant pairs: 67 where only the router was right, 71 where only the
learned model was. **McNemar exact p = 0.80.** That is a coin flip.

### 2. Where the learned model wins, the router is not the thing that is broken

Per-tier recall, single-message cases only:

| tier | router | TF-IDF |
|---|---|---|
| fast | **0.794** (112/141) | 0.752 (106/141) |
| balanced | 0.572 (79/138) | **0.783** (108/138) |
| powerful | **0.733** (63/86) | 0.512 (44/86) |

The systems fail in opposite directions. The learned model's entire
advantage is at the middle tier, and it is *worse* than the router at both
ends — including `powerful`, which is the tier where under-routing costs
answer quality.

`evals/ERROR_ANALYSIS.md` reported powerful recall of 0.250 for the router
and 0.467 for the learned model, and called the learned one "less bad". That
was true when the router scored 0.529. The router now scores **0.728** on
that tier. The comparison it was drawn from no longer holds.

### 3. Shipping it would cost the thing that makes any of this measurable

The learned model's out-of-fold score is 0.706; the same model scored on
the data it was fitted on is **0.985**. That 28-point gap is memorisation.

Those 391 cases are the only evaluation set this project has. Fitting on it
and evaluating on it does not just weaken one number — it removes the only
thing that lets a future change be judged. The router's current score is
also now optimistic, because this branch tuned keyword membership against the
same failures; that is a known, bounded, *documented* contamination. A
fitted model makes it total and invisible.

There is also `evals/production/`, which has never been run because there is
no traffic export. That is the dataset that would settle this question, and
it does not exist yet. Shipping a model fitted on the synthetic set before
the real one exists inverts the order of evidence.

---

## What was verified, so this is not a guess

The artifact was built and measured, not reasoned about. Findings:

**It works technically.** Exporting vocabulary + idf + coefficients +
intercept and replaying inference in pure Python reproduces
`sklearn.pipeline.predict()` on **391/391 cases, 100%**. stdlib only
(`json`, `math`, `re`, `unicodedata`). Cost:

| | |
|---|---|
| vocabulary | 3,963 word 1-2 grams |
| raw JSON | 375 KiB |
| gzipped | **78 KiB** |
| inference | **0.011 ms** per prompt (router: 0.047 ms) |
| runtime deps | none beyond the standard library |

Three fidelity bugs were found and fixed while building the replay, each of
which silently changed predictions:

1. The idf vector was never applied, only `1 + log(tf)`.
2. The intercept was divided by the L2 norm along with the dot product.
   sklearn normalises the row, *then* adds the intercept. Dividing a
   negative `powerful` intercept by a norm > 1 shrinks it toward zero and
   inflates that class.
3. `sublinear_tf` was applied to unigrams but not bigrams, and repeated
   n-grams were summed as `1.0` each instead of `1 + log(count)`.

Each was found by a disagreement, not by reading sklearn's source. At 98.2%
agreement the artifact would still have shipped, and would have been wrong
on inputs nothing in this dataset happens to contain. **The artifact
mechanism is sound. It is the decision to use it that fails.**

## What would change the answer

Not more work on the router. Any of these:

1. **`evals/production/` populated with real traffic.** The synthetic set is
   written by the same person who writes the labels; its ceiling is
   agreement-with-judgement. A real traffic sample is the only thing that
   makes 0.706 mean "right model" rather than "right about my own set".
2. **A second labeller**, so inter-rater agreement on the 114 ambiguous
   cases is known. Both systems are measured against one opinion.
3. **A gap that survives McNemar.** +1.1 points at p = 0.80 is not a gap.
   If the learned model reached, say, 0.75 on comparable input while
   keeping powerful recall, the trade would be worth making.
4. **The multi-turn asymmetry resolved.** The learned model gets 18/26 on
   multi-turn and the router 11/26 (p = 0.065), but the router sees the
   thread and the model does not. A learned model that reads the transcript
   is the only version of this idea that is honestly comparable, and it is
   also the version that needs a real dataset first.

## What to do instead

The router's remaining errors are concentrated and legible. Of the 106
under-routes at the start of this branch, the largest surviving clusters are
`code` (9), `architecture` (7) and `vague_request` (6), nearly all of them
single-intent prompts naming exactly one kind of work. That is a keyword
problem, and it is the kind of problem where a human can read the failure,
decide whether the label or the rule is wrong, and fix it. That property is
worth more than 1.1 points at p = 0.80.

The evidence that this pays: 0.529 -> 0.678 overall, powerful recall
0.250 -> 0.728, and under-routes 138 -> 79, each step measured and
committed separately. The learned baseline was never the ceiling; it was a
reference point, exactly as `evals/baselines.py` describes it.

---

## Reproducing the numbers in this file

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python -m evals.run --report          # router vs learned, both, with intervals
```

The artifact replay is not committed — nothing in the repo depends on it,
which is the point. To rebuild it, export `vocabulary_`, `idf_`,
`coef_` and `intercept_` from `evals/learned.py`'s fitted pipeline and
replay `TfidfVectorizer` by hand: lowercase, strip unicode accents, tokenize
on `(?u)\b\w+\b`, count word 1-grams and 2-grams, `tf = 1 + log(count)`,
multiply by idf, L2-normalise the row, then `argmax(x @ coef.T + intercept)`.
All three orderings above matter.
