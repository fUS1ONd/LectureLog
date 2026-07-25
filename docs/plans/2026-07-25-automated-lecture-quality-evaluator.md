# Automated lecture quality evaluator

Date: 2026-07-25

## Product objective

Build a convenient, reproducible offline evaluation product for a single LectureLog
result. It must answer whether a generated note is faithful, complete, coherent,
linguistically consistent, structurally valid, and whether attached slides are placed
where they are useful and semantically correct.

Legacy output is not required. Historical v2 results may be used later as regression
baselines, but every run must receive an absolute self-contained evaluation.

The evaluator is a developer/QA tool, not part of the task-processing HTTP API. No
production API, database, task, or result contract changes are required.

## User workflow

The primary command evaluates one result ZIP:

```bash
python -m lecturelog.evaluation \
  evaluate \
  --result result.zip \
  --slides slides.pdf \
  --profile standard \
  --output evaluation/2026-02-26
```

The ZIP supplies `конспект.md`, `structure.json`, `transcript.srt`, slide images, and
optional `document-slide-alignment.json`. `--slides` supplies authoritative PDF text and
page count when document slides were used.

Before making an LLM request, the command prints:

- selected profile and judge models;
- estimated request count and configured daily budget;
- whether images will be uploaded;
- a privacy warning for free endpoints;
- cache hits and remaining requests.

LLM evaluation requires explicit `--allow-remote-llm`. Static evaluation works without
network access or an API key.

Secondary workflows:

```bash
# Fast local validation, no LLM
python -m lecturelog.evaluation evaluate --result result.zip --slides slides.pdf \
  --profile static --output evaluation/run

# Resume an interrupted run using the content-addressed cache
python -m lecturelog.evaluation evaluate ... --resume

# Evaluate a real-case suite
python -m lecturelog.evaluation suite \
  --manifest benchmarks/suites/real-lectures.yml \
  --profile standard --allow-remote-llm

# Compare two v2 runs only after both have absolute reports
python -m lecturelog.evaluation compare evaluation/baseline evaluation/candidate
```

## Output

Each run produces:

```text
evaluation/
  evaluation.json
  report.md
  manifest.json
  deterministic-findings.json
  block-evaluations.json
  section-evaluations.json
  slide-evaluations.json
  judge-calls.jsonl
  cache/
```

`manifest.json` records source hashes, evaluator commit, schema/prompt versions, models
requested and actually returned by OpenRouter, profile, request budget, timestamps, and
incomplete/unstable status. Reports never hide missing judge calls.

`evaluation.json` is the canonical machine-readable artifact. `report.md` is the primary
human interface and starts with:

- verdict and confidence;
- scorecard;
- critical errors and quality gates;
- ten highest-impact findings;
- model/request/token usage;
- evaluator stability and limitations;
- drill-down tables for sections, blocks, and slides.

## Profiles and free-request budget

OpenRouter currently documents 50 free-model requests/day for accounts without purchased
credits. Free availability changes, so capabilities are discovered at runtime and the
actual model is recorded.

Profiles:

| Profile | Remote calls | Intended use |
| --- | ---: | --- |
| `static` | 0 | CI, artifact and language sanity |
| `smoke` | <= 8 | every implementation iteration |
| `standard` | target 12-20, hard cap 24 | full lecture decision |
| `deep` | target <= 36, hard cap 45 | release candidate and adjudication |

The planner batches 6-10 blocks or 4-6 slides per call. It performs deterministic and
retrieval work first, then spends judge calls on representative and suspicious cases.
Every call is content-addressed and atomically cached. A rerun with unchanged inputs,
prompt, model, and schema uses zero requests.

The runner refuses to exceed `--max-requests`, never launches remote calls concurrently by
default, persists after every call, retries only transient failures, and leaves an
`incomplete` but inspectable report when quota is exhausted.

## Judge model policy

The calibrated MVP pins `google/gemma-4-26b-a4b-it:free` for text, vision, and
adjudication roles. A live strict-schema probe succeeded for this endpoint, while real
smoke runs showed that the currently available Nemotron endpoint returned structurally
valid but empty template scores and Gemma 4 31B ignored the requested JSON schema.

Using one model for every role is an explicit MVP limitation: adjudication is not treated
as independent evidence, and judge stability stays unknown until a second free endpoint
passes the same calibration suite. Gemma 4 26B supports image input and structured output;
images are still sent only when native PDF text is weak or the case is visually dependent.

`openrouter/free` is an opt-in emergency fallback, never the default, because random model
selection makes benchmarks non-reproducible.

Runtime model discovery validates zero prompt/completion price, required modalities,
context length, and response-format support. If a pinned model disappears, the run stops
with an actionable error unless the user explicitly allows a configured fallback.

Free providers can log prompts and outputs. The evaluator must display this before remote
evaluation and must not silently upload lectures. Reports record that remote free-model
processing was used.

## Evaluation architecture

### 1. Artifact loader

Load and cross-link:

- Markdown note;
- structure tree and section timing;
- SRT blocks with stable identifiers;
- slide PDF text and rendered/exported pages;
- v2 assignments and placements;
- result files and marker references.

Normalize them into immutable evaluation packets. Reject unsafe ZIP paths and report
missing optional artifacts without crashing static evaluation.

### 2. Deterministic checks

Check without LLM:

- Markdown markers and referenced files;
- PDF/exported/diagnostic slide counts;
- assignment, placement, section, block and timestamp consistency;
- duplicate/missing slides and marker/structure drift;
- invalid Markdown fences, empty headings/sections, repeated headings;
- slide concentration and timeline anomalies;
- diagnostic confidence contradictions;
- exact and near-duplicate content blocks;
- language distribution and isolated language switches.

Language detection must ignore code, URLs, identifiers, formulas, proper product names,
and short fragments. A Russian paragraph containing English technical terms is not an
English block. Findings distinguish unexpected full-block language, mixed-language prose,
heading/body mismatch, and legitimate terminology.

### 3. Block judge

Evaluate batches of blocks against their source transcript evidence:

- faithfulness;
- completeness of the local thought;
- clarity;
- local coherence;
- heading relevance;
- information value;
- redundancy;
- style and language consistency.

Each issue requires severity, stable code, block ID, and evidence quote/block ID. High
scores are invalid without evidence. Code, formula, quote, and metadata blocks use
type-specific rubrics.

### 4. Coverage and section judge

Build a compact evidence-grounded topic inventory from transcript windows, then judge
whether major topics are covered, shallow, misplaced, distorted, or omitted. Judge the
section outline, title/content agreement, hierarchy, narrative flow, and fragmentation.

### 5. Slide judge

For each slide, assemble:

- slide number, native text and optional image;
- actual placement context;
- source transcript window around the anchor;
- neighboring slide summaries;
- v2 confidence/reason;
- top alternative contexts from local retrieval;
- hard semantic decoy and random negative.

Judge topic relevance, slide specificity, transcript evidence, anchor precision, reader
utility, and whether the slide should be omitted. Rank blinded candidate contexts. Page
order is a weak prior only: reordering is valid when supported by stronger semantic
evidence.

### 6. Global judge

Consume compact deterministic and local results, not the full lecture again. Detect
systemic failures: tail collapse, unjustified reorderings, missing thematic groups,
repeated weak anchors, inconsistent confidence, language/style islands, and a note that is
locally plausible but globally incoherent.

### 7. Stability and adjudication

Critical and low-margin decisions are repeated with candidate order reversed. A decision
that changes is `unstable`. The adjudicator sees evidence and the two conflicting
structured decisions, not model identities. `uncertain` is a valid outcome and reduces
confidence rather than being forced into pass/fail.

## Scoring

Top-level dimensions:

| Dimension | Weight |
| --- | ---: |
| Faithfulness | 20% |
| Content coverage | 12% |
| Block quality | 18% |
| Document structure | 10% |
| Slide semantic relevance | 17% |
| Slide anchor precision | 13% |
| Confidence calibration | 10% |

Block quality:

| Component | Weight |
| --- | ---: |
| Language consistency | 25% |
| Clarity | 20% |
| Local coherence | 20% |
| Heading relevance | 15% |
| Information value | 10% |
| Style consistency | 5% |
| Formatting | 5% |

Scores are 0-100, but verdicts are gate-aware:

- `excellent`
- `good`
- `usable_with_minor_issues`
- `usable_with_alignment_issues`
- `usable_with_major_consistency_issues`
- `poor`
- `invalid`
- `evaluation_inconclusive`

Initial blocking gates:

- broken output invariant: zero allowed;
- transcript contradiction: zero critical allowed;
- unsupported critical claim: at most one;
- unexpected full-language prose blocks: <= 1%;
- critical incomplete blocks: zero;
- incorrect slide placements: <= 10%;
- `verified` but incorrect slide placements: <= 3%;
- judge stability: >= 0.80 for a confident release verdict.

Until calibrated on several real lectures, thresholds are reported as provisional and do
not block CI.

## Prompt and schema rules

- Prompts live in versioned files under `lecturelog/evaluation/prompts/`.
- All remote responses use strict Pydantic models and JSON response format.
- Prompts use Russian rubric explanations but accept multilingual lecture content.
- Judges receive no implementation name, model configuration, legacy/v2 label, or expected
  verdict.
- Evidence references use stable IDs; quotes are bounded.
- Prompts explicitly distinguish absence of evidence from evidence of absence.
- Scores are aggregated locally; a judge never calculates the overall product score.

## Implementation boundaries

New production package:

```text
lecturelog/evaluation/
  __main__.py
  cli.py
  models.py
  artifacts.py
  deterministic.py
  language.py
  retrieval.py
  planner.py
  openrouter.py
  judges.py
  aggregation.py
  reporting.py
  prompts/
```

Tests:

```text
tests/unit/evaluation/
tests/integration/test_evaluation_cli.py
```

Real inputs and generated evaluation outputs stay under ignored `test-data/` and are never
committed. Unit tests use small synthetic ZIP/PDF/SRT fixtures.

## Delivery phases

### Phase 1: useful local product

- artifact loading and static findings;
- robust block parsing and language consistency;
- schemas, aggregation, Markdown report;
- CLI with `static` profile;
- synthetic tests.

### Phase 2: free-model judging

- request planner and atomic cache;
- runtime free-model capability validation;
- block/section/slide batch judges;
- request budget, resume, privacy opt-in;
- smoke and standard profiles.

### Phase 3: trustworthy verdict

- blinded alternatives and semantic decoys;
- reversed-order stability checks;
- adjudication;
- calibration and gate-aware verdict;
- run on `02-12`, `02-26`, and `03-12`.

### Phase 4: regression workflow

- suite manifests;
- compare absolute reports from two v2 revisions;
- Markdown summary suitable for PR review;
- optional non-blocking CI static evaluation.

## Verification

- unit tests for unsafe/missing ZIP members, Markdown parsing, language exclusions,
  deterministic findings, scoring and gates;
- mocked OpenRouter tests for schema errors, 429, unavailable models, cache/resume,
  request caps, actual-model recording and incomplete reports;
- prompt contract fixtures with stable expected issue codes;
- static profile over every real result ZIP;
- smoke judge run on selected blocks/slides;
- full standard evaluation on at least two real v2 results;
- rerun must consume zero remote requests from cache;
- `ruff`, full `pytest`, `git diff --check`;
- independent subagent review before commit.

## Product success criteria

- one documented command produces an understandable report;
- static mode is useful without secrets or network;
- interrupted/quota-limited runs resume safely;
- a complete standard lecture stays below 24 remote calls;
- reports distinguish facts, judge opinions, instability, and missing evidence;
- every score can be drilled down to blocks/slides and source evidence;
- free-model churn cannot silently change the judge;
- no production API or database mutation is involved.

## Real smoke calibration

An early pre-hardening smoke run on the `2026-02-26` v2 result:

- inspected 310 blocks, 30 sections, and 36 slides locally;
- judged a prioritized sample of 8 blocks, 4 sections, and 2 suspicious slides;
- produced an overall sample score of 85/100;
- reported slide anchor precision of 40/100;
- detected five major deterministic alignment anomalies;
- returned `usable_with_alignment_issues` rather than allowing the weighted score to hide
  the failed alignment gates;
- reused four cached calls on the next identical run and spent zero new requests.

That 85/100 result is historical calibration evidence, not an authoritative benchmark:
later evidence-provenance and blind-ranking hardening changed the prompt/schema cache
identity. Hardened reruns correctly became `evaluation_inconclusive` when the free
provider returned malformed structured output instead of fabricating or partially
aggregating a score.

Smoke scores are directional, not exhaustive. Static findings are currently dependable;
remote `standard`/`deep` reports remain diagnostic until a stable independent second free
judge passes calibration. A release-grade verdict requires that independence plus
measured judge stability.
