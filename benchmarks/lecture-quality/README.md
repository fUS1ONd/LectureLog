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

