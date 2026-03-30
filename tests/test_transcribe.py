import asyncio

import pytest

from lecturelog.pipeline.transcribe import _build_srt_from_words


def test_build_srt_from_words_groups_by_seven() -> None:
    words = [
        {"start": 0.0, "end": 0.2, "word": "один"},
        {"start": 0.2, "end": 0.4, "word": "два"},
        {"start": 0.4, "end": 0.6, "word": "три"},
        {"start": 0.6, "end": 0.8, "word": "четыре"},
        {"start": 0.8, "end": 1.0, "word": "пять"},
        {"start": 1.0, "end": 1.2, "word": "шесть"},
        {"start": 1.2, "end": 1.4, "word": "семь"},
        {"start": 1200.0, "end": 1200.2, "word": "восемь"},
    ]

    srt = _build_srt_from_words(words, words_per_caption=7)

    assert "1\n00:00:00,000 --> 00:00:01,400\nодин два три четыре пять шесть семь" in srt
    assert "2\n00:20:00,000 --> 00:20:00,200\nвосемь" in srt


def test_build_srt_from_words_returns_empty_for_no_words() -> None:
    assert _build_srt_from_words([]) == ""


@pytest.mark.anyio
async def test_groq_key_pool_round_robin():
    from lecturelog.pipeline.transcribe import GroqKeyPool
    pool = GroqKeyPool(["key1", "key2"])
    k1 = await pool.acquire()
    k2 = await pool.acquire()
    k3 = await pool.acquire()
    assert k1 == "key1"
    assert k2 == "key2"
    assert k3 == "key1"


@pytest.mark.anyio
async def test_groq_key_pool_skip_blocked():
    from lecturelog.pipeline.transcribe import GroqKeyPool
    pool = GroqKeyPool(["key1", "key2"])
    pool.mark_rate_limited(0)
    k = await pool.acquire()
    assert k == "key2"


def test_groq_key_pool_empty_raises():
    from lecturelog.pipeline.transcribe import GroqKeyPool
    with pytest.raises(ValueError):
        GroqKeyPool([])

