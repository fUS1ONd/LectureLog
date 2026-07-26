from lecturelog.domain.slides import SlideCandidate, SlideRelation
from lecturelog.infrastructure.slides.alignment.sequence import align_sequence


def _candidate(
    slide: int,
    section: int,
    score: float,
    tier: str = "explicit",
    competition_margin: float | None = None,
) -> SlideCandidate:
    return SlideCandidate(
        slide,
        section,
        (section + 1,),
        "grounded",
        section * 10,
        section * 10 + 5,
        score,
        tier,
        competition_margin=competition_margin,
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


def test_single_candidate_cannot_be_verified_without_real_competition() -> None:
    result = align_sequence([1], {1: (_candidate(1, 0, 50),)})

    assert result[0].match_status == "discussed"
    assert result[0].assignment_confidence == "probable"


def test_real_runner_up_margin_can_verify_explicit_evidence() -> None:
    result = align_sequence(
        [1],
        {1: (_candidate(1, 0, 10, competition_margin=3.0),)},
    )

    assert result[0].assignment_confidence == "verified"


def test_sequence_softly_allows_strong_backtrack() -> None:
    result = align_sequence(
        [1, 2],
        {1: (_candidate(1, 2, 8),), 2: (_candidate(2, 0, 12),)},
    )
    assert [item.global_section_id for item in result] == [2, 0]


def test_progressive_build_is_not_suppressed_as_duplicate() -> None:
    result = align_sequence(
        [1, 2],
        {1: (_candidate(1, 0, 5),), 2: (_candidate(2, 0, 5),)},
        (SlideRelation(2, "progressive_build", "g", 1),),
    )
    assert [item.match_status for item in result] == ["discussed", "discussed"]
