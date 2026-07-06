from pathlib import Path

from lecturelog.config.settings import get_config
from scripts.frames_debug import _build_llm, _parse_args


def _set_required_env(monkeypatch, **overrides):
    base = {
        "GROQ_API_KEYS": "g1,g2",
        "OPENROUTER_API_KEY": "or-k1",
        "DATABASE_URL": "postgresql+asyncpg://u:p@db:5432/lecturelog",
        "S3_INTERNAL_ENDPOINT": "http://minio:9000",
        "S3_BUCKET": "lectures",
        "S3_ACCESS_KEY": "ak",
        "S3_SECRET_KEY": "sk",
    }
    base.update(overrides)
    for key, value in base.items():
        monkeypatch.setenv(key, value)
    get_config.cache_clear()


def _stub_openai(monkeypatch):
    class DummyAsyncOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("openai.AsyncOpenAI", DummyAsyncOpenAI)


def test_build_llm_uses_dedicated_classify_env(monkeypatch):
    _stub_openai(monkeypatch)
    _set_required_env(
        monkeypatch,
        LLM_MODELS_VIDEO_SLIDES="qc-a, qc-b",
        LLM_EFFORT_VIDEO_SLIDES="low",
        LLM_MODELS_FRAMES_CLASSIFY="classify-a, classify-b",
        LLM_EFFORT_FRAMES_CLASSIFY="medium",
    )
    try:
        _llm, models, effort, classify_models, classify_effort = _build_llm(None, None, None, None)
    finally:
        get_config.cache_clear()

    assert models == ["qc-a", "qc-b"]
    assert effort == "low"
    assert classify_models == ["classify-a", "classify-b"]
    assert classify_effort == "medium"


def test_build_llm_cli_overrides_can_split_qc_and_classify(monkeypatch):
    _stub_openai(monkeypatch)
    _set_required_env(monkeypatch)
    try:
        _llm, models, effort, classify_models, classify_effort = _build_llm(
            "qc-a, qc-b",
            "low",
            "classify-a, classify-b",
            "high",
        )
    finally:
        get_config.cache_clear()

    assert models == ["qc-a", "qc-b"]
    assert effort == "low"
    assert classify_models == ["classify-a", "classify-b"]
    assert classify_effort == "high"


def test_parse_args_accepts_classify_overrides():
    args = _parse_args(
        [
            "lecture.mp4",
            "lecture.srt",
            "out",
            "--classify-models",
            "classify-a,classify-b",
            "--classify-effort",
            "medium",
        ]
    )

    assert args.video == Path("lecture.mp4")
    assert args.classify_models == "classify-a,classify-b"
    assert args.classify_effort == "medium"
