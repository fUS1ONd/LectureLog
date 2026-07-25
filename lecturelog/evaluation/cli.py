"""Command-line product shell for offline lecture evaluation."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lecturelog.evaluation.aggregation import aggregate_evaluation
from lecturelog.evaluation.reporting import write_json, write_jsonl, write_report

PROFILE_CAPS = {"static": 0, "smoke": 8, "standard": 24, "deep": 45}


def _model_policy() -> dict[str, str]:
    from lecturelog.evaluation.openrouter import (
        ADJUDICATOR_MODEL,
        TEXT_MODEL,
        VISION_MODEL,
    )

    return {
        "text": TEXT_MODEL,
        "vision": VISION_MODEL,
        "adjudicator": ADJUDICATOR_MODEL,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m lecturelog.evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate", help="evaluate one generated result")
    evaluate.add_argument("--result", type=Path, required=True)
    evaluate.add_argument("--slides", type=Path)
    evaluate.add_argument("--profile", choices=tuple(PROFILE_CAPS), default="standard")
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--allow-remote-llm", action="store_true")
    evaluate.add_argument("--max-requests", type=int)
    evaluate.add_argument("--resume", action="store_true")
    return parser


def _hash_file(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _field(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _load_artifacts(result: Path, slides: Path | None) -> Any:
    from lecturelog.evaluation import artifacts

    loader = getattr(artifacts, "load_artifacts", None) or getattr(
        artifacts, "load_evaluation_artifacts", None
    )
    if loader is None:
        loader_class = getattr(artifacts, "ArtifactLoader", None)
        if loader_class is None:
            raise RuntimeError("artifact loader interface is unavailable")
        loader = loader_class().load
    try:
        return loader(result=result, slides=slides)
    except TypeError:
        try:
            return loader(result_path=result, slides_path=slides)
        except TypeError:
            return loader(result, slides)


def _run_static_checks(artifacts_value: Any) -> list[Any]:
    from lecturelog.evaluation import deterministic

    checker = getattr(deterministic, "run_deterministic_checks", None) or getattr(
        deterministic, "evaluate_deterministic", None
    )
    if checker is None:
        checker_class = getattr(deterministic, "DeterministicEvaluator", None)
        if checker_class is None:
            raise RuntimeError("deterministic evaluator interface is unavailable")
        checker = checker_class().evaluate
    result = checker(artifacts_value)
    return list(_field(result, "findings", default=result) or [])


def _items(artifacts_value: Any, name: str) -> list[Any]:
    return list(_field(artifacts_value, name, default=[]) or [])


def _evaluation_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _print_remote_summary(profile: str, cap: int, resume: bool) -> None:
    print(f"Profile: {profile}")
    print("Judge models:")
    for role, model in _model_policy().items():
        print(f"  {role}: {model}")
    print(f"Request hard cap: {cap}; estimated requests are finalized after artifact planning.")
    print("Images may be uploaded only for visually dependent slide packets.")
    print("Privacy warning: free OpenRouter providers may log prompts, images, and outputs.")
    print(f"Cache mode: {'resume enabled' if resume else 'new/reusable content cache'}")


def _remote_plan_summary(artifacts_value: Any, profile: str, cap: int) -> dict[str, Any]:
    """Describe the public logical plan without reimplementing request selection."""
    from lecturelog.evaluation.planner import plan_judge_batches

    batches = plan_judge_batches(
        profile,
        blocks=_items(artifacts_value, "blocks"),
        sections=_items(artifacts_value, "sections"),
        slides=_items(artifacts_value, "slides"),
        alignment=_field(artifacts_value, "alignment"),
    )
    by_kind: dict[str, int] = {}
    for batch in batches:
        kind = str(_field(batch, "kind", default="unknown"))
        by_kind[kind] = by_kind.get(kind, 0) + 1
    logical = min(len(batches), cap)
    metadata = _field(batches, "metadata", default={})
    worst_case = _field(
        metadata,
        "worst_case_physical_requests",
        "max_physical_requests",
        default=cap,
    )
    if not isinstance(worst_case, int) or isinstance(worst_case, bool):
        worst_case = cap
    return {
        "logical_batches": logical,
        "batches_by_kind": by_kind,
        "cache_hits": None,
        # Retries/adjudication may spend the rest of the profile budget even when the
        # initial logical plan is smaller. The planner's metadata wins when available.
        "worst_case_physical_requests": min(worst_case, cap),
    }


def _print_plan_summary(plan: dict[str, Any]) -> None:
    kinds = ", ".join(f"{kind}={count}" for kind, count in plan["batches_by_kind"].items())
    print(f"Logical judge plan: {plan['logical_batches']} batches ({kinds or 'empty'})")
    print("Preflight cache hits: resolved by the runner from exact prompt keys")
    print(f"Worst-case physical requests: {plan['worst_case_physical_requests']}")


def _finding(
    code: str,
    message: str,
    *,
    severity: str = "critical",
) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _remote_issues(groups: Iterable[Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for evaluation in groups:
        serialized = _jsonable(evaluation)
        if not isinstance(serialized, dict):
            continue
        nested = serialized.get("judgments")
        if isinstance(nested, list):
            findings.extend(_remote_issues(nested))
        issues = [
            *(serialized.get("issues", []) or []),
            *(serialized.get("findings", []) or []),
        ]
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            finding = dict(issue)
            finding.setdefault("code", "remote_judge_issue")
            finding.setdefault("severity", "warning")
            finding.setdefault("message", "Remote judge reported an issue.")
            finding["source"] = "remote_judge"
            finding.setdefault("stable_id", serialized.get("stable_id"))
            findings.append(finding)
    return findings


def _global_metrics(global_evaluations: Iterable[Any]) -> dict[str, Any]:
    scores: dict[str, Any] = {}
    for evaluation in global_evaluations:
        serialized = _jsonable(evaluation)
        if not isinstance(serialized, dict):
            continue
        candidate = serialized.get("dimension_scores")
        if isinstance(candidate, dict):
            scores.update(candidate)
        for name in (
            "faithfulness",
            "content_coverage",
            "block_quality",
            "document_structure",
            "slide_semantic_relevance",
            "slide_anchor_precision",
            "confidence_calibration",
        ):
            if name in serialized:
                scores[name] = serialized[name]
    return scores


def _measured_stability(checks: Any) -> float | None:
    if isinstance(checks, dict):
        value = checks.get("score", checks.get("stability"))
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        return None
    if not isinstance(checks, list) or not checks:
        return None
    outcomes: list[bool] = []
    for check in checks:
        if isinstance(check, bool):
            outcomes.append(check)
        elif isinstance(check, dict) and isinstance(check.get("stable"), bool):
            outcomes.append(check["stable"])
    return sum(outcomes) / len(outcomes) if outcomes else None


def _system_confidence_by_slide(artifacts_value: Any) -> dict[str, str]:
    alignment = _field(artifacts_value, "alignment")
    records = [
        *(_field(alignment, "assignments", default=()) or ()),
        *(_field(alignment, "placements", default=()) or ()),
    ]
    rank = {"none": 0, "unresolved": 0, "fallback": 1, "probable": 2, "verified": 3}
    result: dict[str, str] = {}
    for record in records:
        slide_num = _field(record, "slide_num", "slide_number")
        confidence = _field(record, "assignment_confidence", "anchor_confidence")
        if slide_num is not None and confidence is not None:
            candidate = str(confidence).lower()
            current = result.get(str(slide_num), "none")
            if rank.get(candidate, -1) > rank.get(current, -1):
                result[str(slide_num)] = candidate
    return result


def _attach_system_confidence(
    evaluations: list[Any], artifacts_value: Any
) -> list[Any]:
    confidence = _system_confidence_by_slide(artifacts_value)
    result: list[Any] = []
    for evaluation in evaluations:
        serialized = _jsonable(evaluation)
        if not isinstance(serialized, dict):
            result.append(evaluation)
            continue
        stable_id = str(serialized.get("stable_id", ""))
        slide_id = stable_id.removeprefix("slide-")
        if slide_id in confidence:
            serialized["system_source_confidence"] = confidence[slide_id]
        result.append(serialized)
    return result


def _failed_batch_reasons(remote_result: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if remote_result.get("incomplete") and not remote_result.get("incomplete_reasons"):
        reasons.append("Remote judge run reported an incomplete result.")
    failed_batches = remote_result.get("failed_batches", []) or []
    reasons.extend(f"Remote judge batch failed: {batch}" for batch in failed_batches)
    for group in ("blocks", "sections", "slides", "global"):
        for item in remote_result.get(group, []) or []:
            serialized = _jsonable(item)
            if isinstance(serialized, dict) and serialized.get("status") in {"failed", "invalid"}:
                reasons.append(
                    f"{group} judge result {serialized.get('stable_id', 'unknown')} "
                    f"is {serialized['status']}."
                )
    return reasons


def _remote_placeholder_reason() -> str:
    return (
        "Remote judge calls were requested, but no judge results were returned; "
        "the deterministic report remains inspectable and resumable."
    )


def _run_evaluate(args: argparse.Namespace) -> int:
    started = datetime.now(UTC)
    if args.max_requests is not None and args.max_requests < 0:
        raise ValueError("--max-requests must be non-negative")
    cap = PROFILE_CAPS[args.profile]
    if args.max_requests is not None:
        cap = min(cap, args.max_requests)
    if args.profile != "static" and not args.allow_remote_llm:
        raise ValueError(
            "remote profiles require explicit --allow-remote-llm; "
            "use --profile static for an offline run"
        )
    if args.profile == "static" and args.allow_remote_llm:
        print("Note: --allow-remote-llm is ignored by the static profile.")
    if args.profile != "static":
        _print_remote_summary(args.profile, cap, args.resume)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    findings: list[Any] = []
    incomplete_reasons: list[str] = []
    artifacts_value: Any = {}
    try:
        artifacts_value = _load_artifacts(args.result, args.slides)
        findings.extend(_run_static_checks(artifacts_value))
    except Exception as exc:  # report invalid/incomplete artifacts instead of losing the run
        findings.append(_finding("broken_artifact_input", str(exc)))
        incomplete_reasons.append(f"Artifact evaluation failed: {exc}")
    deterministic_findings = list(findings)

    block_evaluations: list[Any] = []
    section_evaluations: list[Any] = []
    slide_evaluations: list[Any] = []
    judge_calls: list[dict[str, Any]] = []
    global_evaluations: list[Any] = []
    judge_findings: list[dict[str, Any]] = []
    prompt_versions: dict[str, str] = {}
    judge_attempts: list[dict[str, Any]] = []
    judge_stability: float | None = None
    usage = {"requests_used": 0, "cache_hits": 0, "request_cap": cap}
    remote_metadata: dict[str, Any] = {}
    if args.profile != "static":
        # Remote orchestration is deliberately delegated to planner/judges. Keeping the
        # persisted run usable here means quota and provider failures never erase static facts.
        try:
            plan_summary = _remote_plan_summary(artifacts_value, args.profile, cap)
            _print_plan_summary(plan_summary)
            remote_result = _run_remote(
                artifacts_value,
                profile=args.profile,
                output=output,
                cap=cap,
                resume=args.resume,
                deterministic_findings=deterministic_findings,
            )
            block_evaluations = _evaluation_list(remote_result.get("blocks"))
            section_evaluations = _evaluation_list(remote_result.get("sections"))
            slide_evaluations = _attach_system_confidence(
                _evaluation_list(remote_result.get("slides")), artifacts_value
            )
            global_evaluations = _evaluation_list(remote_result.get("global"))
            judge_calls = list(remote_result.get("calls", []))
            judge_attempts = list(remote_result.get("attempts", []))
            prompt_versions = dict(remote_result.get("prompt_versions", {}))
            remote_usage = remote_result.get("usage", {})
            usage.update(remote_usage)
            usage["requests_used"] = remote_usage.get(
                "requests_used", remote_usage.get("requests", 0)
            )
            usage["cache_hits"] = sum(call.get("cached", False) for call in judge_calls)
            usage["remote_judgments_used"] = len(judge_calls)
            usage["new_remote_requests"] = remote_usage.get(
                "requests_used",
                remote_usage.get(
                    "requests",
                    sum(not bool(call.get("cached")) for call in judge_calls),
                ),
            )
            if not judge_attempts:
                judge_attempts = [
                    {
                        **call,
                        "status": "cache_hit" if call.get("cached") else "succeeded",
                    }
                    for call in judge_calls
                ]
            missing_attempts = max(
                0,
                int(usage["new_remote_requests"])
                - sum(not bool(attempt.get("cached")) for attempt in judge_attempts),
            )
            judge_attempts.extend(
                {
                    "status": "failed_unreported",
                    "attempt_index": len(judge_attempts) + index + 1,
                    "warning": "Runner reported physical usage without attempt details.",
                }
                for index in range(missing_attempts)
            )
            remote_metadata = {
                "coverage": remote_result.get("coverage", {}),
                "stability": remote_result.get("stability", {}),
            }
            incomplete_reasons.extend(remote_result.get("incomplete_reasons", []))
            incomplete_reasons.extend(_failed_batch_reasons(remote_result))
            judge_stability = _measured_stability(
                remote_result.get(
                    "stability_checks",
                    remote_result.get("stability", remote_result.get("judge_stability")),
                )
            )
            judge_findings = _remote_issues(
                [
                    *block_evaluations,
                    *section_evaluations,
                    *slide_evaluations,
                    *global_evaluations,
                ]
            )
        except Exception as exc:
            incomplete_reasons.append(f"{_remote_placeholder_reason()} Cause: {exc}")

    if args.profile == "static":
        # Static is a complete profile even though semantic dimensions are intentionally absent.
        incomplete_reasons = [
            reason for reason in incomplete_reasons if "Artifact evaluation failed" in reason
        ]
    dimension_scores = _global_metrics(global_evaluations)
    unreported_model_calls = [
        call
        for call in judge_calls
        if not call.get("actual_model_reported", bool(call.get("actual_model")))
        or not call.get("actual_model")
    ]
    release_capable = not (
        args.profile in {"standard", "deep"} and unreported_model_calls
    )
    evaluation = aggregate_evaluation(
        deterministic_findings=deterministic_findings,
        judge_findings=judge_findings,
        block_evaluations=block_evaluations,
        section_evaluations=section_evaluations,
        slide_evaluations=slide_evaluations,
        dimension_scores=dimension_scores,
        profile=args.profile,
        incomplete_reasons=incomplete_reasons,
        judge_stability=judge_stability,
        usage=usage,
        blocks_inspected=len(_items(artifacts_value, "blocks")),
        sections_inspected=len(_items(artifacts_value, "sections")),
        slides_inspected=len(_items(artifacts_value, "slides")),
        coverage=remote_metadata.get("coverage"),
        stability=remote_metadata.get("stability"),
        release_capable=release_capable,
    )
    completed = datetime.now(UTC)
    manifest = {
        "schema_version": "1",
        "profile": args.profile,
        "result": {"path": str(args.result), "sha256": _hash_file(args.result)},
        "slides": {"path": str(args.slides), "sha256": _hash_file(args.slides)}
        if args.slides
        else None,
        "evaluator_commit": _commit(),
        "models": _model_policy() if args.profile != "static" else {},
        "models_actually_returned": sorted(
            {
                str(call["actual_model"])
                for call in judge_calls
                if call.get("actual_model")
                and call.get("actual_model_reported", True)
            }
        ),
        "actual_model_unreported_count": len(unreported_model_calls),
        "actual_model_unreported_calls": [
            {
                "kind": call.get("kind"),
                "cache_key": call.get("cache_key"),
                "requested_model": call.get("requested_model"),
            }
            for call in unreported_model_calls
        ],
        "release_capable": release_capable,
        "request_budget": cap,
        "estimated_requests": usage.get("estimated_requests"),
        "actual_requests": usage.get("requests_used", 0),
        "remote_judgments_used": usage.get("remote_judgments_used", 0),
        "new_remote_requests": usage.get("new_remote_requests", 0),
        "cache_hits": usage.get("cache_hits", 0),
        "token_usage": {
            key: value
            for key, value in usage.items()
            if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
        },
        "resume": bool(args.resume),
        "remote_llm_used": (
            usage.get("new_remote_requests", 0) > 0
            or usage.get("remote_judgments_used", 0) > 0
        ),
        "remote_provenance": [
            {
                "kind": call.get("kind"),
                "requested_model": call.get("requested_model"),
                "actual_model": call.get("actual_model"),
                "actual_model_reported": call.get("actual_model_reported", True),
                "normalization_warnings": call.get("normalization_warnings", []),
                "cache_key": call.get("cache_key"),
                "cached": bool(call.get("cached")),
            }
            for call in judge_calls
        ],
        "remote_attempt_provenance": _jsonable(judge_attempts),
        "status": evaluation["status"],
        "incomplete_reasons": evaluation["incomplete_reasons"],
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat(),
        "prompt_versions": prompt_versions,
    }
    serialized_findings = [_jsonable(item) for item in deterministic_findings]
    write_json(output / "deterministic-findings.json", serialized_findings)
    write_json(output / "block-evaluations.json", _jsonable(block_evaluations))
    write_json(output / "section-evaluations.json", _jsonable(section_evaluations))
    write_json(output / "slide-evaluations.json", _jsonable(slide_evaluations))
    write_jsonl(output / "judge-calls.jsonl", _jsonable(judge_calls))
    write_jsonl(output / "judge-attempts.jsonl", _jsonable(judge_attempts))
    write_json(output / "evaluation.json", evaluation)
    write_json(output / "manifest.json", manifest)
    write_report(output / "report.md", evaluation, manifest)
    print(
        f"Evaluation {evaluation['status']}: {evaluation['verdict']} "
        f"({evaluation['overall_score'] if evaluation['overall_score'] is not None else 'n/a'})"
    )
    print(f"Report: {output / 'report.md'}")
    return 0


def _run_remote(
    artifacts_value: Any,
    *,
    profile: str,
    output: Path,
    cap: int,
    resume: bool,
    deterministic_findings: Iterable[Any] = (),
) -> dict[str, Any]:
    """Call the remote subsystem through its public planner/runner interface.

    The runner is optional during phase-one/static development. A missing runner becomes an
    explicit incomplete report, never a fabricated semantic score.
    """
    from lecturelog.evaluation import judges, planner

    runner = getattr(judges, "run_planned_evaluation", None)
    if runner is None:
        raise RuntimeError("remote judge runner is not available")
    kwargs = {
        "artifacts": artifacts_value,
        "profile": profile,
        "max_requests": cap,
        "cache_dir": output / "cache",
        "resume": resume,
        "allow_remote": lambda: True,
        "api_key": os.environ.get("OPENROUTER_API_KEY", ""),
        "deterministic_findings": list(deterministic_findings),
    }
    result = runner(**kwargs)
    if hasattr(result, "__await__"):
        import asyncio

        result = asyncio.run(result)
    serialized = dict(_jsonable(result))
    estimated = len(
        planner.plan_judge_batches(
            profile,
            blocks=_items(artifacts_value, "blocks"),
            sections=_items(artifacts_value, "sections"),
            slides=_items(artifacts_value, "slides"),
            alignment=_field(artifacts_value, "alignment"),
        )
    )
    serialized.setdefault("usage", {})["estimated_requests"] = min(estimated, cap)
    serialized.setdefault(
        "prompt_versions", {"judge": getattr(judges, "PROMPT_VERSION", "unknown")}
    )
    return serialized


def main(argv: Iterable[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "evaluate":
            return _run_evaluate(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    sys.exit(main())
