# Groq Key Pool Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Добавить поддержку нескольких Groq API ключей с автоматическим fallback при 429/сетевых ошибках.

**Architecture:** Добавить класс `GroqKeyPool` в `lecturelog/pipeline/transcribe.py`, который при каждом запросе чанка выбирает следующий доступный ключ по round-robin; при 429 помечает ключ заблокированным на 60 сек и переключается на другой. Сигнатура `transcribe()` меняется: `groq_api_key: str` → `groq_api_keys: list[str]`. `runner.py` и `scripts/transcribe.py` передают список ключей.

**Tech Stack:** Python asyncio, httpx, asyncio.Lock

---

### Task 1: Добавить `GroqKeyPool` и обновить `_transcribe_chunk`

**Files:**
- Modify: `lecturelog/pipeline/transcribe.py`
- Test: `tests/test_transcribe.py`

**Step 1: Написать падающий тест на GroqKeyPool**

```python
import asyncio
import pytest
from lecturelog.pipeline.transcribe import GroqKeyPool

@pytest.mark.asyncio
async def test_groq_key_pool_round_robin():
    pool = GroqKeyPool(["key1", "key2"])
    k1 = await pool.acquire()
    k2 = await pool.acquire()
    k3 = await pool.acquire()
    assert k1 == "key1"
    assert k2 == "key2"
    assert k3 == "key1"  # round-robin

@pytest.mark.asyncio
async def test_groq_key_pool_skip_blocked():
    pool = GroqKeyPool(["key1", "key2"])
    pool.mark_rate_limited(0)  # блокируем key1
    k = await pool.acquire()
    assert k == "key2"
```

**Step 2: Запустить тест — убедиться что падает**

```bash
pytest tests/test_transcribe.py::test_groq_key_pool_round_robin -v
```
Ожидаем: `ImportError` или `AttributeError` — класса нет.

**Step 3: Реализовать `GroqKeyPool`**

Добавить в `lecturelog/pipeline/transcribe.py` после импортов:

```python
import time

class GroqKeyPool:
    """Round-robin пул Groq API ключей с блокировкой при rate limit."""

    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("Нужен хотя бы один Groq API ключ")
        self._keys = keys
        self._blocked_until: list[float] = [0.0] * len(keys)
        self._next_idx = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> str:
        """Вернуть следующий доступный ключ. Ждёт если все заблокированы."""
        while True:
            async with self._lock:
                now = time.monotonic()
                for _ in range(len(self._keys)):
                    idx = self._next_idx
                    self._next_idx = (self._next_idx + 1) % len(self._keys)
                    if now >= self._blocked_until[idx]:
                        return self._keys[idx]
                # Все заблокированы — считаем минимальное время ожидания
                min_wait = min(self._blocked_until) - now

            await asyncio.sleep(max(0.1, min_wait))

    def mark_rate_limited(self, key_index: int, block_seconds: float = 60.0):
        """Пометить ключ как получивший 429."""
        self._blocked_until[key_index] = time.monotonic() + block_seconds
        print(f"  Groq ключ {key_index + 1}/{len(self._keys)} заблокирован на {block_seconds:.0f}с")

    def key_index(self, key: str) -> int:
        return self._keys.index(key)
```

**Step 4: Запустить тесты**

```bash
pytest tests/test_transcribe.py -v
```
Ожидаем: все тесты PASS.

**Step 5: Обновить `_transcribe_chunk` — принимает `pool` вместо одного ключа**

Заменить сигнатуру и логику в `lecturelog/pipeline/transcribe.py`:

```python
async def _transcribe_chunk(
    client: httpx.AsyncClient,
    chunk_path: Path,
    pool: GroqKeyPool,
    offset_seconds: float,
    semaphore: asyncio.Semaphore,
    max_retries: int = 5,
) -> list[dict[str, Any]]:
    async with semaphore:
        file_bytes = chunk_path.read_bytes()
        for attempt in range(max_retries):
            api_key = await pool.acquire()
            headers = {"Authorization": f"Bearer {api_key}"}
            files_payload = [
                ("model", (None, "whisper-large-v3")),
                ("response_format", (None, "verbose_json")),
                ("timestamp_granularities[]", (None, "word")),
                ("file", (chunk_path.name, file_bytes, "audio/mpeg")),
            ]
            try:
                response = await client.post(
                    GROQ_TRANSCRIBE_URL,
                    headers=headers,
                    files=files_payload,
                )
                response.raise_for_status()
                break
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError):
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt * 5)
                    continue
                raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 503) and attempt < max_retries - 1:
                    pool.mark_rate_limited(pool.key_index(api_key))
                    await asyncio.sleep(2 ** attempt * 5)
                    continue
                raise
        payload = response.json()
        words: list[dict[str, Any]] = payload.get("words") or []
        result: list[dict[str, Any]] = []
        for item in words:
            word = str(item.get("word", "")).strip()
            start = item.get("start")
            end = item.get("end")
            if not word or start is None or end is None:
                continue
            result.append({
                "word": word,
                "start": float(start) + offset_seconds,
                "end": float(end) + offset_seconds,
            })
        return result
```

**Step 6: Обновить `transcribe()` — принимает `groq_api_keys: list[str]`**

```python
async def transcribe(
    audio_path: Path,
    output_dir: Path,
    groq_api_keys: list[str],
    on_progress: Callable[[int], Any],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    await _emit_progress(on_progress, 5)

    await _run_ffmpeg_segment(audio_path, output_dir)
    chunk_paths = sorted(output_dir.glob("chunk_*.mp3"))
    if not chunk_paths:
        raise RuntimeError("ffmpeg не создал сегменты аудио")

    await _emit_progress(on_progress, 20)

    pool = GroqKeyPool(groq_api_keys)
    semaphore = asyncio.Semaphore(1)
    words: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=60.0)) as client:
        tasks = [
            asyncio.create_task(
                _transcribe_chunk(
                    client=client,
                    chunk_path=chunk_path,
                    pool=pool,
                    offset_seconds=index * 1200.0,
                    semaphore=semaphore,
                )
            )
            for index, chunk_path in enumerate(chunk_paths)
        ]

        total = len(tasks)
        done = 0
        for task in asyncio.as_completed(tasks):
            chunk_words = await task
            words.extend(chunk_words)
            done += 1
            await _emit_progress(on_progress, 20 + int((done / total) * 70))

    srt_content = _build_srt_from_words(words)
    srt_path = output_dir / "transcript.srt"
    srt_path.write_text(srt_content, encoding="utf-8")

    await _emit_progress(on_progress, 100)
    return srt_path
```

**Step 7: Запустить все тесты**

```bash
pytest tests/test_transcribe.py -v
```
Ожидаем: все PASS.

**Step 8: Коммит**

```bash
git add lecturelog/pipeline/transcribe.py tests/test_transcribe.py
git commit -m "feat: добавить GroqKeyPool с round-robin и fallback при rate limit"
```

---

### Task 2: Обновить runner.py и scripts/transcribe.py

**Files:**
- Modify: `lecturelog/pipeline/runner.py`
- Modify: `scripts/transcribe.py`
- Modify: `lecturelog/config.py` (GROQ_API_KEY → GROQ_API_KEYS)

**Step 1: Обновить `config.py`**

Заменить `GROQ_API_KEY: str` на `GROQ_API_KEYS: str` и добавить property:

```python
GROQ_API_KEYS: str  # одиночный ключ или несколько через запятую

@property
def groq_api_keys(self) -> list[str]:
    return [k.strip() for k in self.GROQ_API_KEYS.split(",") if k.strip()]
```

> Примечание: `.env` файл нужно обновить — переименовать `GROQ_API_KEY` в `GROQ_API_KEYS`.

**Step 2: Обновить `runner.py`**

Заменить строку:
```python
groq_api_key=self.config.GROQ_API_KEY,
```
На:
```python
groq_api_keys=self.config.groq_api_keys,
```

**Step 3: Обновить `scripts/transcribe.py`**

Заменить чтение одного ключа:
```python
groq_api_key = os.environ.get("GROQ_API_KEY", "")
if not groq_api_key:
    print("Добавьте GROQ_API_KEY в scripts/.env или установите переменную окружения")
    sys.exit(1)
```

На чтение списка ключей:
```python
groq_keys_raw = os.environ.get("GROQ_API_KEYS", "") or os.environ.get("GROQ_API_KEY", "")
groq_api_keys = [k.strip() for k in groq_keys_raw.split(",") if k.strip()]
if not groq_api_keys:
    print("Добавьте GROQ_API_KEYS в scripts/.env (один ключ или несколько через запятую)")
    sys.exit(1)

print(f"Groq ключей: {len(groq_api_keys)}")
```

И в вызове `transcribe()`:
```python
srt_path = await transcribe(
    audio_path=audio_path,
    output_dir=output_dir,
    groq_api_keys=groq_api_keys,
    on_progress=on_progress,
)
```

**Step 4: Обновить `.env.example`**

```
# Groq (транскрибация) — один ключ или несколько через запятую
GROQ_API_KEYS=gsk_...,gsk_...
```

**Step 5: Запустить все тесты проекта**

```bash
pytest tests/ -v
```
Ожидаем: все PASS (возможны пропуски интеграционных тестов).

**Step 6: Коммит**

```bash
git add lecturelog/pipeline/runner.py lecturelog/config.py scripts/transcribe.py scripts/.env.example
git commit -m "feat: переключить runner и скрипты на groq_api_keys (список через запятую)"
```
