from __future__ import annotations

from lecturelog.application.factories import storage_factory
from lecturelog.config.settings import S3Config
from lecturelog.domain.ports import Storage


def _cfg(monkeypatch, public=None):
    monkeypatch.setenv("S3_INTERNAL_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("S3_BUCKET", "lectures")
    monkeypatch.setenv("S3_ACCESS_KEY", "ak")
    monkeypatch.setenv("S3_SECRET_KEY", "sk")
    if public is None:
        monkeypatch.delenv("S3_PUBLIC_ENDPOINT", raising=False)
    else:
        monkeypatch.setenv("S3_PUBLIC_ENDPOINT", public)
    return S3Config(_env_file=None)


def test_storage_factory_returns_storage_port(monkeypatch):
    # storage_factory строит реализацию доменного порта Storage из конфига.
    storage = storage_factory(_cfg(monkeypatch))
    assert isinstance(storage, Storage)


def test_presigned_availability_follows_public_endpoint(monkeypatch):
    # Доступность presigned = public_endpoint задан.
    on = storage_factory(_cfg(monkeypatch, public="https://files.example"))
    off = storage_factory(_cfg(monkeypatch, public=None))
    assert on._public_endpoint == "https://files.example"
    assert off._public_endpoint is None
