import asyncio

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
        pool = KeyPool(clients=["c1", "c2"], rpm_per_key=10_000)
        pool.mark_rate_limited(0)
        _, idx = await pool.acquire()
        return idx

    assert asyncio.run(scenario()) == 1


def test_alive_count_changes_after_block() -> None:
    pool = KeyPool(clients=["c1", "c2", "c3"], rpm_per_key=10_000)
    assert pool.alive_count() == 3
    pool.mark_rate_limited(1)
    assert pool.alive_count() == 2

