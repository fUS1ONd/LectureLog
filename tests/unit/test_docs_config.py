from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_documented_env_examples_include_frames_classify_config():
    for relative in ("README.md", ".env.example", "deploy/env.core.example"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "LLM_MODELS_FRAMES_CLASSIFY" in text, relative
        assert "LLM_EFFORT_FRAMES_CLASSIFY" in text, relative
