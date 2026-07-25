import json

from lecturelog.evaluation.reporting import render_markdown_report, write_json, write_jsonl


def test_report_leads_with_verdict_scorecard_gates_and_incomplete_reason() -> None:
    evaluation = {
        "verdict": "evaluation_inconclusive",
        "status": "incomplete",
        "overall_score": None,
        "scorecard": {"faithfulness": None},
        "quality_gates": [
            {
                "label": "Judge stability",
                "status": "unknown",
                "actual": None,
                "operator": ">=",
                "threshold": 0.8,
            }
        ],
        "highest_impact_findings": [
            {"code": "mixed_language", "severity": "major", "message": "Language island"}
        ],
        "usage": {"requests_used": 3, "cache_hits": 2},
        "incomplete_reasons": ["quota exhausted"],
        "judge_stability": None,
        "limitations": ["Only representative blocks were judged."],
        "counts": {},
    }
    report = render_markdown_report(
        evaluation,
        {"models": {"text": "free/model"}, "remote_llm_used": True},
    )

    assert report.index("**Verdict:**") < report.index("## Scorecard")
    assert "not evaluated" in report
    assert "quota exhausted" in report
    assert "mixed_language" in report
    assert "Requests used: 3" in report


def test_json_writer_keeps_unicode_and_is_parseable(tmp_path) -> None:
    path = tmp_path / "nested" / "evaluation.json"
    write_json(path, {"message": "английский блок"})

    assert json.loads(path.read_text()) == {"message": "английский блок"}
    assert "английский блок" in path.read_text()


def test_jsonl_writer_persists_one_call_per_line(tmp_path) -> None:
    path = tmp_path / "judge-calls.jsonl"
    write_jsonl(path, [{"kind": "block"}, {"kind": "global"}])

    assert [json.loads(line) for line in path.read_text().splitlines()] == [
        {"kind": "block"},
        {"kind": "global"},
    ]


def test_report_contains_usable_evaluated_item_drill_down() -> None:
    evaluation = {
        "status": "complete",
        "verdict": "sampled_directional",
        "overall_score": 80,
        "scorecard": {},
        "quality_gates": [],
        "highest_impact_findings": [],
        "usage": {},
        "limitations": [],
        "counts": {},
        "drill_down": {
            "blocks": [
                {
                    "stable_id": "block-7",
                    "score": 42,
                    "faithfulness": 30,
                    "issues": [
                        {"severity": "major", "code": "unsupported", "message": "No source"}
                    ],
                    "evidence": [{"stable_id": "cue-2", "quote": "Исходный текст"}],
                }
            ],
            "sections": [],
            "slides": [],
        },
    }

    report = render_markdown_report(evaluation, {})

    assert "`block-7`" in report
    assert "major:unsupported No source" in report
    assert "Исходный текст" in report
