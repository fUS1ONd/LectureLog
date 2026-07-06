from pathlib import Path

from lecturelog.infrastructure.transcribe.groq_transcriber import (
    WHISPER_MODEL,
    _build_srt_from_words,
    _emit_usage,
    _probe_audio_seconds,
)


def _clear_proxy_env(monkeypatch):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)


def test_build_srt_groups_words_into_captions():
    words = [{"word": f"w{i}", "start": float(i), "end": float(i) + 0.5} for i in range(8)]
    srt = _build_srt_from_words(words, words_per_caption=7)
    # 8 слов при 7/блок -> 2 блока
    assert "1\n" in srt and "2\n" in srt
    assert "w0" in srt and "w7" in srt
    assert "-->" in srt


def test_build_srt_empty_returns_empty():
    assert _build_srt_from_words([]) == ""


async def test_emit_usage_noop_on_none():
    # не должно падать при on_usage=None
    await _emit_usage(None, {"audio_seconds": 1})


async def test_emit_usage_passes_payload():
    captured: list[dict] = []

    async def cb(payload):
        captured.append(payload)

    await _emit_usage(cb, {"audio_seconds": 42, "provider": "groq", "model": WHISPER_MODEL})
    assert captured == [{"audio_seconds": 42, "provider": "groq", "model": WHISPER_MODEL}]


async def test_probe_returns_zero_when_ffprobe_missing(monkeypatch):
    # ffprobe отсутствует в системе -> create_subprocess_exec кидает FileNotFoundError.
    # Зонд best-effort: должен вернуть 0, а не ронять транскрибацию.
    import asyncio

    async def boom(*args, **kwargs):
        raise FileNotFoundError("ffprobe не найден")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    assert await _probe_audio_seconds(Path("/tmp/a.mp3")) == 0


async def test_probe_returns_zero_on_non_numeric_output(monkeypatch):
    # ffprobe вернул мусор/пустую строку -> float() кинет ValueError -> ожидаем 0.
    import asyncio

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"\n", b"")

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await _probe_audio_seconds(Path("/tmp/a.mp3")) == 0


async def test_probe_returns_zero_on_nonzero_returncode(monkeypatch):
    # ffprobe завершился с ошибкой (returncode != 0) -> зонд возвращает 0.
    import asyncio

    class FakeProc:
        returncode = 1

        async def communicate(self):
            return (b"", b"ffprobe error")

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await _probe_audio_seconds(Path("/tmp/a.mp3")) == 0


async def test_transcribe_emits_audio_seconds_usage(tmp_path, monkeypatch):
    from lecturelog.infrastructure.transcribe import groq_transcriber as mod

    _clear_proxy_env(monkeypatch)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")

    async def fake_segment(audio_path, output_dir):
        (output_dir / "chunk_000.mp3").write_bytes(b"x")

    async def fake_probe(audio_path):
        return 123

    async def fake_chunk(*args, **kwargs):
        return []

    monkeypatch.setattr(mod, "_run_ffmpeg_segment", fake_segment)
    monkeypatch.setattr(mod, "_probe_audio_seconds", fake_probe)
    monkeypatch.setattr(mod, "_transcribe_chunk", fake_chunk)

    captured: list[dict] = []

    async def on_usage(payload):
        captured.append(payload)

    transcriber = mod.GroqTranscriber(["k"])
    await transcriber.transcribe(audio, tmp_path / "out", on_usage=on_usage)

    assert captured == [{"audio_seconds": 123, "provider": "groq", "model": WHISPER_MODEL}]


async def test_transcribe_continues_when_probe_returns_zero(tmp_path, monkeypatch):
    # Best-effort: если зонд длительности вернул 0 (сбой ffprobe),
    # транскрибация не падает, а эмитит usage с audio_seconds=0.
    from lecturelog.infrastructure.transcribe import groq_transcriber as mod

    _clear_proxy_env(monkeypatch)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")

    async def fake_segment(audio_path, output_dir):
        (output_dir / "chunk_000.mp3").write_bytes(b"x")

    async def fake_probe(audio_path):
        return 0  # имитируем сбой зонда

    async def fake_chunk(*args, **kwargs):
        return []

    monkeypatch.setattr(mod, "_run_ffmpeg_segment", fake_segment)
    monkeypatch.setattr(mod, "_probe_audio_seconds", fake_probe)
    monkeypatch.setattr(mod, "_transcribe_chunk", fake_chunk)

    captured: list[dict] = []

    async def on_usage(payload):
        captured.append(payload)

    transcriber = mod.GroqTranscriber(["k"])
    srt_path = await transcriber.transcribe(audio, tmp_path / "out", on_usage=on_usage)

    assert srt_path.exists()
    assert captured == [{"audio_seconds": 0, "provider": "groq", "model": WHISPER_MODEL}]
