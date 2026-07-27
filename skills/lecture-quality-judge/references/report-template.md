# Independent lecture quality report

## Scope and inventory

- Inputs inspected:
- Source hashes:
- Sections / blocks / transcript cues / slides:
- Missing artifacts:

## Sampling method

Describe intervals, blocks, all-slide coverage, searches, and exclusions.

Confirm that pass-A labels were completed before matcher diagnostics were opened.

## Pass-A ground truth

| Slide | Role | Discussion status | Central concepts | Preferred context | Acceptable range | Evidence |
| ---: | --- | --- | --- | --- | --- | --- |

This table must not contain matcher confidence or assignment scores.

## Scorecard

| Dimension | Score or unknown | Confidence | Evidence summary |
| --- | ---: | --- | --- |

Do not calculate an overall arithmetic score unless explicitly requested.

## Critical and major defects

For each finding:

- severity and typed kind;
- concise claim;
- current artifact location;
- exact source evidence;
- better context or expected behavior;
- why the defect matters to a reader.

## Slide audit

| Slide | Predicted status | Topic verdict | Anchor verdict | Evidence strength | Regret | Rendering | Confidence | Evidence |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- |

Include every slide. Keep ambiguous slides explicitly `reasonable_range` or `unknown`.

## Slide metrics

For every metric include `numerator/denominator`, percentage, and excluded unknown count.

| Metric | Value | Count | Unknown/excluded |
| --- | ---: | ---: | ---: |
| Discussed precision |  |  |  |
| Discussed recall |  |  |  |
| Unmentioned false-negative rate |  |  |  |
| Acceptable topic accuracy |  |  |  |
| Preferred topic accuracy |  |  |  |
| Wrong-topic rate |  |  |  |
| Best-context hit |  |  |  |
| Acceptable-context hit |  |  |  |
| Materially-better-context rate |  |  |  |
| Verified precision |  |  |  |
| High-confidence error rate |  |  |  |
| Collapsed-slide rate |  |  |  |
| Rendering correctness |  |  |  |

Also report maximum slides per evidence cue/anchor and appendix false positives.

## Strong evidence-backed aspects

List representative correct note passages and slide placements.

## Confidence calibration

Count incorrect high-confidence placements, false-negative `unmentioned` slides, weak
high-confidence matches, and appropriate fallbacks.

## Uncertainty and limitations

Separate unchecked scope from checked-and-correct scope.

## Verdict

Return exactly one:

`excellent`, `good`, `usable_with_minor_issues`,
`usable_with_alignment_issues`, `poor`, or `evaluation_inconclusive`.

Give a short evidence-based rationale. Do not let prose quality hide alignment failures.
