import asyncio

import lecturelog.llm.gemini as gemini_module
from lecturelog.llm.gemini import call_gemini
from lecturelog.llm.key_pool import KeyPool


def test_acquire_round_robin() -> None:
    async def scenario() -> list[int]:
        pool = KeyPool(clients=["c1", "c2", "c3"], rpm_per_key=10_000)
        result = []
        for _ in range(4):
            _, idx = await pool.acquire()
            result.append(idx)
        return result

    assert asyncio.run(scenario()) == [0, 1, 2, 0]


def test_mark_rate_limited_excludes_key() -> None:
    async def scenario() -> int:
        pool = KeyPool(clients=["c1", "c2"], rpm_per_key=10_000, block_seconds=0.2)
        await pool.mark_rate_limited(0)
        _, idx = await pool.acquire()
        return idx

    assert asyncio.run(scenario()) == 1


def test_alive_count_changes_after_block() -> None:
    async def scenario() -> tuple[int, int]:
        pool = KeyPool(clients=["c1", "c2", "c3"], rpm_per_key=10_000, block_seconds=0.2)
        before = pool.alive_count()
        await pool.mark_rate_limited(1)
        after = pool.alive_count()
        return before, after

    assert asyncio.run(scenario()) == (3, 2)


def test_call_gemini_retries_after_429_and_succeeds(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, text: str):
            self.text = text

    class FakeModels:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, *, model: str, contents: str):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
            assert model == "custom-model"
            assert contents == "prompt"
            return FakeResponse("ok")

    class FakeClient:
        def __init__(self) -> None:
            self.models = FakeModels()

    async def scenario() -> str:
        pool = KeyPool(clients=[FakeClient()], rpm_per_key=10_000, block_seconds=0.0)
        to_thread_calls: list[tuple] = []

        async def fake_to_thread(func, *args, **kwargs):
            to_thread_calls.append((func, args, kwargs))
            return func(*args, **kwargs)

        monkeypatch.setattr(gemini_module.asyncio, "to_thread", fake_to_thread)

        result = await call_gemini(
            pool=pool,
            prompt="prompt",
            model="custom-model",
            retries=2,
        )
        assert result == "ok"
        assert len(to_thread_calls) == 2
        return result

    assert asyncio.run(scenario()) == "ok"
