from pathlib import Path

import pytest

from lecturelog.domain.exceptions import MediaIngestError, MediaIngestReason
from lecturelog.domain.media_source import VideoUrlSource
from lecturelog.domain.ports import CookieStatus, CookieStore
from lecturelog.infrastructure.media.url_utils import VideoUrlKind
from lecturelog.infrastructure.media.video_ingestor import VideoIngestor


class SentinelCookieStore(CookieStore):
    async def save(self, content): ...

    async def get(self):
        raise AssertionError("cookie store must not be read")

    async def status(self):
        return CookieStatus(exists=False, updated_at=None, size=0)

    async def delete(self): ...


def _backend(argv: tuple[str, ...]) -> str | None:
    if "--extractor-args" not in argv:
        return None
    return argv[argv.index("--extractor-args") + 1].split("=")[-1]


def _fake_exec_factory(
    calls,
    *,
    preflight_codes=None,
    download_code=0,
    download_stderr=b"",
    output_mode="one",
):
    preflight_codes = preflight_codes or {}

    async def fake_exec(*argv, **kwargs):
        calls.append(argv)
        is_preflight = "--simulate" in argv

        class Process:
            returncode = preflight_codes.get(_backend(argv), 0) if is_preflight else download_code

            async def communicate(self):
                if is_preflight:
                    return b"", b"preflight failed" if self.returncode else b""
                if self.returncode:
                    return b"", download_stderr
                template = argv[argv.index("-o") + 1]
                output = Path(template.replace("%(ext)s", "mp4"))
                if output_mode == "zero":
                    return b"", b""
                if output_mode == "escape":
                    escaped = output.parent.parent / "escaped.mp4"
                    escaped.write_bytes(b"video")
                    return f"{escaped}\n".encode(), b""
                output.write_bytes(b"video")
                if output_mode == "two":
                    second = output.with_name("second.mp4")
                    second.write_bytes(b"video2")
                    return f"{output}\n{second}\n".encode(), b""
                return f"{output}\n".encode(), b""

        return Process()

    return fake_exec


@pytest.mark.asyncio
async def test_x_uses_graphql_preflight_then_one_cookie_free_download(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "lecturelog.infrastructure.media.video_ingestor.asyncio.create_subprocess_exec",
        _fake_exec_factory(calls),
    )
    ingestor = VideoIngestor(cookie_store=SentinelCookieStore(), target_resolution="720")

    result = await ingestor.ingest(
        VideoUrlSource(url="https://x.com/i/status/2078106556634124335"),
        tmp_path / "out",
    )

    assert result.name == "video.mp4"
    assert len(calls) == 2
    preflight, download = calls
    assert "--simulate" in preflight
    assert _backend(preflight) == "graphql"
    assert "--simulate" not in download
    assert _backend(download) == "graphql"
    assert "--cookies" not in download
    assert "--js-runtimes" not in download
    assert "--remote-components" not in download
    assert download[download.index("--playlist-items") + 1] == "1"
    assert download[download.index("-S") + 1] == "res:720,proto:https"


@pytest.mark.asyncio
async def test_x_falls_back_during_preflight_and_still_downloads_once(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "lecturelog.infrastructure.media.video_ingestor.asyncio.create_subprocess_exec",
        _fake_exec_factory(calls, preflight_codes={"graphql": 1, "syndication": 0}),
    )

    await VideoIngestor().ingest(
        VideoUrlSource(url="https://twitter.com/user/status/1"),
        tmp_path / "out",
    )

    assert [_backend(call) for call in calls] == ["graphql", "syndication", "syndication"]
    assert sum("--simulate" not in call for call in calls) == 1


@pytest.mark.asyncio
async def test_x_download_failure_does_not_retry_other_backend(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "lecturelog.infrastructure.media.video_ingestor.asyncio.create_subprocess_exec",
        _fake_exec_factory(
            calls,
            download_code=1,
            download_stderr=b"ERROR: No space left on device",
        ),
    )

    with pytest.raises(MediaIngestError) as caught:
        await VideoIngestor().ingest(
            VideoUrlSource(url="https://x.com/i/status/1"),
            tmp_path / "out",
        )

    assert caught.value.reason is MediaIngestReason.LOCAL_IO
    assert [_backend(call) for call in calls] == ["graphql", "graphql"]
    assert sum("--simulate" not in call for call in calls) == 1


@pytest.mark.asyncio
async def test_x_both_preflights_fail_without_download(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "lecturelog.infrastructure.media.video_ingestor.asyncio.create_subprocess_exec",
        _fake_exec_factory(calls, preflight_codes={"graphql": 1, "syndication": 1}),
    )

    with pytest.raises(MediaIngestError) as caught:
        await VideoIngestor().ingest(
            VideoUrlSource(url="https://x.com/i/status/1"),
            tmp_path / "out",
        )

    assert caught.value.reason is MediaIngestReason.EXTRACTOR
    assert len(calls) == 2
    assert all("--simulate" in call for call in calls)


@pytest.mark.asyncio
async def test_generic_does_not_read_youtube_cookies_and_best_is_unlimited(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "lecturelog.infrastructure.media.video_ingestor.asyncio.create_subprocess_exec",
        _fake_exec_factory(calls),
    )

    await VideoIngestor(
        cookie_store=SentinelCookieStore(),
        target_resolution="best",
    ).ingest(
        VideoUrlSource(url="https://cdn.example/video"),
        tmp_path / "out",
    )

    assert len(calls) == 1
    assert calls[0][calls[0].index("-S") + 1] == "res,proto:https"
    assert "--cookies" not in calls[0]
    assert "--js-runtimes" not in calls[0]
    assert "--extractor-args" not in calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("output_mode", ["zero", "two", "escape"])
async def test_download_rejects_invalid_after_move_output(monkeypatch, tmp_path, output_mode):
    calls = []
    monkeypatch.setattr(
        "lecturelog.infrastructure.media.video_ingestor.asyncio.create_subprocess_exec",
        _fake_exec_factory(calls, output_mode=output_mode),
    )

    with pytest.raises(MediaIngestError) as caught:
        await VideoIngestor().ingest(
            VideoUrlSource(url="https://cdn.example/video"),
            tmp_path / "out",
        )

    assert caught.value.reason is MediaIngestReason.INVALID_OUTPUT
    assert not list((tmp_path / "out").glob(".yt-dlp-*"))


def test_diagnostic_is_sanitized_and_public_string_is_safe():
    url = "https://x.com/i/status/1?token=secret"
    error = VideoIngestor()._error_from_failure(
        kind=VideoUrlKind.X,
        stderr=(
            b"ERROR https://x.com/i/status/1?token=secret "
            b"--cookies /tmp/private/cookies.txt Authorization: Bearer-secret"
        ),
        url=url,
        phase="download",
    )

    assert url not in error.diagnostic
    assert "/tmp/private/cookies.txt" not in error.diagnostic
    assert "Bearer-secret" not in error.diagnostic
    assert error.diagnostic != str(error)
