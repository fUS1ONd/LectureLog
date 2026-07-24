from pathlib import Path

from lecturelog.application.factories import (
    cutter_factory,
    slide_provider_factory,
    storage_factory,
    transcriber_factory,
)
from lecturelog.config.settings import S3Config, TranscribeConfig
from lecturelog.domain.media_source import AudioSource, VideoFileSource
from lecturelog.infrastructure.storage.s3_storage import S3Storage
from lecturelog.infrastructure.transcribe.deepgram_transcriber import DeepgramTranscriber
from lecturelog.infrastructure.transcribe.groq_transcriber import GroqTranscriber


class _A:  # маркеры, чтобы различать выбранную реализацию
    pass


class _V:
    pass


def test_cutter_factory_picks_video_for_video_source():
    a, v = _A(), _V()
    chosen = cutter_factory(VideoFileSource(path=Path("/v.mp4")), audio_cutter=a, video_cutter=v)
    assert chosen is v


def test_cutter_factory_picks_audio_for_audio_source():
    a, v = _A(), _V()
    chosen = cutter_factory(AudioSource(path=Path("/a.mp3")), audio_cutter=a, video_cutter=v)
    assert chosen is a


def test_no_slides_flag_wins():
    doc, vid = _A(), _V()
    assert slide_provider_factory(no_slides=True, document_provider=doc, video_provider=vid) is None


def test_document_takes_priority_over_video():
    doc, vid = _A(), _V()
    chosen = slide_provider_factory(no_slides=False, document_provider=doc, video_provider=vid)
    assert chosen is doc


def test_video_auto_when_no_document():
    vid = _V()
    chosen = slide_provider_factory(no_slides=False, document_provider=None, video_provider=vid)
    assert chosen is vid


def test_none_when_nothing_available():
    assert (
        slide_provider_factory(no_slides=False, document_provider=None, video_provider=None) is None
    )


def _s3_config(monkeypatch, public=None):
    monkeypatch.setenv("S3_INTERNAL_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("S3_BUCKET", "lectures")
    monkeypatch.setenv("S3_ACCESS_KEY", "ak")
    monkeypatch.setenv("S3_SECRET_KEY", "sk")
    if public is None:
        monkeypatch.delenv("S3_PUBLIC_ENDPOINT", raising=False)
    else:
        monkeypatch.setenv("S3_PUBLIC_ENDPOINT", public)
    return S3Config(_env_file=None)


def test_storage_factory_builds_s3storage(monkeypatch):
    cfg = _s3_config(monkeypatch, public="https://files.example")
    storage = storage_factory(cfg)
    assert isinstance(storage, S3Storage)
    assert storage._bucket == "lectures"
    assert storage._public_endpoint == "https://files.example"


def test_storage_factory_no_public_keeps_presign_off(monkeypatch):
    cfg = _s3_config(monkeypatch, public=None)
    storage = storage_factory(cfg)
    assert storage._public_endpoint is None


def test_transcriber_factory_selects_groq():
    config = TranscribeConfig(_env_file=None, GROQ_API_KEYS="g1")
    assert isinstance(transcriber_factory(config), GroqTranscriber)


def test_transcriber_factory_selects_deepgram():
    config = TranscribeConfig(
        _env_file=None,
        TRANSCRIBE_PROVIDER="deepgram",
        DEEPGRAM_API_KEY="secret",
    )
    assert isinstance(transcriber_factory(config), DeepgramTranscriber)


def test_transcriber_factory_passes_language_detection():
    config = TranscribeConfig(
        _env_file=None,
        TRANSCRIBE_PROVIDER="deepgram",
        DEEPGRAM_API_KEY="secret",
        DEEPGRAM_DETECT_LANGUAGE=True,
    )
    transcriber = transcriber_factory(config)
    assert isinstance(transcriber, DeepgramTranscriber)
    assert transcriber._detect_language is True
