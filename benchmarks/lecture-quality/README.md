# Lecture quality benchmarks

This directory stores lightweight, reviewable results produced by
`skills/lecture-quality-judge`. Source audio, slide decks, result archives, and secrets
must remain outside Git.

Each baseline records:

- the source and generated-artifact hashes;
- the exact code commit and judge method;
- raw numerators and denominators for every headline slide metric;
- independently verified failure examples;
- known ambiguities and exclusions.

Before/after matcher comparisons must use fresh judges in separate contexts. Judges must
not read these baselines until they have completed both passes of their own audit.

## Reports

| Report | Lecture | Run |
| --- | --- | --- |
| `baseline-2026-07-25.md` | 2026-02-12 | baseline before the v2 experiments |
| `2026-07-26-judge-a-flash-lite.md` | 2026-02-12 | A |
| `2026-07-26-judge-b-flash36.md` | 2026-02-12 | B |
| `2026-07-27-judge-c-fixes-medium.md` | 2026-02-12 | C |
| `2026-07-28-judge-d-strict-schemas.md` | 2026-02-12 | D |
| `2026-07-28-judge-e-prompt-v2.md` | 2026-02-12 | E |
| `2026-07-28-judge-f-gallery-rule.md` | 2026-02-12 | F |
| `2026-07-29-judge-i-proper-nouns.md` | 2026-02-12 | I (runs G and H were not judged) |
| `2026-07-29-judge-j-02-26.md` | 2026-02-26 | J, same configuration as I |
| `2026-07-29-judge-k-03-12.md` | 2026-03-12 | K, first validation lecture |

Run configurations and cross-run analysis live in
`docs/progress/2026-07-27-matcher-model-experiments.md`. Dataset splits and the release
gates live in `generalization-roadmap.md`.

