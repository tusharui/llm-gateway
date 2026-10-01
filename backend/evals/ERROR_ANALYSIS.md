# Error analysis: the routing classifier

Dataset `backend/evals/golden`, 379 cases, fingerprint
`9923f6760d716aff3e2464078561473527327cdcc70ff20e29841e81fc54d9cd`.
Commit `f4bff76`. Reproduce with:

```bash
cd backend
python -m evals.run --load-baseline evals/baseline_gates.json
python -m evals.run --report
```

Every number below is in `evals/results.json` with a Wilson interval attached.
Failure counts come from `--dump-failures`, and the 23 cases behind
`evals/REVIEW.md` were read individually.

---

## Headline

| system | overall | clear | ambiguous | adversarial |
|---|---|---|---|---|
| heuristic | **0.533** (202/379) [0.483–0.583] | 0.560 (149/266) [0.500–0.619] | 0.469 (53/113) [0.380–0.561] | 0.474 (46/97) [0.378–0.573] |
| tfidf + logistic regression | **0.689** (261/379) [0.640–0.733] | 0.744 (198/266) [0.689–0.793] | 0.558 (63/113) [0.466–0.646] | 0.660 (64/97) [0.561–0.746] |
| majority class (floor) | 0.380 (144/379) [0.333–0.430] | 0.350 | 0.451 | 0.371 |

Per tier, heuristic: `fast` 0.771 (111/144), `balanced` 0.473 (69/146),
`powerful` **0.247** (22/89).

### A note on the learned baseline's number

An earlier version of this document reported 0.715 for the learned baseline.
That figure was wrong. Two near-duplicate case pairs were landing on opposite
sides of a stratified split, so a model had seen a twin of its test case. The
folds are now grouped (`StratifiedGroupKFold`, near-duplicate groups kept
whole) and the same model scores **0.689**. The lower number is the honest one,
and the committed gate moved with it from 0.68 to 0.65.

### Confusion matrix (rows = expected, columns = predicted)

```
              fast  balanced  powerful
fast           111        31         2
balanced        69        69         8
powerful        20        47        22
```

### The one-sentence finding

**The heuristic fails open.** 136 of its 177 errors are under-routes (it sends
genuinely hard work to a cheaper model) and 41 are over-routes. It predicts
`powerful` for only 32 of 379 prompts when 89 need it. Under-routing is the
expensive direction: it costs answer quality silently, while over-routing only
costs money, which the router's own cost tracking can see and quality cannot.

### Does clear beat ambiguous?

Not by a measurable margin. 0.560 versus 0.469, a difference of +9.1 points
with a 95% interval of **−1.8 to +19.8 points, p = 0.104**. Not significant.

This is the most surprising result in the analysis and it is worth being precise
about what it means: the ambiguous subset is not where the heuristic fails
hardest. It fails about equally everywhere. The intuition that a transparent
keyword rule "works on clear cases and breaks on ambiguous ones" is *not*
supported by this dataset. Where it does break down is along the axes below.

The learned baseline drops 18.4 points from clear to ambiguous (0.744 →
0.558), which is a more ordinary pattern, and its ambiguous interval is wide
enough (0.466–0.646) that the drop is not itself significant.

### Does the learned baseline replace the heuristic?

On this data, yes, and the comparison is significant. Restricted to the 365
cases where both systems received **identical input** (see the limits section
for why that restriction exists):

- Paired (McNemar exact, 146 discordant): heuristic wins 45, learned wins 101,
  p < 1e-8.
- Clear cases: learned is 18.4 points better.
- Ambiguous cases: learned is 8.8 points better.
- Per-tier recall for `powerful` is 0.247 heuristic versus 0.427 learned. Both
  are bad; the learned one is less bad.

I am **not** recommending the swap, for reasons that have nothing to do with
this table and everything to do with it: the learned model is trained on 379
cases written by the same person who writes the labels, so its 0.689 measures
agreement with my judgement, not correctness. Its training accuracy is 0.989
against 0.689 out-of-fold, which is memorisation. It is a reference point, not
a candidate.

---

## Failure mode 1: prompt surface is the only signal

**Observed failures: 43 of 177 (24.3%)**
Categories: `keyword_stuffing` 10/10 wrong, `long_distractor` 8/10,
`misleading_keyword` 8/10, `borderline_length` 5/8, `conflicting_signals` 3/10,
`casing` 3/6, `irrelevant_context` 3/8, `contradiction` 3/6.

**Mechanism.** `classify_complexity` computes a score from character count,
word count, the number of regex hits, and three conversation bonuses. Every
input to that score is a property of the *string*. Nothing asks whether the
string contains a request, in what tense, or whether the keywords are the
object of the sentence or its subject.

**Representative cases**

- `adv_stuff_01` — `analyze compare evaluate design architect code implement debug refactor optimize`. Score 21, predicted `powerful`. Ten verbs, no request.
- `adv_stuff_04` — `proof proof proof proof proof proof`. Score 2, predicted `balanced`.
- `adv_mislead_01` — `I read a design document today.` Score 2, predicted `balanced`. Past tense, no request.
- `amb_longdistract_01` — 354 characters of sprint history ending `...what is the capital of Portugal?`. Score 4 from `chars+2` and `words+2` alone. Predicted `balanced`.
- `amb_longdistract_06` — the same trick with `what is two plus two?` at the end.

**Which system failed:** both. Learned gets 4 of the 10 stuffed prompts right,
because a bag of verbs without sentence structure looks unusual to a bag-of-
words model too. Neither approach asks the right question.

**Classification:** heuristic limitation, plus a dataset problem worth fixing.
Six of the ten `keyword_stuffing` cases are keyword lists with no surrounding
sentence, which is a shape a real user almost never produces and which
therefore inflates this mode's share.

**Remediation.** Score *distinct intents* rather than occurrences: cap the
keyword contribution, and weight by whether a verb appears in an imperative
position. Cheapest available step, and it does not need a model: require a
detected keyword to appear in a sentence with a second-person subject or an
imperative mood before it counts. Separately, stop treating raw length as
evidence and treat it as a tiebreaker only.

**Evidence.** Category accuracies above; the recomputed scores for each
representative case in `evals/REVIEW.md`.

---

## Failure mode 2: verbs that mean real work are not in the keyword list

**Observed failures: 25 of 177 (14.1%)**
`howto` 8/8 wrong (0% accuracy), `summarization` 5/6, `troubleshooting` 5/7,
`drafting` 3/3, `advice` 2/9, `rewriting` 2/3.

**Mechanism.** The list at `app/engine/auto_router.py:72` contains 40 patterns.
It has `explain`, `compare`, `recommend` and `proof`. It has no `summarise`, no
`condense`, no `how do I`, no `what should I`, no `troubleshoot`, no `draft`, no
`rewrite`, no `translate`, no `narrow down`, no `profile`, no `check whether`.

**Representative cases**

- `clear_bal_howto_05` — `How do I export a CSV from a Postgres table?`. Score 0. Eight such prompts, eight wrong.
- `clear_bal_summarise_03` — `Summarise the main points of the Declaration of Independence.` Score 1.
- `adv_quoted_02` — `Summarise this quote: "..."`. Score 1.
- `clear_bal_trouble_05` — `Why is my Python script slow to start? How do I profile it?`. Score 1 from the character count; `why does` needs "does", `profile` is not listed.

**Which system failed:** the heuristic, and only the heuristic. The learned
baseline handles all of these, because it learned "summarise" correlates with
`balanced` from the 379 cases. That gap is the clearest single argument
against hand-maintained keyword lists: it is a maintenance bug that has not
happened yet.

**Classification:** heuristic limitation, with a **bug** component (see mode 5
for the spelling half of it).

**Remediation.** Add the missing families. This is the highest
value-per-line change available and it is verifiable in one eval run. The
general lesson is that a keyword list is a liability, and the fix is not a
bigger list but a classifier that does not need one.

**Evidence.** `howto` category accuracy is 0.000 with n=8, and every one of
those prompts scores 0 or 1 on a hand recount.

---

## Failure mode 3: the conversation is ignored

**Observed failures: 10 of 177 (5.6%) — but 10 of the 14 multi-turn cases
(71%), where they are 71% of that subset's error rate.**

**Mechanism.** `classify_complexity` reads only the last user message for its
text signals (`auto_router.py:42-48`). Its only use of earlier turns is
`len(messages) > 10` and `> 20` bonuses and a flat `+1` for any system message.
A three-turn conversation scores the same as a three-word prompt.

**Representative cases**

- `mt_early_keyword_01` — `Please refactor the entire billing service and rewrite it in Rust.` / `Understood. Where would you like to start?` / `ok then`. Score 0, predicted `fast`. The work was scoped in turn 1.
- `mt_early_design_01` — `Design a rate limiter for a public API.` / `Here is one approach using a token bucket.` / `what about distributed?`. Score 0.
- `mt_history_debug_01` — six turns of diagnosing a 500 on `POST /charges`; last turn `It has a trailing comma.` Score 1, from the system-message bonus.

**Which system failed:** the heuristic only, but not for the reason the first
draft of this document gave. The learned baseline reads `case.input` -- the
last user turn -- while the heuristic reads the whole conversation. On these
cases the baseline is wrong too, but on 4 of 14 rather than 10 of 14, because
the last turn sometimes carries enough on its own. That is not a fair contest:
the two systems are answering different questions, and the headline comparison
is now restricted to the 365 single-message cases where the input is identical.
`by_multi_turn` is still not a gated subset.

**Classification:** heuristic limitation, plus a **harness gap**. The dataset
has 14 multi-turn cases out of 379 (3.7%), so this mode is under-represented
relative to real traffic, where most requests are the second or later turn of a
conversation. Worse, the longest conversation in the set is six messages, so
the `len(messages) > 10` and `> 20` branches of the classifier are never
exercised at all. The eval's own 71%-wrong-on-10-cases number is based on a
sample too small to gate.

**Remediation.** Score the whole conversation: take text signals from the last
three user turns, and lower the message-count bonus thresholds from 10/20 to
something the gateway actually sees. Then **add roughly 30 more multi-turn
cases** before trusting any number about this mode.

**Evidence.** The three transcripts in `evals/REVIEW.md`, and the per-case
`n_messages` and `has_system_message` fields in `--dump-failures`.

---

## Failure mode 4: requested output size is invisible

**Observed failures: 4 of 177 (2.3%) — but 4 of the 4 cases that state an
output length.**

**Mechanism.** Every length signal in the score measures the *input*:
`char_count > 500`, `word_count > 100`. A prompt that asks for 1,200 words of
output and occupies 70 characters scores 3 and lands on `balanced`.

**Representative cases**

- `clear_pow_create_05` — `Write a 1,200-word essay arguing whether open source maintainers should be paid.` Predicted `balanced`. This is the longest creative request in the set and it is 71 characters.
- `clear_pow_create_02` — `Write a 600-word short story about a lighthouse keeper who stops seeing ships.` Predicted `balanced`.
- `clear_pow_code_09` — `Refactor this 200-line function into smaller units with tests.` Predicted `balanced`.

**Which system failed:** both. TF-IDF has no notion of output length either, and
it reads "1200-word" as two unremarkable tokens. This mode is a genuine limit of
prompt-text-only classification, not a bug.

**Classification:** limitation shared by both approaches. Taxonomy-adjacent: if
output length is a routing signal, the tier description ought to say so, because
`MODEL_TIERS["powerful"]` currently describes input-shaped work
("analysis, code, creative writing") with no mention of generation length.

**Remediation.** Add an explicit output-length pattern
(`\b\d[\d,]{0,5}\s*[- ]?\s*(word|line|paragraph|page)s?\b`) worth enough points
to reach `powerful` on its own. Cheap, and 4 of 4 currently wrong.

**Evidence.** All three cases in the dataset that contain an explicit output
length are misclassified.

---

## Failure mode 5: pattern bugs, including one that matches ordinary English

**Observed failures: 11 of 177 (6.2%)** — 3 from the `plan` false positive, 8
from missing spellings and vocabulary.

**5a. `\bplan\w*\b` matches "plant" and "planet".** The pattern at
`auto_router.py:80` is `\bplan\w*\b`. The trailing `\w*` admits any word
beginning with "plan": plant, plane, plank, planet, planning (intended),
planners (intended).

- `clear_fast_factual_11` — `Which planet is closest to the Sun?` → `balanced`. Wrong.
- `clear_fast_yesno_02` — `Is the moon a planet?` → `balanced`. Wrong.
- `adv_irrel_01` — `My cat knocked over the plant. Anyway, what is the capital of Peru?` → `balanced`. Wrong.

Every sentence in production containing the word "planet" routes to a bigger
model. This is a bug, not a limitation, and it is the single most clear-cut
item in this document.

**5b. The keyword stems are US-only.** The list has `\banalyz\w*` and
`\boptimiz\w*`, which match `analyze`, `analyzing`, `optimize` and
`optimizing`, and match neither `analyse` nor `optimise`. It also has no
`summari[sz]e\w*`, so "summarise" and "summarize" both miss entirely -- that
one is a missing word, not a spelling variant. Eight misclassifications trace
to this, six of them `Summarise` matching nothing:

`clear_bal_summarise_02`, `clear_bal_summarise_03`, `clear_bal_summarise_06`,
`adv_quoted_02`, `amb_multi_05`, `amb_indirect_09`, `amb_longdistract_07`, and
`amb_multi_08` (`analyse the tone of the original`).

Roughly a third of the golden set's non-US-spelling prompts are written with
`-ise`, and real gateway traffic certainly is not all American English. A list
of stems that silently assumes one locale is a maintenance trap, not just a
missing entry.

**Which system failed:** the heuristic. The learned baseline is
spelling-sensitive in a different way -- it splits `analyse` and `analyze` into
different tokens -- so it will also lose these, but it has enough examples to
generalise from the ones it has seen.

**Classification:** implementation bug. The clearest bugs to fix, and fixing
them is honest engineering rather than score-chasing: 5a is a false positive
that produces wrong answers on correct input.

**Remediation.** Replace `\bplan\w*\b` with `\bplans?\b|\bplanning\b|\bplanned\b`.
Add `summari[sz]e\w*`, `condens\w*`, `translat\w*`, `rewrit\w*|rewrote`,
`draft\w*`, `troubleshoot\w*`, and `analys\w*` / `optimis\w*` alongside the
`-z` stems.

**Evidence.** The three case ids above, verified by direct regex evaluation
rather than inferred from the prediction. The eight spelling cases are listed
in `evals/REVIEW.md`.

---

## Not a failure mode, but worth stating

**The ambiguous subset is not where the classifier breaks.** Clear 0.560
versus ambiguous 0.469 is not a significant difference (p = 0.104, CI spans
zero). The five failure modes above are organised by *mechanism*, and the
mechanisms are roughly orthogonal to whether a human would call the case
ambiguous. Anyone expecting the ambiguous subset to be the headline problem
will be disappointed by the data, which is itself worth knowing.

**Both systems are weak on `powerful`.** 0.247 and 0.427 recall. 67 of 89
genuinely hard prompts are sent to a cheap model by the current router. This is
the finding with the clearest operational consequence and it is not visible in
the single accuracy number that the old harness printed.

**Macro-F1 is reported with its convention stated.** The first version of this
document's tooling dropped classes with an undefined F1 from the macro average,
which inflated the majority-class floor to 0.386 on exactly the class it fails
completely. The convention is now "average over every label, undefined counted
as 0.0", and the floor reads 0.257.

**Over-routing is rarer but real.** 41 errors go the other way, mostly `fast`
prompts scoring 2–4 on length or an incidental keyword. That is the cheap
direction, but 31 `fast` prompts sent to `balanced` is real money at volume.

---

## Taxonomy problems found along the way

Three disagreements in `evals/REVIEW.md` are not model failures and should not
be fixed in the router:

1. **Short instructions with implicit work** (`Fix the login bug.`, `Refactor
   the parser.`). The tier definition cannot decide these. The correct answer
   is a model that clarifies, and a third tier state for "needs clarification"
   would express that better than moving a threshold.
2. **Output-length work** (`Write a 1,200-word essay`). The powerful tier's
   description does not mention generation length, so a case that is only hard
   because of output size has no home in the rubric.
3. **Boundary tier assignment** (the `balanced`/`powerful` line). 47 `powerful`
   cases land on `balanced` and 8 `balanced` cases land on `powerful`. Given
   that `clear_pow_math_04` and `clear_bal_howto_05` differ mainly in
   judgement, a human could reasonably label a handful of the 47 differently.
   I would not spend effort on that without a second labeller.

---

## Limits of this analysis

- **The misclassification sample is not random.** `--dump-failures` writes in
  dataset order, so reading the first 60 would have read only shards 01 and 02.
  The 23 reviewed cases were chosen to span the five mechanisms, which means
  the mode percentages above are those of a targeted sample, not of the whole
  177. The mode *counts* come from the full set by category; only the
  mechanisms were chosen by hand.
- **Only one labeller.** Every expected label here is my judgement. The
  inter-rater agreement on the 113 ambiguous cases is unknown, and until it is
  measured, `min-ambiguous-accuracy` is a gate against a single opinion.
- **Synthetic traffic.** No case came from production. Mode 1 in particular is
  probably over-represented: keyword-stuffing prompts are a thing evaluators
  write, not a thing users send. `evals/production` exists to fix this and has
  never been run, because there is no traffic export wired up yet.
- **`by_multi_turn` is not a subset in the report.** Multi-turn accuracy is
  visible per case but not aggregated, so it is not gated. That should change.
  The set also stops at six messages, so `len(messages) > 10` and `> 20` in the
  classifier are untested.
- **The two systems do not receive the same input.** The heuristic sees
  `case.chat_messages()`, the learned baseline sees `case.input`. On the 365
  single-message cases that is the same string; on the 14 multi-turn cases it is
  not. The headline comparison is restricted to the 365 and the unrestricted
  numbers are reported alongside, flagged. This is a real asymmetry in the
  comparison rather than a modelling choice, and the right long-term fix is a
  learned model that reads a transcript.
- **No cost or latency dimension.** The eval measures whether the right tier was
  chosen, not whether the choice saved anything. A classifier that is right and
  routes everything to the expensive model scores well and is useless.

## Reproducing the numbers in this document

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt

# full report, both baselines, confusion matrices, intervals, comparisons
python -m evals.run

# the same, with the committed gates applied (exit 1 on failure)
python -m evals.run --load-baseline evals/baseline_gates.json

# per-case table with a wrong-answer marker per system
python -m evals.run --report

# every misclassification, in dataset order, for a manual pass
python -m evals.run --dump-failures 200 --failures-out evals/failures.json

# the 20 cases behind this document were read from that dump; see REVIEW.md
```

`evals/results.json` is committed and regenerates byte-identically apart from
`generated_at`. If a number here disagrees with `results.json`, `results.json`
is right and this file is stale.