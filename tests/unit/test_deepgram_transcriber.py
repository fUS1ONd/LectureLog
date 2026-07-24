from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from lecturelog.infrastructure.transcribe import deepgram_transcriber as mod


def _payload() -> dict:
    return {
        "metadata": {"duration": 3.5, "request_id": "req"},
        "results": {
            "utterances": [
                {
                    "start": 0,
                    "end": 3.5,
                    "transcript": "Привет, мир.",
                    "words": [
                        {
                            "start": 0,
                            "end": 1,
                            "word": "привет",
                            "punctuated_word": "Привет,",
                        },
                        {"start": 1, "end": 3.5, "word": "мир", "punctuated_word": "мир."},
                    ],
                }
            ],
            "channels": [{"alternatives": [{"transcript": "Привет, мир."}]}],
        },
    }


async def test_streams_file_and_builds_srt(tmp_path, monkeypatch):
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        seen["headers"] = request.headers
        seen["body"] = await request.aread()
        return httpx.Response(200, json=_payload())

    async def fake_probe(path: Path) -> float:
        return 4.0

    monkeypatch.setattr(mod, "probe_audio_seconds", fake_probe)
    audio = tmp_path / "lecture.mp3"
    audio.write_bytes(b"audio-data")
    progress: list[int] = []
    usage: list[dict] = []
    transcriber = mod.DeepgramTranscriber(
        api_key="test-secret",
        transport=httpx.MockTransport(handler),
    )
    result = await transcriber.transcribe(
        audio,
        tmp_path / "out",
        on_progress=progress.append,
        on_usage=usage.append,
    )
    assert seen["body"] == b"audio-data"
    assert seen["headers"]["authorization"] == "Token test-secret"
    assert seen["headers"]["content-length"] == str(len(b"audio-data"))
    assert seen["query"]["model"] == "nova-3"
    assert seen["query"]["language"] == "ru"
    assert "detect_language" not in seen["query"]
    assert seen["query"]["mip_opt_out"] == "true"
    assert seen["query"]["utterances"] == "true"
    assert result.read_text() == "1\n00:00:00,000 --> 00:00:03,500\nПривет, мир."
    assert progress == [5, 10, 20, 30, 40, 50, 60, 70, 90, 100]
    assert [item["audio_seconds"] for item in usage] == [4, 3]


async def test_detect_language_omits_fixed_language(tmp_path, monkeypatch):
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        await request.aread()
        return httpx.Response(200, json=_payload())

    async def fake_probe(path: Path) -> float:
        return 4.0

    monkeypatch.setattr(mod, "probe_audio_seconds", fake_probe)
    audio = tmp_path / "lecture.mp3"
    audio.write_bytes(b"audio")
    transcriber = mod.DeepgramTranscriber(
        api_key="secret",
        language="ru",
        detect_language=True,
        transport=httpx.MockTransport(handler),
    )
    await transcriber.transcribe(audio, tmp_path / "out")
    assert seen["detect_language"] == "true"
    assert "language" not in seen


async def test_retries_503_with_repeatable_body(tmp_path, monkeypatch):
    bodies: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(await request.aread())
        if len(bodies) == 1:
            return httpx.Response(503, json={"err_code": "UNAVAILABLE"})
        return httpx.Response(200, json=_payload())

    async def no_sleep(delay: float) -> None:
        return None

    async def fake_probe(path: Path) -> float:
        return 1.0

    monkeypatch.setattr(mod, "probe_audio_seconds", fake_probe)
    audio = tmp_path / "lecture.wav"
    audio.write_bytes(b"repeat-me")
    transcriber = mod.DeepgramTranscriber(
        api_key="secret",
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
    )
    await transcriber.transcribe(audio, tmp_path / "out")
    assert bodies == [b"repeat-me", b"repeat-me"]


@pytest.mark.parametrize("status", [413, 415, 422])
async def test_input_errors_become_value_error(tmp_path, monkeypatch, status):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"err_code": "ASR_UNPROCESSABLE"})

    async def fake_probe(path: Path) -> float:
        return 1.0

    monkeypatch.setattr(mod, "probe_audio_seconds", fake_probe)
    audio = tmp_path / "bad.mp3"
    audio.write_bytes(b"bad")
    transcriber = mod.DeepgramTranscriber(
        api_key="secret",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError, match="входное аудио"):
        await transcriber.transcribe(audio, tmp_path / "out")


async def test_auth_error_is_sanitized_runtime_error(tmp_path, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="secret provider body")

    async def fake_probe(path: Path) -> float:
        return 1.0

    monkeypatch.setattr(mod, "probe_audio_seconds", fake_probe)
    audio = tmp_path / "lecture.mp3"
    audio.write_bytes(b"audio")
    transcriber = mod.DeepgramTranscriber(
        api_key="secret",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RuntimeError) as caught:
        await transcriber.transcribe(audio, tmp_path / "out")
    assert "secret" not in str(caught.value)


def test_long_utterance_splits_by_words_and_clamps_duration():
    words = [
        {"start": i, "end": i + 0.8, "word": f"w{i}", "punctuated_word": f"W{i}"} for i in range(13)
    ]
    payload = {
        "metadata": {"duration": 10},
        "results": {
            "utterances": [{"start": 0, "end": 13, "transcript": "ignored", "words": words}]
        },
    }
    captions, duration = mod.build_captions(payload, 20)
    assert duration == 10
    assert len(captions) == 1
    assert captions[0].end == 10
    assert captions[0].text.startswith("W0")


def test_empty_transcript_produces_empty_srt():
    captions, _ = mod.build_captions(
        {"metadata": {"duration": 1}, "results": {"channels": [{"alternatives": [{}]}]}},
        1,
    )
    assert captions == []
