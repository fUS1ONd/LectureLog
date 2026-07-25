"""Human-readable evaluator reports and atomic artifact persistence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_jsonl(path: Path, values: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for value in values:
                stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
                stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _format_score(value: Any) -> str:
    return "not evaluated" if value is None else f"{float(value):.1f}/100"


def _cell(value: Any, limit: int = 180) -> str:
    if value is None:
        return "—"
    if isinstance(value, list):
        value = "; ".join(
            str(item.get("quote") or item.get("message") or item)
            if isinstance(item, dict)
            else str(item)
            for item in value
        )
    text = " ".join(str(value).split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace("|", "\\|")


def _issues(item: dict[str, Any]) -> str:
    issues = item.get("issues") or item.get("findings") or []
    return _cell(
        [
            f"{issue.get('severity', 'info')}:{issue.get('code', 'unknown')} "
            f"{issue.get('message', '')}"
            if isinstance(issue, dict)
            else issue
            for issue in issues
        ]
    )


def _evidence(item: dict[str, Any]) -> str:
    return _cell(item.get("evidence") or item.get("transcript_evidence") or [])


def _render_drill_down(kind: str, items: list[dict[str, Any]]) -> list[str]:
    title = kind.title()
    lines = ["", f"### {title}", ""]
    if not items:
        return [*lines, f"No {kind.lower()} were remotely evaluated."]
    lines.extend(
        [
            "| ID | Score | Verdict / key scores | Issues | Evidence excerpt |",
            "| --- | ---: | --- | --- | --- |",
        ]
    )
    for item in items:
        stable_id = (
            item.get("stable_id")
            or item.get("block_id")
            or item.get("section_id")
            or item.get("slide_num")
            or "unknown"
        )
        key_scores = item.get("placement_verdict") or ", ".join(
            f"{key}={value}"
            for key, value in item.items()
            if key
            in {
                "faithfulness",
                "language_consistency",
                "content_coverage",
                "document_structure",
                "semantic_relevance",
                "anchor_precision",
            }
        )
        lines.append(
            f"| `{_cell(stable_id)}` | {_cell(item.get('score'))} | "
            f"{_cell(key_scores)} | {_issues(item)} | {_evidence(item)} |"
        )
    return lines


def render_markdown_report(evaluation: dict[str, Any], manifest: dict[str, Any]) -> str:
    status = str(evaluation.get("status", "incomplete"))
    verdict = str(evaluation.get("verdict", "evaluation_inconclusive"))
    lines = [
        "# Lecture quality evaluation",
        "",
        f"**Verdict:** `{verdict}`  ",
        f"**Status:** `{status}`  ",
        f"**Overall score:** {_format_score(evaluation.get('overall_score'))}",
        "",
        "## Scorecard",
        "",
        "| Dimension | Score |",
        "| --- | ---: |",
    ]
    for name, score in evaluation.get("scorecard", {}).items():
        lines.append(f"| {name.replace('_', ' ').title()} | {_format_score(score)} |")

    lines.extend(
        [
            "",
            "## Quality gates",
            "",
            "> Thresholds are provisional until calibrated on several real lectures.",
            "",
            "| Gate | Status | Actual | Requirement |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for gate in evaluation.get("quality_gates", []):
        actual = "unknown" if gate.get("actual") is None else str(gate["actual"])
        lines.append(
            f"| {gate['label']} | **{str(gate['status']).upper()}** | {actual} | "
            f"{gate['operator']} {gate['threshold']} |"
        )

    lines.extend(["", "## Highest-impact findings", ""])
    findings = evaluation.get("highest_impact_findings", [])
    if findings:
        for finding in findings:
            code = finding.get("code", finding.get("issue_code", "unknown"))
            severity = str(finding.get("severity", "info")).upper()
            message = finding.get("message") or finding.get("detail") or "No details."
            lines.append(f"- **{severity} · `{code}`:** {message}")
    else:
        lines.append("No findings were produced.")

    lines.extend(["", "## Run and model usage", ""])
    models = manifest.get("models", {})
    if models:
        for role, model in models.items():
            lines.append(f"- {role}: `{model}`")
    else:
        lines.append("- Remote models: not used")
    usage = evaluation.get("usage", {})
    lines.append(f"- Requests used: {usage.get('requests_used', 0)}")
    lines.append(
        f"- Successful/cached remote judgments used: "
        f"{manifest.get('remote_judgments_used', 0)}"
    )
    lines.append(
        f"- New physical remote requests attempted: "
        f"{manifest.get('new_remote_requests', 0)}"
    )
    lines.append(f"- Cache hits: {usage.get('cache_hits', 0)}")
    lines.append(f"- Remote processing: {'yes' if manifest.get('remote_llm_used') else 'no'}")
    lines.append(
        f"- Release-capable provenance: "
        f"{'yes' if manifest.get('release_capable', True) else 'no'}"
    )
    unreported = manifest.get("actual_model_unreported_count", 0)
    if unreported:
        lines.append(
            f"- **Provenance warning:** provider did not report the actual model for "
            f"{unreported} judgment(s)."
        )
    normalization_warnings = [
        warning
        for call in manifest.get("remote_provenance", [])
        for warning in call.get("normalization_warnings", [])
    ]
    if normalization_warnings:
        lines.append(
            f"- **Normalization warnings:** {_cell(normalization_warnings, limit=500)}"
        )
    failed_attempts = [
        attempt
        for attempt in manifest.get("remote_attempt_provenance", [])
        if attempt.get("status") not in {"succeeded", "success", "cache_hit"}
    ]
    if failed_attempts:
        lines.append(
            f"- **Attempt provenance warning:** {len(failed_attempts)} physical attempt(s) "
            f"failed or lack complete runner details."
        )

    if status != "complete":
        lines.extend(["", "## Incomplete evaluation", ""])
        reasons = evaluation.get("incomplete_reasons") or ["Unspecified missing evaluation data."]
        lines.extend(f"- {reason}" for reason in reasons)

    lines.extend(["", "## Stability and limitations", ""])
    stability = evaluation.get("judge_stability")
    lines.append(f"- Judge stability: {stability if stability is not None else 'not measured'}")
    limitations = evaluation.get("limitations", [])
    lines.extend(f"- {limitation}" for limitation in limitations)
    if not limitations:
        lines.append("- No additional limitations recorded.")

    counts = evaluation.get("counts", {})
    lines.extend(
        [
            "",
            "## Drill-down coverage",
            "",
            f"- Deterministic findings: {counts.get('deterministic_findings', 0)}",
            f"- Judge findings: {counts.get('judge_findings', 0)}",
            f"- Blocks inspected locally: {counts.get('blocks_inspected', 0)}",
            f"- Blocks evaluated: {counts.get('blocks_evaluated', 0)}",
            f"- Sections inspected locally: {counts.get('sections_inspected', 0)}",
            f"- Sections evaluated: {counts.get('sections_evaluated', 0)}",
            f"- Slides inspected locally: {counts.get('slides_inspected', 0)}",
            f"- Slides evaluated: {counts.get('slides_evaluated', 0)}",
        ]
    )
    coverage = evaluation.get("coverage", {})
    for kind in ("blocks", "sections", "slides"):
        record = coverage.get(kind, {})
        if record:
            lines.append(
                f"- {kind.title()} coverage: {record.get('evaluated', 0)}/"
                f"{record.get('total', 0)} "
                f"({'full' if record.get('complete') else 'sampled'})"
            )
    drill_down = evaluation.get("drill_down", {})
    lines.extend(["", "## Evaluated-item drill-down"])
    lines.extend(_render_drill_down("blocks", drill_down.get("blocks", [])))
    lines.extend(_render_drill_down("sections", drill_down.get("sections", [])))
    lines.extend(_render_drill_down("slides", drill_down.get("slides", [])))
    lines.extend(
        [
            "",
            "Complete machine-readable details are available in the per-kind JSON files.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(path: Path, evaluation: dict[str, Any], manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_markdown_report(evaluation, manifest)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
