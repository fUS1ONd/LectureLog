from lecturelog.domain.slides import SlideCandidate
from lecturelog.infrastructure.slides.alignment.sequence import align_sequence


def _candidate(slide: int, section: int, score: float, tier: str = "explicit") -> SlideCandidate:
    return SlideCandidate(
        slide,
        section,
        (section + 1,),
        "grounded",
        section * 10,
        section * 10 + 5,
        score,
        tier,
    )


def test_sequence_allows_unmatched_and_does_not_force_weak_slide() -> None:
    result = align_sequence(
        [1, 2],
        {
            1: (_candidate(1, 0, 5),),
            2: (_candidate(2, 1, -2, "none"),),
        },
    )
    assert result[0].match_status == "discussed"
    assert result[1].match_status == "unmentioned"


def test_sequence_softly_allows_strong_backtrack() -> None:
    result = align_sequence(
        [1, 2],
        {1: (_candidate(1, 2, 8),), 2: (_candidate(2, 0, 12),)},
    )
    assert [item.global_section_id for item in result] == [2, 0]
