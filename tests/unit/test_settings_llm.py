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


def test_llm_config_max_tokens_default_matches_model_ceiling(monkeypatch):
    """Потолок ответа по умолчанию — предел моделей Gemini, обрезка ответа нам не нужна."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert LlmConfig().max_tokens == 65536


def test_llm_config_max_tokens_is_configurable(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("LLM_MAX_TOKENS", "8192")
    assert LlmConfig().max_tokens == 8192


def test_slide_match_effort_defaults_to_low(monkeypatch):
    """Структурированные вызовы матчера: reasoning мешает соблюдать схему, держим low."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("LLM_EFFORT_SUBSPLIT", "medium")
    cfg = LlmConfig()
    assert cfg.effort_slide_match == "low"
    assert cfg.effort_subsplit == "medium"


def test_slide_match_models_default_to_subsplit(monkeypatch):
    """Без явной настройки матчер работает на тех же моделях, что и раньше."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("LLM_MODELS_SLIDE_MATCH", raising=False)
    monkeypatch.setenv("LLM_MODELS_SUBSPLIT", "google/gemini-3.6-flash,google/gemini-3.5-flash")

    cfg = LlmConfig()

    assert cfg.slide_match_models == cfg.subsplit_models


def test_slide_match_models_can_be_set_independently(monkeypatch):
    """У каталога слайдов своя ротация: Gemini рвёт батчи по RECITATION, gemma — нет."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("LLM_MODELS_SUBSPLIT", "google/gemini-3.6-flash")
    monkeypatch.setenv(
        "LLM_MODELS_SLIDE_MATCH",
        "google/gemini-3.6-flash,google/gemini-3.5-flash-lite,google/gemma-4-31b-it:free",
    )

    cfg = LlmConfig()

    assert cfg.slide_match_models == [
        "google/gemini-3.6-flash",
        "google/gemini-3.5-flash-lite",
        "google/gemma-4-31b-it:free",
    ]
    assert cfg.subsplit_models == ["google/gemini-3.6-flash"]
