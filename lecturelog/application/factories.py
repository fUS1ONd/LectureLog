from __future__ import annotations

from lecturelog.config.settings import S3Config, TranscribeConfig
from lecturelog.domain.media_source import MediaSource, is_video_source
from lecturelog.domain.ports import (
    MediaCutter,
    SlideProvider,
    Storage,
    Transcriber,
    WebhookNotifier,
)
from lecturelog.infrastructure.storage.s3_storage import S3Storage
from lecturelog.infrastructure.transcribe.deepgram_transcriber import DeepgramTranscriber
from lecturelog.infrastructure.transcribe.groq_transcriber import GroqTranscriber
from lecturelog.infrastructure.webhook.http_notifier import HttpWebhookNotifier


def cutter_factory(
    source: MediaSource, *, audio_cutter: MediaCutter, video_cutter: MediaCutter
) -> MediaCutter:
    """Видеоисточник режется видео-cutter'ом, аудио — аудио-cutter'ом."""
    return video_cutter if is_video_source(source) else audio_cutter


def slide_provider_factory(
    *,
    no_slides: bool,
    document_provider: SlideProvider | None,
    video_provider: SlideProvider | None,
) -> SlideProvider | None:
    """Выбор источника слайдов по приоритету:
    1. no_slides → None;
    2. документ (PDF/PPTX) приоритетнее;
    3. иначе авто-извлечение из видео;
    4. иначе None.
    """
    if no_slides:
        return None
    if document_provider is not None:
        return document_provider
    return video_provider


def storage_factory(s3: S3Config) -> Storage:
    """Собрать S3-адаптер хранилища из конфига. presigned наружу включается,
    только если задан public_endpoint (иначе адаптер вернёт None на presigned)."""
    return S3Storage(
        internal_endpoint=s3.internal_endpoint,
        public_endpoint=s3.public_endpoint,
        bucket=s3.bucket,
        access_key=s3.access_key,
        secret_key=s3.secret_key,
        region=s3.region,
        default_expiry=s3.presign_expiry,
    )


def transcriber_factory(config: TranscribeConfig) -> Transcriber:
    if config.provider == "groq":
        return GroqTranscriber(groq_api_keys=config.groq_keys)
    assert config.deepgram_api_key is not None
    return DeepgramTranscriber(
        api_key=config.deepgram_api_key.get_secret_value(),
        base_url=config.deepgram_base_url,
        model=config.deepgram_model,
        language=config.deepgram_language,
        utt_split=config.deepgram_utt_split,
    )


def webhook_notifier_factory(
    callback_url: str | None, secret: str | None
) -> WebhookNotifier | None:
    """Нотификатор только при заданных callback_url и секрете; иначе None (автономный режим)."""
    if not callback_url:
        return None
    if not secret:
        # Секрет обязателен для подписи; без него вебхук не включаем (логируем выше по стеку).
        return None
    return HttpWebhookNotifier(callback_url=callback_url, secret=secret)
