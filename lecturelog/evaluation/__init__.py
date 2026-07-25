"""Offline quality evaluation for generated LectureLog artifacts."""

from lecturelog.evaluation.aggregation import aggregate_evaluation
from lecturelog.evaluation.artifacts import ArtifactLoadError, load_evaluation_artifacts
from lecturelog.evaluation.deterministic import run_deterministic_checks
from lecturelog.evaluation.models import EvaluationArtifacts, Finding, Severity
from lecturelog.evaluation.reporting import render_markdown_report

__all__ = [
    "ArtifactLoadError",
    "EvaluationArtifacts",
    "Finding",
    "Severity",
    "aggregate_evaluation",
    "load_evaluation_artifacts",
    "render_markdown_report",
    "run_deterministic_checks",
]
