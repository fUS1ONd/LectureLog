from lecturelog.evaluation.aggregation import aggregate_evaluation


def test_aggregation_uses_declared_dimension_weights() -> None:
    result = aggregate_evaluation(
        profile="standard",
        dimension_scores={
            "faithfulness": 90,
            "content_coverage": 80,
            "block_quality": 70,
            "document_structure": 60,
            "slide_semantic_relevance": 50,
            "slide_anchor_precision": 40,
            "confidence_calibration": 30,
        },
        judge_stability=0.9,
    )

    assert result["overall_score"] == 62.9
    assert result["status"] == "complete"
    assert result["available_score_weight"] == 1.0


def test_gate_failure_changes_verdict_even_with_high_score() -> None:
    result = aggregate_evaluation(
        profile="standard",
        dimension_scores=dict.fromkeys(
            (
                "faithfulness",
                "content_coverage",
                "block_quality",
                "document_structure",
                "slide_semantic_relevance",
                "slide_anchor_precision",
                "confidence_calibration",
            ),
            95,
        ),
        slide_evaluations=[
            {"placement_verdict": "incorrect", "system_source_confidence": "verified"},
            *[{"placement_verdict": "correct"} for _ in range(9)],
        ],
        judge_stability=0.95,
    )

    assert result["overall_score"] == 95
    assert result["verdict"] == "usable_with_alignment_issues"
    assert next(
        gate for gate in result["quality_gates"] if gate["id"] == "incorrect_slide_placements"
    )["status"] == "pass"
    assert next(
        gate
        for gate in result["quality_gates"]
        if gate["id"] == "verified_incorrect_slide_placements"
    )["status"] == "fail"


def test_slide_relevance_combines_topic_match_and_specificity() -> None:
    result = aggregate_evaluation(
        profile="static",
        slide_evaluations=[
            {"semantic_relevance": 100, "specificity": 50},
            {"semantic_relevance": 50, "specificity": 100},
        ],
    )

    assert result["scorecard"]["slide_semantic_relevance"] == 75


def test_weak_and_confidently_outranked_slides_fail_placement_gate() -> None:
    slides = [
        {"placement_verdict": "weak"},
        {
            "placement_verdict": "acceptable",
            "current_context_rank": 2,
            "better_context_id": "block-42",
            "better_context_confidence": 0.82,
        },
        *[{"placement_verdict": "correct"} for _ in range(8)],
    ]
    result = aggregate_evaluation(profile="static", slide_evaluations=slides)

    gate = next(
        gate for gate in result["quality_gates"] if gate["id"] == "incorrect_slide_placements"
    )
    assert gate["actual"] == 20
    assert gate["status"] == "fail"


def test_low_confidence_alternative_does_not_count_as_placement_issue() -> None:
    result = aggregate_evaluation(
        profile="static",
        slide_evaluations=[
            {
                "placement_verdict": "acceptable",
                "current_context_rank": 3,
                "better_context_id": "block-42",
                "better_context_confidence": 0.69,
            }
        ],
    )

    gate = next(
        gate for gate in result["quality_gates"] if gate["id"] == "incorrect_slide_placements"
    )
    assert gate["actual"] == 0
    assert gate["status"] == "pass"


def test_high_overall_with_low_anchor_quality_is_alignment_issue() -> None:
    scores = dict.fromkeys(
        (
            "faithfulness",
            "content_coverage",
            "block_quality",
            "document_structure",
            "slide_semantic_relevance",
            "slide_anchor_precision",
            "confidence_calibration",
        ),
        92,
    )
    scores["slide_anchor_precision"] = 40
    result = aggregate_evaluation(
        profile="standard",
        dimension_scores=scores,
        judge_stability=0.9,
    )

    assert result["overall_score"] == 85.2
    assert result["verdict"] == "usable_with_alignment_issues"
    gate = next(
        gate for gate in result["quality_gates"] if gate["id"] == "slide_anchor_quality"
    )
    assert gate["actual"] == 40
    assert gate["status"] == "fail"


def test_static_anchor_collapse_has_visible_gate_but_stays_inconclusive() -> None:
    result = aggregate_evaluation(
        profile="static",
        deterministic_findings=[
            {
                "code": "slide_anchor_collapse",
                "severity": "major",
                "message": "Most slides share one anchor",
            }
        ],
    )

    assert result["verdict"] == "evaluation_inconclusive"
    gate = next(
        gate
        for gate in result["quality_gates"]
        if gate["id"] == "deterministic_alignment_anomalies"
    )
    assert gate["actual"] == 1
    assert gate["status"] == "fail"


def test_incomplete_run_never_gets_confident_verdict() -> None:
    result = aggregate_evaluation(
        profile="standard",
        dimension_scores={"faithfulness": 100},
        incomplete_reasons=["quota exhausted"],
    )

    assert result["status"] == "incomplete"
    assert result["verdict"] == "evaluation_inconclusive"
    assert result["incomplete_reasons"] == [
        "quota exhausted",
        "not all requested judge dimensions were evaluated",
    ]


def test_remote_issues_participate_in_gates_and_have_separate_count() -> None:
    result = aggregate_evaluation(
        profile="standard",
        deterministic_findings=[{"code": "repeated_heading", "severity": "minor"}],
        judge_findings=[
            {
                "kind": "faithfulness",
                "code": "arbitrary_model_label",
                "severity": "critical",
                "message": "Contradiction",
            }
        ],
        dimension_scores=dict.fromkeys(
            (
                "faithfulness",
                "content_coverage",
                "block_quality",
                "document_structure",
                "slide_semantic_relevance",
                "slide_anchor_precision",
                "confidence_calibration",
            ),
            90,
        ),
        judge_stability=0.9,
    )

    assert result["counts"]["deterministic_findings"] == 1
    assert result["counts"]["judge_findings"] == 1
    gate = next(
        gate
        for gate in result["quality_gates"]
        if gate["id"] == "critical_transcript_contradiction"
    )
    assert gate["status"] == "fail"


def test_arbitrary_code_cannot_bypass_or_trigger_typed_faithfulness_gate() -> None:
    base_scores = dict.fromkeys(
        (
            "faithfulness",
            "content_coverage",
            "block_quality",
            "document_structure",
            "slide_semantic_relevance",
            "slide_anchor_precision",
            "confidence_calibration",
        ),
        90,
    )
    bypass = aggregate_evaluation(
        profile="standard",
        judge_findings=[
            {
                "kind": "faithfulness",
                "code": "made_up_code",
                "severity": "critical",
            }
        ],
        dimension_scores=base_scores,
        judge_stability=0.9,
    )
    spoof = aggregate_evaluation(
        profile="standard",
        judge_findings=[
            {
                "kind": "other",
                "code": "critical_transcript_contradiction",
                "severity": "critical",
            }
        ],
        dimension_scores=base_scores,
        judge_stability=0.9,
    )

    bypass_gate = next(
        gate
        for gate in bypass["quality_gates"]
        if gate["id"] == "critical_transcript_contradiction"
    )
    spoof_gate = next(
        gate
        for gate in spoof["quality_gates"]
        if gate["id"] == "critical_transcript_contradiction"
    )
    assert bypass_gate["status"] == "fail"
    assert spoof_gate["status"] == "pass"


def test_static_profile_is_complete_but_semantically_inconclusive() -> None:
    result = aggregate_evaluation(
        profile="static",
        deterministic_findings=[
            {
                "code": "unexpected_full_language_block",
                "severity": "major",
                "message": "English island",
            }
        ],
        blocks_inspected=12,
    )

    assert result["status"] == "complete"
    assert result["verdict"] == "evaluation_inconclusive"
    assert result["highest_impact_findings"][0]["code"] == "unexpected_full_language_block"
    assert result["counts"]["blocks_evaluated"] == 0
    assert result["counts"]["blocks_inspected"] == 12


def test_static_broken_invariant_is_invalid() -> None:
    result = aggregate_evaluation(
        profile="static",
        deterministic_findings=[
            {
                "code": "broken_markdown_reference",
                "severity": "critical",
                "message": "Missing slide image",
            }
        ],
    )

    assert result["status"] == "complete"
    assert result["verdict"] == "invalid"


def test_smoke_reports_sampling_limitation_and_coverage_counts() -> None:
    result = aggregate_evaluation(
        profile="smoke",
        block_evaluations=[{"clarity": 80}],
        section_evaluations=[{"coverage": 80}],
        blocks_inspected=20,
        sections_inspected=5,
        slides_inspected=12,
        incomplete_reasons=["sample only"],
    )

    assert result["counts"]["blocks_evaluated"] == 1
    assert result["counts"]["blocks_inspected"] == 20
    assert result["counts"]["sections_inspected"] == 5
    assert result["counts"]["slides_inspected"] == 12
    assert any("prioritized sample" in limitation for limitation in result["limitations"])


def test_unknown_stability_caps_standard_release_verdict() -> None:
    result = aggregate_evaluation(
        profile="standard",
        dimension_scores=dict.fromkeys(
            (
                "faithfulness",
                "content_coverage",
                "block_quality",
                "document_structure",
                "slide_semantic_relevance",
                "slide_anchor_precision",
                "confidence_calibration",
            ),
            98,
        ),
    )

    assert result["status"] == "complete"
    assert result["verdict"] == "usable_with_minor_issues"


def test_smoke_verdict_is_explicitly_directional() -> None:
    result = aggregate_evaluation(
        profile="smoke",
        dimension_scores=dict.fromkeys(
            (
                "faithfulness",
                "content_coverage",
                "block_quality",
                "document_structure",
                "slide_semantic_relevance",
                "slide_anchor_precision",
                "confidence_calibration",
            ),
            98,
        ),
        judge_stability=0.95,
    )

    assert result["verdict"] == "sampled_directional"


def test_sampled_slide_rate_does_not_apply_full_document_gate() -> None:
    result = aggregate_evaluation(
        profile="static",
        slide_evaluations=[{"placement_verdict": "incorrect"}],
        slides_inspected=20,
    )

    gate = next(
        gate for gate in result["quality_gates"] if gate["id"] == "incorrect_slide_placements"
    )
    assert gate["status"] == "unknown"
    assert result["coverage"]["slides"]["complete"] is False


def test_findings_are_deduplicated_and_warning_has_supported_severity() -> None:
    finding = {"code": "same", "severity": "warning", "message": "same issue"}
    result = aggregate_evaluation(
        profile="static",
        deterministic_findings=[finding],
        judge_findings=[finding],
    )

    assert len(result["highest_impact_findings"]) == 1
