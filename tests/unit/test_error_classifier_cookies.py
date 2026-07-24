from lecturelog.application.error_classifier import classify_error
from lecturelog.domain.enums import ErrorCode
from lecturelog.domain.exceptions import MediaIngestError, MediaIngestReason


def test_classifies_youtube_bot_check_as_cookies_invalid():
    exc = RuntimeError(
        "yt-dlp не смог скачать видео: ERROR: [youtube] Sign in to confirm "
        "you're not a bot. Use --cookies-from-browser ..."
    )
    assert classify_error(exc) == ErrorCode.COOKIES_INVALID


def test_classifies_confirm_not_a_bot_variant():
    exc = RuntimeError("Please confirm you're not a bot")
    assert classify_error(exc) == ErrorCode.COOKIES_INVALID


def test_unrelated_error_stays_internal():
    assert classify_error(RuntimeError("disk full")) == ErrorCode.INTERNAL


def _media_error(source_kind, reason):
    return MediaIngestError(
        source_kind=source_kind,
        reason=reason,
        public_message="safe",
        diagnostic="raw",
    )


def test_structured_youtube_auth_is_cookies_invalid():
    assert (
        classify_error(_media_error("youtube", MediaIngestReason.AUTH_REQUIRED))
        is ErrorCode.COOKIES_INVALID
    )


def test_structured_x_auth_is_bad_input_not_cookies_invalid():
    assert classify_error(_media_error("x", MediaIngestReason.AUTH_REQUIRED)) is ErrorCode.BAD_INPUT


def test_structured_media_reason_mapping():
    assert classify_error(_media_error("x", MediaIngestReason.RATE_LIMIT)) is ErrorCode.RATE_LIMIT
    assert classify_error(_media_error("x", MediaIngestReason.NOT_FOUND)) is ErrorCode.BAD_INPUT
    assert classify_error(_media_error("x", MediaIngestReason.LOCAL_IO)) is ErrorCode.INTERNAL
