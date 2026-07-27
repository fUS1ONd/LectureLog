# Slide matcher generalization roadmap

## Goal

Improve slide matching on unseen lectures, not maximize the score of the two lectures
currently used for diagnosis.

There is no finite benchmark that guarantees quality on every lecture. The practical
goal is to:

- cover materially different lecturer/deck behaviours;
- keep development examples separate from honest validation and release holdout data;
- measure failures per lecture and per slide class, not only as one average;
- accept matcher changes only when they improve general quality without introducing
  high-impact regressions.

## Dataset split

Every evaluated lecture belongs to exactly one split.

### Development

Results may be inspected slide by slide. These lectures are used to find root causes,
design matcher changes, and write regression tests.

Initial development set:

- `2026-02-12`;
- `2026-02-26`.

Once a lecture or individual slide has influenced implementation decisions, it remains
in development permanently and must not be presented as unseen evidence.

### Validation

Used after a matcher revision to detect overfitting. Reports may be inspected after the
run, but individual validation errors must not be repeatedly patched without moving the
affected lecture into development and replacing it with new validation data.

Initial candidate:

- `2026-03-12`, kept uninspected until the first matcher revision is complete.

Target size: 6–8 lectures.

### Hidden holdout

Used only before a PR or release decision. Judges and implementers must not read its
previous reports or labels before producing the candidate result.

Target size: 4–6 lectures. Any holdout lecture opened for detailed diagnosis is retired
to development and replaced.

## Coverage matrix

Select lectures by matcher behaviour, not only by academic subject. The benchmark should
eventually contain at least 2–3 independent examples of each high-risk class:

| Behaviour class | Capability under test |
| --- | --- |
| Sequential deck traversal | Basic monotonic matching |
| Lecturer returns to earlier slides | Legitimate backtracking |
| Some slides are skipped | Correct `unmentioned` decisions |
| Slides are discussed out of deck order | Semantics over page-number prior |
| One slide is discussed for a long interval | Best local anchor in a broad range |
| Several slides are discussed rapidly | Resistance to cue/anchor collapse |
| Summary and table slides | Composite evidence and acceptable ranges |
| Title, agenda, divider, and closing slides | Role-aware navigation placement |
| Mostly visual slides | Image-aware matching with little native text |
| Text-heavy slides with generic vocabulary | Resistance to lexical false positives |
| Lecturer paraphrases slide text | Semantic recall without exact wording |
| Slide and speech languages differ | Cross-language matching |
| Poor or corrupted ASR | Robust evidence under transcription noise |
| Irrelevant or wrong deck | Deck mismatch and fail-closed behaviour |

A lecture may cover several classes. A large collection of nearly identical sequential
lectures does not substitute for this coverage.

## Target dataset size

The first useful generalization benchmark should contain:

- 6–8 development lectures;
- 6–8 validation lectures;
- 4–6 hidden holdout lectures;
- at least 300–500 audited slides in total;
- multiple lecturers, subjects, deck sizes, languages, and PDF qualities.

These are initial targets, not a stopping rule. Add data where confidence intervals are
wide or a behaviour class remains underrepresented.

## Ground-truth protocol

Use `skills/lecture-quality-judge` in a fresh subagent context for each candidate result.

The judge must:

1. Complete pass A without opening matcher diagnostics or previous reports.
2. Classify every slide's role and discussion status.
3. Record preferred and acceptable semantic ranges with transcript evidence.
4. Open assignments and placements only in pass B.
5. Evaluate topic, local anchor, evidence strength, confidence, rendering, and collapse.
6. Publish raw numerators, denominators, exclusions, and per-slide labels.

Persist compact labels as benchmark artifacts:

- `discussion_status`;
- `role`;
- `preferred_ranges`;
- `acceptable_ranges`;
- `topic_verdict`;
- `anchor_verdict`;
- `evidence_strength`;
- `regret`;
- `confidence_correct`;
- stable transcript/slide evidence.

Aggregate metrics should be calculated deterministically from these labels. Subjective
scorecards remain useful diagnostics but must not replace per-slide evidence.

## Judge calibration

Before trusting the benchmark, give the same lecture independently to two fresh judges.
Compare agreement on:

- `discussed`, `partially_discussed`, and `unmentioned`;
- `correct`, `reasonable_range`, and `incorrect`;
- best versus acceptable anchor;
- materially better context;
- evidence strength and severity.

Manually resolve disagreements, clarify the rubric, and repeat on a second lecture.
Ambiguous slides remain explicitly ambiguous; do not force a single timestamp merely to
make the dataset easier to score.

## Metrics

Report both:

- micro-average across all slides;
- macro-average giving each lecture equal weight.

Also report:

- every individual lecture;
- worst-lecture result;
- each behaviour class;
- each slide role;
- strict and partial-inclusive discussion metrics where relevant.

Required headline metrics:

- discussed precision and recall;
- unmentioned false-negative rate;
- acceptable and preferred topic accuracy;
- wrong-topic rate;
- best and acceptable context hit;
- materially-better-context rate;
- verified precision;
- high-confidence error rate;
- collapsed-slide rate;
- rendering correctness;
- missing/duplicate markers;
- unsupported inline and appendix false positives.

## Product-weighted error priority

Not all mistakes have equal reader impact. Optimize in this order:

1. Incorrect `verified` inline placement.
2. Unsupported slide marked `discussed`.
3. Discussed slide incorrectly relegated to the appendix.
4. Correct broad topic but misleading local anchor.
5. Safe section-gallery fallback instead of a valid inline anchor.

Do not improve recall by placing weakly supported slides inline. A conservative gallery
fallback is preferable to a confidently wrong paragraph insertion.

## Generalization-safe implementation rule

Each matcher change must be expressible as an input-independent invariant, for example:

- a generic one-token overlap cannot prove an explicit semantic match;
- `verified` requires grounded evidence and meaningful competition/calibration;
- title and closing slides are placed according to document role;
- semantically different slides cannot share one unsupported cue merely because it
  contains a broad deck term;
- a semantic miss may use conservative global recovery based on distinctive evidence;
- slide order is a weak prior, not a hard truth.

A rule mentioning a particular lecture, slide number, lecturer, or expected phrase is a
likely overfit and must not enter production matching logic.

## Release gates

A matcher revision is accepted only when all of the following hold:

- development metrics improve on the targeted failure classes;
- validation macro metrics improve or remain within an explicitly approved tolerance;
- hidden-holdout metrics do not materially regress;
- no important behaviour class or slide role has a hidden systematic regression;
- verified precision does not decrease;
- high-confidence error and unsupported-inline counts do not increase;
- no new missing or duplicate slide markers appear;
- every newly discovered critical or major regression is reported even when aggregate
  metrics improve.

Headline averages alone cannot approve a revision.

## Execution sequence

1. Treat 02-12 and 02-26 as development data and preserve their current baseline.
2. Finish the first matcher revision using only general invariants derived from those
   failures.
3. Run unit and integration tests.
4. Reprocess 02-12 and 02-26 sequentially.
5. Blind-judge both results with fresh skill-based subagents.
6. Compare per-slide and aggregate development metrics.
7. Process and blind-judge 03-12 once as the first unseen validation lecture.
8. If development improves but 03-12 regresses, treat that as evidence of overfitting;
   investigate the general failure class rather than special-casing the lecture.
9. Expand validation using the coverage matrix.
10. Assemble a hidden holdout before PR/release approval.
11. Run a final fresh blind evaluation on the holdout and apply the release gates.

## Data growth policy

Add another lecture when:

- a behaviour class has fewer than two independent examples;
- a regression appears on a new lecturer/deck style;
- judge disagreement reveals an underspecified case;
- per-class metrics are dominated by only one lecture;
- a validation or holdout lecture is retired into development.

Prefer targeted diversity over raw volume. Within a particularly important or unstable
class, add several lectures to estimate variance rather than relying on one exemplary
case.
