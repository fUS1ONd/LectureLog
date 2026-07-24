from lecturelog.config.settings import LlmConfig


def test_llm_config_reads_openrouter_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv(
        "LLM_MODELS_SPLIT",
        "google/gemini-3.6-flash,google/gemini-3.5-flash,google/gemini-3.5-flash-lite",
    )
    cfg = LlmConfig()
    assert cfg.openrouter_key == "sk-or-test"
    assert cfg.base_url == "https://openrouter.ai/api/v1"  # дефолт
    assert cfg.split_models == [
        "google/gemini-3.6-flash",
        "google/gemini-3.5-flash",
        "google/gemini-3.5-flash-lite",
    ]


def test_llm_config_effort_per_stage_defaults(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    cfg = LlmConfig()
    # effort по стадиям — строки low|medium|high, дефолты подобраны в дизайне
    assert cfg.effort_split in ("low", "medium", "high")
    assert cfg.effort_render in ("low", "medium", "high")
    # усиленная проверка: конкретный дефолт, а не просто принадлежность множеству
    assert cfg.effort_split == "medium"
    assert cfg.effort_render == "medium"
