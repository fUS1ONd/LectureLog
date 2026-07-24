from __future__ import annotations


class UsageAccumulator:
    """Накопитель расхода ресурсов по стадиям с двухуровневым расслоением.

    Каждая стадия хранит нейтральное зерно + пустой `raw`. Зерно LLM-стадий —
    разбивка `by_model[model]` (а не плоская сумма). `total` вычисляется движком
    суммой по стадиям и несёт две оси режима (source, slides_origin).

    Деньги/кредиты/цены и пер-call записи сознательно не хранятся.
    """

    def __init__(self) -> None:
        self.usage: dict = {}
        self._source: str = "audio"
        self._slides_origin: str = "none"

    def record_transcribe(self, payload: dict) -> None:
        """Зерно транскрибации: audio_seconds (из ffprobe), provider, model."""
        self.usage["transcribe"] = {
            "audio_seconds": int(payload.get("audio_seconds", 0)),
            "provider": payload.get("provider", "unknown"),
            "model": payload.get("model"),
            "raw": {},
        }

    def record_llm(self, stage: str, payload: dict) -> None:
        """Инкремент по стадии×модели: prompt, output, calls. provider=gemini."""
        stage_usage = self.usage.setdefault(
            stage, {"provider": "gemini", "by_model": {}, "raw": {}}
        )
        model = payload.get("model", "unknown")
        by_model = stage_usage["by_model"].setdefault(model, {"prompt": 0, "output": 0, "calls": 0})
        by_model["prompt"] += int(payload.get("prompt", 0))
        by_model["output"] += int(payload.get("output", 0))
        by_model["calls"] += 1

    def set_mode(self, source: str, slides_origin: str) -> None:
        """Две оси режима.

        source ∈ {audio, video}; slides_origin ∈ {none, document, video_extracted}.
        """
        self._source = source
        self._slides_origin = slides_origin

    def compute_total(self) -> None:
        """Пересчитать total суммой по стадиям. Звать после каждой стадии и в except."""
        audio_seconds = self.usage.get("transcribe", {}).get("audio_seconds", 0)
        gemini_prompt = 0
        gemini_output = 0
        for stage in ("structurize", "video_slides"):
            by_model = self.usage.get(stage, {}).get("by_model", {})
            for model_usage in by_model.values():
                gemini_prompt += model_usage.get("prompt", 0)
                gemini_output += model_usage.get("output", 0)
        self.usage["total"] = {
            "audio_seconds": audio_seconds,
            "gemini_prompt": gemini_prompt,
            "gemini_output": gemini_output,
            "source": self._source,
            "slides_origin": self._slides_origin,
        }
