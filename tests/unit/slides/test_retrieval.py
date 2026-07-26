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


def test_retrieval_normalizes_long_generic_section_and_limits_evidence() -> None:
    generic = "\n\n".join(
        f"{index}\n00:00:{index:02d},000 --> 00:00:{index:02d},500\n"
        "модель система требования процесс"
        for index in range(1, 9)
    )
    blocks = parse_srt_blocks(
        generic
        + "\n\n9\n00:00:10,000 --> 00:00:11,000\n"
        "спиральная модель анализирует риски на каждом витке\n"
    )
    sections = (
        SectionRef(0, 0, 0, 0, 8.5),
        SectionRef(1, 0, 1, 9.5, 11.5),
    )
    entry = SlideCatalogEntry(
        1,
        "content",
        "Спиральная модель",
        "Анализ рисков на каждом витке",
    )

    candidates = generate_candidates(
        entry, sections, blocks, limit=1, neighbor_radius=0
    )

    assert candidates[0].global_section_id == 1
    assert candidates[0].evidence_block_ids == (9,)
