# Lecture evaluation rubric

## Dimensions

Score 0–100 only with representative evidence. Otherwise use `unknown`.

| Dimension | What to verify |
| --- | --- |
| Faithfulness | Claims agree with transcript; corrections of ASR preserve meaning; no invented facts |
| Content coverage | Major topics and the lecture tail are represented; no material temporal gaps |
| Block quality | Blocks are complete, readable, locally coherent, informative, and not needlessly repetitive |
| Document structure | Headings reflect content; sections progress coherently; tangents do not dominate |
| Language consistency | Prose follows the lecture/user language; foreign terms and proper names are not false positives |
| Slide semantic relevance | Slide content belongs to the surrounding note topic |
| Slide anchor precision | Slide is placed at a strong local explanatory context, not merely somewhere in the broad topic |
| Confidence calibration | `verified/probable/fallback/unresolved` agrees with observed evidence strength |

## Score anchors

- `90–100`: consistently strong; only negligible defects in the inspected scope.
- `75–89`: useful and reliable; limited local defects.
- `60–74`: mixed; noticeable defects reduce trust or usability.
- `40–59`: major systematic weakness.
- `0–39`: unreliable or actively misleading.

Do not claim precision beyond the sampling method. Prefer a range or `unknown` when
coverage is weak.

## Slide classification

- `correct`: central slide concepts are explicitly supported near the placement and no
  materially better context was found.
- `reasonable_range`: the slide summarizes a span or divider topic with multiple valid
  anchors.
- `incorrect`: cited context is unrelated/weak and a materially better context exists, or
  placement misleads document navigation.
- `unknown`: transcript/slide evidence is insufficient.

Title/divider slides may require document-role reasoning, not lexical matching. A title
slide normally belongs at the beginning of its covered span.

## Mandatory alignment checks

- Inspect every `verified` assignment with low raw score or weak evidence.
- Flag incorrect `verified` placements as confidence-calibration failures.
- Globally search every `unmentioned` slide for explicit and paraphrased discussion.
- Detect many slides collapsing onto one cue, block, or section.
- Separate assignment correctness from renderer placement correctness.
- Allow a semantic range for summary slides instead of inventing a single exact anchor.

## Verdicts

- `excellent`: release-grade across all dimensions with strong evidence.
- `good`: useful and trustworthy with minor defects.
- `usable_with_minor_issues`: useful; defects are localized and not materially misleading.
- `usable_with_alignment_issues`: note is useful, but slide placement/confidence is
  materially misleading.
- `poor`: major content or structural defects make the result unreliable.
- `evaluation_inconclusive`: evidence is insufficient or required checks could not run.
