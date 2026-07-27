---
name: lecture-quality-judge
description: Independently audit generated lecture notes against transcript, attached PDF slides, and slide-alignment artifacts. Use for real-result calibration, regression comparison, release review, suspected misplaced or missing slides, mixed-language blocks, unsupported claims, and subagent judging where every verdict must be traceable to stable artifact evidence.
---

# Lecture Quality Judge

Act as an independent read-only judge. Evaluate the generated artifact, not the
implementation. Do not call external LLMs or APIs and do not modify files.

## Inputs

Require paths to:

- generated result ZIP or extracted result;
- source transcript, preferably the transcript persisted in the result;
- attached PDF/PPTX slides when slide quality is in scope;
- alignment diagnostics when present.

State missing inputs. Mark affected dimensions `unknown`; never infer absent evidence.

## Independence rules

- Do not read prior evaluation reports, expected verdicts, known bugs, or benchmark labels.
- Do not treat slide order as ground truth.
- Do not trust system confidence, scores, section assignments, or placements.
- Compare note, transcript, slide content, and placement independently.
- Distinguish a wrong semantic match from a reasonable alternative anchor.
- Quote only text actually present in an identified source.

## Workflow

Use two passes. Do not open assignment/placement diagnostics during pass A.

### Pass A: independent ground truth

1. Inventory note sections, note blocks, transcript cues, and slide assets. Record source
   hashes when available, but keep matcher assignments and placements closed.
2. Inspect every slide's native text and image. Classify its role and central concepts.
   For more than 30 slides, work in numbered chunks of 10–15. After each chunk, retain a
   compact row and the strongest evidence; do not postpone the whole report while
   polishing prose.
3. Independently label each slide `discussed`, `partially_discussed`, `unmentioned`, or
   `unknown`. Search the full transcript, including paraphrases and visual explanations.
4. For each discussed slide, record preferred and acceptable semantic sections/time
   ranges. Allow multiple correct ranges for summaries and dividers.
5. Sample note quality across the whole lecture:
   - beginning, middle, and end;
   - at least eight distributed transcript intervals for a long lecture;
   - every section flagged by deterministic checks;
   - blocks with language changes, suspicious claims, or weak structure.

### Pass B: audit the matcher

6. Only now open assignments, placements, matcher scores, reason codes, and confidence.
7. For each discussed slide:
   - identify its central concepts;
   - inspect cited evidence and local transcript context;
   - search for materially better contexts outside the assigned section;
   - classify `correct`, `reasonable_range`, `incorrect`, or `unknown`.
8. For every predicted `unmentioned`, compare against pass-A discussion labels.
9. Inspect renderer output separately from semantic assignment.
10. Evaluate the dimensions and scoring anchors in
   [rubric.md](references/rubric.md).
11. Compute every required slide metric from the completed manual slide audit. Include raw
   numerators, denominators, and excluded `unknown` cases.
12. Return the exact structure in
   [report-template.md](references/report-template.md).

If execution is interrupted or budget-limited, immediately return the completed rows,
coverage count, proven blockers, and remaining `unknown` rows. Never lose gathered
evidence in pursuit of a polished narrative.

## Evidence standard

Support every major/critical defect with:

- artifact path;
- stable section/block/cue/slide IDs;
- bounded exact quote or native slide text;
- current placement and the better context when claiming incorrect placement.

Treat a score without evidence as `unknown`. Keep subjective numeric scores separate
from directly proven defects.

## Verdict rules

- A high weighted score cannot override a critical invariant or repeated incorrect
  `verified` placements.
- Use `usable_with_alignment_issues` when note quality is useful but slide placement or
  confidence calibration materially misleads readers.
- Use `evaluation_inconclusive` when required artifacts or representative evidence are
  missing.
- Explicitly state sampling limits and ambiguous slides.

## Handoff

The parent reviewer must verify all critical findings and a sample of major findings
against raw artifacts before accepting the verdict. Provide enough stable evidence for
that verification without reconstructing your hidden reasoning.

The parent must verify:

- every incorrect `verified` placement;
- every false-negative `unmentioned` slide;
- every critical finding;
- at least 20% of claimed-correct slides, selected across the deck;
- all metric arithmetic from the per-slide table.
