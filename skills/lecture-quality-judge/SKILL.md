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

1. Inventory sections, note blocks, transcript cues, slides, assignments, and placements.
2. Inspect every slide's native text or image and every assignment/placement.
   For more than 30 slides, work in numbered chunks of 10–15. After each chunk, retain a
   compact row and the strongest evidence; do not postpone the whole report while
   polishing prose.
3. Sample note quality across the whole lecture:
   - beginning, middle, and end;
   - at least eight distributed transcript intervals for a long lecture;
   - every section flagged by deterministic checks;
   - blocks with language changes, suspicious claims, or weak structure.
4. Search the full transcript for every slide marked `unmentioned`.
5. For each discussed slide:
   - identify its central concepts;
   - inspect cited evidence and local transcript context;
   - search for materially better contexts outside the assigned section;
   - classify `correct`, `reasonable_range`, `incorrect`, or `unknown`.
6. Evaluate the dimensions and scoring anchors in
   [rubric.md](references/rubric.md).
7. Return the exact structure in
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
