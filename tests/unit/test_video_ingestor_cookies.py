from pathlib import Path

import pytest

from lecturelog.domain.media_source import VideoUrlSource
from lecturelog.domain.ports import CookieStatus, CookieStore
from lecturelog.infrastructure.media.video_ingestor import VideoIngestor


class FakeCookieStore(CookieStore):
    def __init__(self, content: bytes | None):
        self._content = content

    async def save(self, content): ...
    async def get(self):
        return self._content

    async def status(self):
        return CookieStatus(exists=self._content is not None, updated_at=None, size=0)

    async def delete(self): ...


@pytest.fixture
def captured(monkeypatch, tmp_path):
    calls = {}

    async def fake_exec(*argv, **kwargs):
        calls["argv"] = list(argv)
        # Проверяем, что cookies-файл реально лежит на диске в момент вызова.
        if "--cookies" in argv:
            cpath = Path(argv[argv.index("--cookies") + 1])
            calls["cookies_existed"] = cpath.exists()
            calls["cookies_content"] = cpath.read_bytes()

        class P:
            returncode = 0

            async def communicate(self):
                template = argv[argv.index("-o") + 1]
                out = Path(template.replace("%(ext)s", "mp4"))
                out.write_bytes(b"video")
                return (f"{out}\n".encode(), b"")

        return P()

    monkeypatch.setattr(
        "lecturelog.infrastructure.media.video_ingestor.asyncio.create_subprocess_exec",
        fake_exec,
    )
    return calls


@pytest.mark.asyncio
async def test_download_uses_cookies_and_deno(captured, tmp_path):
    ingestor = VideoIngestor(cookie_store=FakeCookieStore(b"COOKIEDATA"))
    out_dir = tmp_path / "out"
    await ingestor.ingest(VideoUrlSource(url="https://youtu.be/x"), output_dir=out_dir)
    argv = captured["argv"]
    assert "--cookies" in argv
    assert captured["cookies_existed"] is True
    assert captured["cookies_content"] == b"COOKIEDATA"
    assert "--js-runtimes" in argv and "deno" in argv
    assert "--remote-components" in argv and "ejs:github" in argv
    assert "bv*+ba/b" in argv
    assert "res:720,proto:https" in argv
    assert "--no-playlist" in argv
    assert argv[argv.index("--playlist-items") + 1] == "1"
    # БЕЗОПАСНОСТЬ: cookies-файл лежит ВНЕ output-директории (не в расшаренном томе).
    cookies_path = Path(argv[argv.index("--cookies") + 1])
    assert out_dir.resolve() not in cookies_path.resolve().parents


@pytest.mark.asyncio
async def test_download_without_cookies_omits_flag(captured, tmp_path):
    ingestor = VideoIngestor(cookie_store=FakeCookieStore(None))
    await ingestor.ingest(VideoUrlSource(url="https://youtu.be/x"), output_dir=tmp_path / "out")
    argv = captured["argv"]
    assert "--cookies" not in argv
    assert "--js-runtimes" in argv and "deno" in argv
    assert "--remote-components" in argv and "ejs:github" in argv


@pytest.mark.asyncio
async def test_cookies_tempdir_removed_after_download(captured, tmp_path):
    ingestor = VideoIngestor(cookie_store=FakeCookieStore(b"DATA"))
    out_dir = tmp_path / "out"
    await ingestor.ingest(VideoUrlSource(url="https://youtu.be/x"), output_dir=out_dir)
    # Временный cookies-файл не должен остаться ни в output, ни в своём temp-каталоге.
    assert not list(out_dir.glob("*cookies*"))
    cookies_path = Path(captured["argv"][captured["argv"].index("--cookies") + 1])
    assert not cookies_path.exists()
    assert not cookies_path.parent.exists()  # temp-каталог удалён целиком
