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

## Pass-A ground-truth rules

Determine these labels before reading matcher output.

### Discussion status

- `discussed`: central content is explicitly explained or clearly paraphrased.
- `partially_discussed`: only a meaningful subset is explained.
- `unmentioned`: no material content is explained after a global transcript search.
- `unknown`: transcript/slide extraction is insufficient.

A repeated generic deck term is not proof that a slide was discussed. A distinctive
entity, formula, diagram explanation, or combination of central concepts can be proof.

### Slide roles

Use one of:

- `title`;
- `agenda`;
- `section_divider`;
- `content`;
- `summary`;
- `reference_or_table`;
- `visual_example`;
- `closing`;
- `appendix`;
- `blank`;
- `unknown`.

Role affects placement:

- title: beginning of the covered lecture/span;
- agenda: near the beginning, not necessarily at every mentioned bullet;
- divider: before its covered semantic range;
- summary: any strong range covering its combined concepts;
- content: near its direct explanation;
- reference/table: section gallery is acceptable when no single row is narrated;
- visual example: require image-aware checking;
- closing: end of the covered span;
- appendix/blank: omission from the main note is normally correct.

### Evidence strength

For each placement classify:

- `direct`: distinctive title/concept/formula/visual relationship is explicitly supported;
- `composite`: multiple cues together cover the slide;
- `broad_topic_only`: same general subject without central slide content;
- `unrelated`;
- `unknown`.

`verified` requires `direct` or strong `composite` evidence. `broad_topic_only` cannot
justify an inline verified placement.

## Mandatory alignment checks

- Inspect every `verified` assignment with low raw score or weak evidence.
- Flag incorrect `verified` placements as confidence-calibration failures.
- Globally search every `unmentioned` slide for explicit and paraphrased discussion.
- Detect many slides collapsing onto one cue, block, or section.
- Separate assignment correctness from renderer placement correctness.
- Allow a semantic range for summary slides instead of inventing a single exact anchor.

## Required slide metrics

Build the metrics from the completed per-slide audit, not from matcher scores. Exclude
`unknown` labels from denominators and publish each denominator.

### Discussion detection

- `discussed_precision = true_discussed_predictions / all_discussed_predictions`
- `discussed_recall = true_discussed_predictions / all_actually_discussed`
- `unmentioned_false_negative_rate = discussed_but_predicted_unmentioned /
  all_actually_discussed`

Count `partially_discussed` separately and state whether it is treated as positive for a
specific calculation.

### Semantic placement

- `acceptable_topic_accuracy`: current section belongs to the manually accepted semantic
  range.
- `preferred_topic_accuracy`: current section is among the strongest contexts.
- `wrong_topic_rate`: placement is demonstrably outside any reasonable semantic range.

Do not use numeric section distance as semantic distance. Non-adjacent sections may be
equivalent; adjacent sections may be unrelated.

### Local anchor

- `best_context_hit`: current anchor is the strongest found context.
- `acceptable_context_hit`: current anchor is within a reasonable explanatory range.
- `materially_better_context_rate`: a clearly stronger context exists elsewhere.

Record categorical anchor regret:

- `none`: no materially better context;
- `small`: better wording exists in the same local explanation;
- `major`: current evidence is weak/unrelated and a direct explanation exists elsewhere.

### Confidence

- `verified_precision = correct_verified / all_verified`
- `high_confidence_error_rate = incorrect_verified_or_probable /
  all_verified_or_probable`
- `unresolved_precision = truly_unsupported_unresolved / all_unresolved`

An incorrect `verified` placement is always at least a major finding.

### Collapse and rendering

- `collapsed_slide_rate`: slides sharing an implausible cue/block/section collapse divided
  by all audited slides;
- maximum slides per evidence cue and per rendered anchor;
- duplicate marker count;
- missing marker/image count;
- appendix false-positive count;
- assignment-correct but rendering-wrong count.

Collapse is a defect only when the grouped slides are semantically different or their
individual evidence is unsupported.

### Role-aware correctness

Report semantic and rendering accuracy separately for:

- title/divider/closing;
- ordinary content;
- summary/reference/table;
- visual examples;
- appendix/blank.

Do not let many easy content slides hide systematic failure on navigation or visual
slides.

## Severity

- `critical`: corrupt/missing output or a result that cannot be safely used.
- `major`: materially misleading content/placement, including incorrect `verified`
  assignment or discussed slide incorrectly relegated to appendix.
- `warning`: localized weakness with limited reader impact.
- `info`: diagnostic observation without a quality failure.

## Comparison protocol

For before/after matcher comparisons:

1. Judge each result in a fresh context without reading the other report.
2. Use the same source audio/transcript and slide document hashes.
3. Complete both per-slide audits before comparing aggregate metrics.
4. Compare only common, non-unknown denominators.
5. Report regressions even when the headline verdict improves.
6. Treat changed note text as a potential confounder for paragraph placement and disclose
   it.

## Verdicts

- `excellent`: release-grade across all dimensions with strong evidence.
- `good`: useful and trustworthy with minor defects.
- `usable_with_minor_issues`: useful; defects are localized and not materially misleading.
- `usable_with_alignment_issues`: note is useful, but slide placement/confidence is
  materially misleading.
- `poor`: major content or structural defects make the result unreliable.
- `evaluation_inconclusive`: evidence is insufficient or required checks could not run.
