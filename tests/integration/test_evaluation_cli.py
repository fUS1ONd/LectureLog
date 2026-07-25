import json
import zipfile

import lecturelog.evaluation.cli as evaluation_cli
from lecturelog.evaluation.cli import (
    _attach_system_confidence,
    _failed_batch_reasons,
    _global_metrics,
    _measured_stability,
    _model_policy,
    _remote_issues,
    _remote_plan_summary,
    main,
)
from lecturelog.evaluation.openrouter import ADJUDICATOR_MODEL, TEXT_MODEL, VISION_MODEL


def _result_zip(path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("конспект.md", "# Тема\n\nРусский текст.")
        archive.writestr("structure.json", '{"sections": []}')
        archive.writestr("transcript.srt", "1\n00:00:00,000 --> 00:00:01,000\nТекст\n")


def test_static_cli_writes_product_artifacts(tmp_path) -> None:
    result = tmp_path / "result.zip"
    output = tmp_path / "evaluation"
    _result_zip(result)

    exit_code = main(
        [
            "evaluate",
            "--result",
            str(result),
            "--profile",
            "static",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert {path.name for path in output.iterdir()} >= {
        "evaluation.json",
        "report.md",
        "manifest.json",
        "deterministic-findings.json",
        "block-evaluations.json",
        "section-evaluations.json",
        "slide-evaluations.json",
        "judge-calls.jsonl",
        "judge-attempts.jsonl",
    }
    evaluation = json.loads((output / "evaluation.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert evaluation["profile"] == "static"
    assert evaluation["status"] == "complete"
    assert evaluation["counts"]["blocks_evaluated"] == 0
    assert evaluation["counts"]["blocks_inspected"] > 0
    assert manifest["remote_llm_used"] is False
    assert manifest["result"]["sha256"]


def test_remote_adapter_exposes_dimensions_stability_and_issues() -> None:
    judgments = [
        {
            "stable_id": "block-1",
            "issues": [
                {
                    "code": "unsupported_critical_claim",
                    "severity": "critical",
                    "message": "No transcript support",
                    "evidence": [{"stable_id": "cue-1", "quote": "source"}],
                }
            ],
        }
    ]
    global_results = [
        {
            "dimension_scores": {"faithfulness": 72, "block_quality": 81},
            "document_structure": 77,
            "judge_stability": 0.86,
        }
    ]

    findings = _remote_issues(judgments)
    scores = _global_metrics(global_results)

    assert findings[0]["source"] == "remote_judge"
    assert findings[0]["stable_id"] == "block-1"
    assert scores == {"faithfulness": 72, "block_quality": 81, "document_structure": 77}
    assert _measured_stability(None) is None


def test_model_policy_uses_openrouter_constants() -> None:
    assert _model_policy() == {
        "text": TEXT_MODEL,
        "vision": VISION_MODEL,
        "adjudicator": ADJUDICATOR_MODEL,
    }


def test_stability_requires_explicit_repeated_checks() -> None:
    assert _measured_stability([{"stable": True}, {"stable": False}, {"stable": True}]) == 2 / 3
    assert _measured_stability({"score": 0.81}) == 0.81
    assert _measured_stability([]) is None


def test_failed_or_invalid_remote_batches_make_run_incomplete() -> None:
    reasons = _failed_batch_reasons(
        {
            "incomplete": True,
            "failed_batches": ["slide-2"],
            "blocks": [{"stable_id": "b1", "status": "invalid", "score": 0}],
        }
    )

    assert any("incomplete" in reason for reason in reasons)
    assert any("slide-2" in reason for reason in reasons)
    assert any("b1" in reason and "invalid" in reason for reason in reasons)


def test_adapter_uses_system_assignment_confidence_not_judge_number() -> None:
    artifacts = {
        "alignment": {
            "assignments": [{"slide_num": 3, "assignment_confidence": "verified"}]
        }
    }

    adapted = _attach_system_confidence(
        [{"stable_id": "3", "source_confidence": 0.25}], artifacts
    )

    assert adapted[0]["source_confidence"] == 0.25
    assert adapted[0]["system_source_confidence"] == "verified"


def test_adapter_can_source_verified_confidence_from_placement() -> None:
    artifacts = {
        "alignment": {
            "assignments": [{"slide_num": 3, "assignment_confidence": "probable"}],
            "placements": [{"slide_num": 3, "anchor_confidence": "verified"}],
        }
    }

    adapted = _attach_system_confidence(
        [{"stable_id": "slide-3", "source_confidence": 0.99}], artifacts
    )

    assert adapted[0]["system_source_confidence"] == "verified"


def test_preflight_worst_case_is_physical_profile_budget() -> None:
    summary = _remote_plan_summary(
        {"blocks": [], "sections": [], "slides": [], "alignment": None},
        "smoke",
        8,
    )

    assert summary["logical_batches"] == 1
    assert summary["worst_case_physical_requests"] == 3


def test_failed_remote_attempt_is_preserved_in_manifest(tmp_path, monkeypatch) -> None:
    result = tmp_path / "result.zip"
    output = tmp_path / "evaluation"
    _result_zip(result)

    received: dict[str, object] = {}

    def failed_remote(*_args, **kwargs):
        received.update(kwargs)
        return {
            "blocks": [],
            "sections": [],
            "slides": [],
            "global": [],
            "calls": [],
            "usage": {"requests_used": 1},
            "incomplete": True,
            "incomplete_reasons": ["provider failed after request"],
        }

    monkeypatch.setattr(evaluation_cli, "_run_remote", failed_remote)
    assert (
        main(
            [
                "evaluate",
                "--result",
                str(result),
                "--profile",
                "smoke",
                "--allow-remote-llm",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    manifest = json.loads((output / "manifest.json").read_text())
    persisted_findings = json.loads((output / "deterministic-findings.json").read_text())
    report = (output / "report.md").read_text()
    assert evaluation_cli._jsonable(received["deterministic_findings"]) == persisted_findings
    assert manifest["new_remote_requests"] == 1
    assert manifest["remote_judgments_used"] == 0
    assert manifest["remote_llm_used"] is True
    attempts = [
        json.loads(line)
        for line in (output / "judge-attempts.jsonl").read_text().splitlines()
    ]
    assert attempts[0]["status"] == "failed_unreported"
    assert "New physical remote requests attempted: 1" in report
    assert "Successful/cached remote judgments used: 0" in report
