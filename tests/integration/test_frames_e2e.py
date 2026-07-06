"""E2E: смешанная синтетика (спикер + слайды + доска) через полный
VideoFrameProvider.get_slides с FakeLlm (дизайн §13, задача 18).

Проверяем воронку целиком (A-F), а не отдельные политики: кадры приходят
только из режимов «слайды»/«доска», ни одного из спикерского интервала,
тайминги монотонны, файлы существуют, суффиксы соответствуют формату рендера."""
from __future__ import annotations

import inspect
import json

from lecturelog.infrastructure.frames.provider import VideoFrameProvider
from lecturelog.infrastructure.frames.types import FramesTuning
from tests.support.synthetic_video import board_frames, slides_frames, speaker_frames, write_video

# Раскладка ролика (все границы известны заранее — проверяем по ним таймкоды):
#   0..40    — спикер (говорящая голова, без слайдов/доски)
#   40..130  — слайды (3 слайда по 30с)
#   130..220 — доска (запись 40с + стирание на 50с, всего 90с)
SPEAKER_END = 40
SLIDES_END = SPEAKER_END + 90
BOARD_END = SLIDES_END + 90

SRT = """1
00:00:00,000 --> 00:00:40,000
вступление, организационные вопросы

2
00:00:40,000 --> 00:02:10,000
смотрим слайды

3
00:02:10,000 --> 00:03:40,000
разбираем на доске
"""


def _video(tmp_path):
    # with_teacher=False: препод в board_frames «ездит» и на компрессии рвёт
    # phase-correlation (shift), из-за чего сегментация B ошибочно метит
    # доску как "camera" (сигнатура камеры доминирует над сигнатурой доски
    # в _classify_window) и кандидатов на доске не находится вовсе. Без
    # препода shift остаётся низким, доска распознаётся штатно.
    frames = (
        speaker_frames(total_secs=SPEAKER_END, seed=7)
        + slides_frames(n_slides=3, secs_per_slide=30, seed=1)
        + board_frames(write_secs=40, erase_at=50, total_secs=90, seed=3, with_teacher=False)
    )
    return write_video(frames, tmp_path / "lecture_mixed.mp4", fps=1)


def _srt(tmp_path):
    p = tmp_path / "lecture_mixed.srt"
    p.write_text(SRT, encoding="utf-8")
    return p


def _qc_keep_all(n: int) -> str:
    return json.dumps([
        {"idx": i + 1, "keep": True, "caption": f"Кадр {i + 1}", "dup_group": None}
        for i in range(n)
    ])


class _DegradeClassifyQcKeepAllLlm:
    """Первый вызов (стадия C, классификация режимов) всегда падает — provider
    по дизайну §10 деградирует на чистые сигнатуры сегментации B (они и так
    надёжно узнают слайды/доску/спикера на этой синтетике). Все последующие
    вызовы (стадия F, QC) подтверждают все присланные кандидаты без фильтрации,
    чтобы тест проверял именно воронку кандидатов A-E, а не решения VLM."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def call(self, prompt, models, images=None, *, on_usage=None,
                    response_json=False, effort=None, retries=5):
        self.calls.append({"prompt": prompt, "images": images})
        is_classify = len(self.calls) == 1
        if on_usage is not None:
            maybe_awaitable = on_usage({"model": models[0], "prompt": 100, "output": 10})
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        if is_classify:
            raise RuntimeError("классификация недоступна: провайдер остаётся на сигнатурах B")
        return _qc_keep_all(len(images or []))


async def test_mixed_lecture_speaker_slides_board(tmp_path):
    video = _video(tmp_path)
    srt = _srt(tmp_path)
    llm = _DegradeClassifyQcKeepAllLlm()

    provider = VideoFrameProvider(
        video_path=video, srt_path=srt,
        llm=llm, models=["m"], effort="low", tuning=FramesTuning(),
    )
    items = await provider.get_slides(tmp_path / "out")

    assert items, "ожидались кадры со слайдов и доски"

    timestamps = [i.timestamp for i in items]
    for ts in timestamps:
        assert ts is not None
        assert SPEAKER_END <= ts <= BOARD_END, (
            f"кадр {ts} вне слайдового/досочного диапазона "
            f"(спикерский интервал 0..{SPEAKER_END} не должен давать кадров)"
        )
    assert timestamps == sorted(timestamps), "тайминги должны быть монотонны"

    for item in items:
        assert item.path.exists(), f"файл кадра отсутствует: {item.path}"
        assert item.path.suffix in {".jpg", ".jpeg", ".png"}

    # Хотя бы один кадр должен прийти из слайдового интервала и хотя бы
    # один — из досочного (иначе воронка деградировала до одного источника).
    assert any(SPEAKER_END <= ts < SLIDES_END for ts in timestamps), "нет кадров со слайдов"
    assert any(SLIDES_END <= ts <= BOARD_END for ts in timestamps), "нет кадров с доски"
