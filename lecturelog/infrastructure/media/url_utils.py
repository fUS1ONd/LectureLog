from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse


class VideoUrlKind(StrEnum):
    YOUTUBE = "youtube"
    X = "x"
    GENERIC = "generic"


_X_HOSTS = {
    "x.com",
    "www.x.com",
    "mobile.x.com",
    "m.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
    "m.twitter.com",
}


def is_url(source: str | Path) -> bool:
    """URL = http/https-схема + непустой netloc. Путь/Path → False."""
    if not isinstance(source, str):
        return False
    parsed = urlparse(source)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def classify_video_url(url: str) -> VideoUrlKind:
    """Выбрать downloader-профиль по точному нормализованному hostname."""
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    if hostname in _X_HOSTS:
        return VideoUrlKind.X
    if hostname == "youtu.be" or hostname == "youtube.com" or hostname.endswith(".youtube.com"):
        return VideoUrlKind.YOUTUBE
    return VideoUrlKind.GENERIC
