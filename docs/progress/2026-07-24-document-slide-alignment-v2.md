# Document slide alignment v2 — implementation progress

Date: 2026-07-24

Implemented:

- explicit slide asset, assignment, placement and structurize result contracts;
- atomic PDF/PPTX rendering with native text extraction and page validation;
- unified stable-ID SRT parser;
- strict catalog/semantic response schemas and native-text fallback;
- bounded lexical/character retrieval with section neighbors;
- evidence validation and global sequence alignment with constrained-path margins;
- `legacy|shadow|v2` configuration and scratch diagnostics;
- evidence-first v2 rendering without slide images;
- safe Markdown block anchoring, canonical markers, gallery/appendix/suppression;
- explicit `slide_num → target` export/structure mapping;
- ORB + homography primitives and temporal visual-run aggregation;
- reproducible evaluator and synthetic fixtures.

Local verification:

- `513 passed`;
- `ruff check lecturelog tests scripts`;
- `git diff --check`;
- public API/OpenAPI integration tests are part of the passing suite.

Not yet claimed:

- release thresholds from the design document require the real held-out corpus;
- production shadow and selective rollout are operational steps after merge/deploy;
- video/document visual evidence is covered by synthetic geometry tests, but its
  thresholds still require real recorded-projector footage.

Before publication, run the same real cases in `legacy` and `v2`, record per-page
ground truth, and compare discussed precision/recall, section accuracy, anchor
error, inline precision/coverage, gallery rate, and false-inline count.
