# Automated lecture quality evaluation

LectureLog includes an offline evaluator for generated result ZIPs. It does not start the
API, use the production database, or modify a completed task.

## Quick start

Run deterministic artifact, Markdown, language, and slide-alignment checks without a
network connection:

```bash
python -m lecturelog.evaluation evaluate \
  --result /path/to/result.zip \
  --slides /path/to/slides.pdf \
  --profile static \
  --output /tmp/lecture-evaluation
```

Run a small LLM-backed evaluation:

```bash
export OPENROUTER_API_KEY=...
python -m lecturelog.evaluation evaluate \
  --result /path/to/result.zip \
  --slides /path/to/slides.pdf \
  --profile smoke \
  --allow-remote-llm \
  --output /tmp/lecture-evaluation
```

Free OpenRouter providers may retain prompts, slide images, and outputs. Remote evaluation
is therefore disabled unless `--allow-remote-llm` is supplied explicitly.

Use the same output directory and `--resume` after a quota or provider interruption:

```bash
python -m lecturelog.evaluation evaluate \
  --result /path/to/result.zip \
  --slides /path/to/slides.pdf \
  --profile smoke \
  --allow-remote-llm \
  --resume \
  --output /tmp/lecture-evaluation
```

Every judge call has a content-addressed cache. Unchanged inputs, model, prompt, and schema
consume no new completion request.

Before calling OpenRouter, the CLI prints the logical batch plan and the planner's
worst-case physical request count. Exact cache hits require fully rendered prompt keys, so
preflight explicitly leaves them unresolved for the runner instead of promising an
estimate.

## Profiles

| Profile | Request cap | Purpose |
| --- | ---: | --- |
| `static` | 0 | Fast local validation |
| `smoke` | 8 | Prioritized sample during development |
| `standard` | 24 | Broader lecture-quality decision |
| `deep` | 45 | Release-candidate investigation |

`--max-requests` can lower, but never raise, the profile cap. Calls are sequential.

The MVP pins `google/gemma-4-26b-a4b-it:free`, selected after a live
strict-JSON-schema probe. Real Russian lecture runs also exposed intermittent provider
formatting failures, so a failed validation produces `evaluation_inconclusive` rather
than a score. Model availability and zero pricing are checked against the OpenRouter
catalog before a completion request.

For an operator-controlled OpenRouter BYOK route, roles can be overridden with
`EVALUATION_TEXT_MODEL`, `EVALUATION_VISION_MODEL`, and
`EVALUATION_ADJUDICATOR_MODEL`. A catalog-priced model must also be listed in
`EVALUATION_BYOK_MODELS`; this explicit assertion prevents an accidental paid OpenRouter
fallback from silently entering a benchmark. `EVALUATION_MAX_TOKENS` defaults to `4096`
so OpenRouter does not reserve a model's full theoretical completion window during
admission checks.
For reasoning models, set `EVALUATION_REASONING_EFFORT=minimal` to preserve the bounded
completion budget for evidence JSON instead of spending it on hidden judge reasoning.

## Reading the report

The main files are:

- `report.md`: verdict, scorecard, failed gates, and highest-impact findings;
- `evaluation.json`: canonical machine-readable result;
- `manifest.json`: input hashes, prompt/model versions, request and token usage;
- `deterministic-findings.json`: findings produced without an LLM;
- `block-evaluations.json`, `section-evaluations.json`, `slide-evaluations.json`: judge
  drill-down;
- `judge-calls.jsonl`: requested/actual models and cache metadata.
- `judge-attempts.jsonl`: physical attempt provenance, including failures when available.

The overall score never overrides a failed quality gate. For example, a readable,
faithful note with poor slide anchors is reported as `usable_with_alignment_issues`.

`smoke` evaluates a prioritized, stratified sample. Its score is useful for fast feedback
but is not exhaustive, so its verdict is always `sampled_directional`, never a release
`good` or `excellent`. Sample issue rates are shown but do not drive full-document
percentage gates. `standard` and `deep` can produce a release `good` or `excellent` only
when judge stability is explicitly measured; otherwise the verdict is capped at
`usable_with_minor_issues`. With only one calibrated free judge, standard/deep results
therefore remain diagnostic rather than release-grade.

The report includes evaluated-item tables for blocks, sections, and slides. Each row keeps
the stable ID, score or verdict, issues, and a bounded evidence excerpt so a reviewer can
move from the headline verdict to the underlying judgment without opening JSON first.

`manifest.json` distinguishes remote judgments consumed by the report, newly issued
physical requests, and cache hits. Cached judgments retain their requested/actual model
and cache-key provenance; a cache-only resumed run is still reported as remotely judged
even though it issued no new request.

When aggregate budget usage exceeds the detailed attempts returned by the runner, the
manifest records an explicit `failed_unreported` placeholder instead of silently losing
that request. Standard/deep runs cannot receive `good` or `excellent` when the provider
did not report the actual model. The report also surfaces model-normalization and
provenance warnings.
