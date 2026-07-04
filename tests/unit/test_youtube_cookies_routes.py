import io

import pytest
from fastapi.testclient import TestClient

from lecturelog.api import dependencies as deps
from lecturelog.api.app import create_app
from lecturelog.domain.ports import CookieStatus, CookieStore

GOOD_COOKIES = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tval\n"


class FakeCookieStore(CookieStore):
    def __init__(self):
        self._content: bytes | None = None

    async def save(self, content: bytes) -> CookieStatus:
        self._content = content
        return CookieStatus(exists=True, updated_at=None, size=len(content))

    async def get(self) -> bytes | None:
        return self._content

    async def status(self) -> CookieStatus:
        return CookieStatus(
            exists=self._content is not None,
            updated_at=None,
            size=len(self._content) if self._content else 0,
        )

    async def delete(self) -> None:
        self._content = None


@pytest.fixture
def cookie_store():
    return FakeCookieStore()


@pytest.fixture
def client(cookie_store):
    app = create_app()
    app.dependency_overrides[deps.get_cookie_store] = lambda: cookie_store
    # Stub остальных зависимостей, чтобы приложение не падало без lifespan.
    app.state.repository = None
    app.state.worker = None
    app.state.work_dir = None
    app.state.storage = None
    app.state.llm = None
    app.state.prompts_dir = None
    app.state.presign_expiry = 3600
    return TestClient(app)


def test_get_empty_returns_not_exists(client):
    resp = client.get("/api/v1/youtube/cookies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is False
    assert data["size"] == 0
    assert data["updated_at"] is None


def test_put_valid_cookies_returns_exists(client):
    resp = client.put(
        "/api/v1/youtube/cookies",
        files={"file": ("cookies.txt", io.BytesIO(GOOD_COOKIES), "text/plain")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is True
    assert data["size"] > 0
    # GET не возвращает содержимое (только метаданные).
    assert "content" not in data


def test_put_garbage_returns_400(client):
    resp = client.put(
        "/api/v1/youtube/cookies",
        files={"file": ("cookies.txt", io.BytesIO(b"<html></html>"), "text/plain")},
    )
    assert resp.status_code == 400


def test_put_too_large_returns_413(client):
    big = b"x" * (1 * 1024 * 1024 + 1)
    resp = client.put(
        "/api/v1/youtube/cookies",
        files={"file": ("cookies.txt", io.BytesIO(big), "text/plain")},
    )
    assert resp.status_code == 413


def test_delete_returns_204(client, cookie_store):
    import asyncio

    asyncio.run(cookie_store.save(GOOD_COOKIES))
    resp = client.delete("/api/v1/youtube/cookies")
    assert resp.status_code == 204
