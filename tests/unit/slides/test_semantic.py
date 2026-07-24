import json

import pytest

from lecturelog.domain.slides import SectionRef, SlideCatalogEntry
from lecturelog.infrastructure.slides.alignment.retrieval import generate_candidates
from lecturelog.infrastructure.slides.alignment.semantic import validate_semantic_response
from lecturelog.infrastructure.srt import parse_srt_blocks


def test_semantic_rejects_fake_evidence_id_and_ungrounded_quote() -> None:
    blocks = parse_srt_blocks("1\n00:00:00,000 --> 00:00:05,000\nобсудим графы\n")
    entry = SlideCatalogEntry(1, "content", "Графы", "графы")
    candidates = generate_candidates(entry, (SectionRef(0, 0, 0, 0, 5),), blocks)
    with pytest.raises(ValueError):
        validate_semantic_response(
            json.dumps(
                {
                    "slide_num": 1,
                    "global_section_id": 0,
                    "evidence_block_ids": [99],
                    "evidence_quote": "графы",
                    "semantic_tier": "explicit",
                }
            ),
            entry=entry,
            candidates=candidates,
            blocks=blocks,
        )

