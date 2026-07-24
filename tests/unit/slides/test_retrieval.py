from lecturelog.domain.slides import SectionRef, SlideCatalogEntry
from lecturelog.infrastructure.slides.alignment.retrieval import generate_candidates
from lecturelog.infrastructure.srt import parse_srt_blocks


def test_retrieval_finds_matching_section_and_expands_neighbor() -> None:
    blocks = parse_srt_blocks(
        "1\n00:00:00,000 --> 00:00:09,000\nвведение\n\n"
        "2\n00:00:10,000 --> 00:00:19,000\nбинарное дерево поиска\n\n"
        "3\n00:00:20,000 --> 00:00:29,000\nзаключение\n"
    )
    sections = tuple(SectionRef(i, 0, i, i * 10, i * 10 + 9) for i in range(3))
    entry = SlideCatalogEntry(1, "content", "Бинарное дерево", "дерево поиска")
    candidates = generate_candidates(entry, sections, blocks, limit=1)
    assert candidates[0].global_section_id == 1
    assert {candidate.global_section_id for candidate in candidates} == {0, 1, 2}

