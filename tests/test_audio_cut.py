import asyncio
from pathlib import Path

import lecturelog.pipeline.audio_cut as audio_cut_module
from lecturelog.models import Section
from lecturelog.pipeline.audio_cut import cut_audio


class _FakeProcess:
    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr


def test_cut_audio_normalizes_timestamps_and_reencodes_to_mp3(tmp_path, monkeypatch) -> None:
    audio_path = tmp_path / "lecture.m4a"
    audio_path.write_bytes(b"audio")
    output_dir = tmp_path / "audio"

    captured: dict[str, object] = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(audio_cut_module.asyncio, "create_subprocess_exec", fake_exec)

    result = asyncio.run(
        cut_audio(
            audio_path=audio_path,
            sections=[
                Section(
                    title="Intro",
                    start="00:00:00,820",
                    end="00:00:05,000",
                    content="",
                    slide_indices=[],
                )
            ],
            output_dir=output_dir,
        )
    )

    target = output_dir / "section_01.mp3"

    assert result == [target]
    assert captured["args"] == (
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-ss",
        "00:00:00.820",
        "-to",
        "00:00:05.000",
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(target),
    )
