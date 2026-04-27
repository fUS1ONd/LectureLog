"""
Двухэтапное структурирование транскрипта лекции в конспект.

Этап 1: LLM разбивает транскрипт на разделы (заголовки + таймкоды)
Этап 2: Параллельные запросы на полное оформление каждого раздела

Использование:
    1. Добавьте ключи в scripts/api_keys.txt (по одному на строку)
    2. python scripts/structurize.py "путь/к/лекция.srt"

Зависимости:
    pip install google-genai
"""

import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Загрузка .env если есть (без внешних зависимостей)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _val = _line.partition("=")
            os.environ.setdefault(_key.strip(), _val.strip())

from google import genai

# Максимум параллельных запросов (подстраивается под кол-во ключей × RPM)
MAX_WORKERS = 5

# --- Промпт этапа 1: разбивка на разделы ---
SPLIT_PROMPT = """\
Проанализируй транскрипт лекции в формате SRT и разбей его на смысловые разделы.

## Задача
Определи, где лектор переходит от одной темы/подтемы к другой. \
Для каждого раздела укажи заголовок и таймкоды.

## Правила
- Новый раздел = смена темы/подтемы лектором
- Ориентируйся на смысловые переходы, а не на паузы
- Оптимальный размер раздела: 3-5 минут лекции. МАКСИМУМ 5 минут на раздел. \
Если тема длится дольше 5 минут — разбей её на подтемы
- Таймкоды бери из SRT — время первой фразы раздела и время последней

## Формат ответа — строго JSON, без markdown-обёртки:

[
  {
    "title": "Название раздела",
    "start": "00:00:00",
    "end": "00:05:30"
  },
  {
    "title": "Следующий раздел",
    "start": "00:05:30",
    "end": "00:12:15"
  }
]

Верни ТОЛЬКО JSON-массив, без пояснений и комментариев.

## Транскрипт (SRT):

"""

# --- Промпт этапа 2: оформление одного раздела ---
SECTION_PROMPT = """\
Ты — ассистент для создания конспектов лекций. Тебе дан фрагмент транскрипта \
лекции (формат SRT) для раздела "{title}" [{start} - {end}].

## КРИТИЧЕСКИ ВАЖНО: НЕ СОКРАЩАЙ

- Это НЕ саммари. Это ПОЛНЫЙ конспект данного фрагмента
- Сохраняй ВСЮ информацию: все определения, все примеры, все пояснения
- Если лектор привёл пример — пример целиком
- Если лектор дал определение — определение дословно
- Если лектор объяснял что-то в 10 предложениях — все 10, только причёсанные

## Правила оформления

1. НЕ ДОБАВЛЯЙ информацию, которой нет в транскрипте
2. Сохраняй точные формулировки, термины и определения лектора
3. Не используй вводные фразы: «Давайте рассмотрим», «Важно отметить», \
«Следует подчеркнуть» — если лектор их не говорил
4. Если мысль обрывается или неясна — помечай [неясный фрагмент]
5. Диалоги со студентами: если вопрос/ответ содержит важную информацию — \
включи. Болтовню и оффтопик пропускай
6. Английские термины сохраняй как есть
7. Убирай: слова-паразиты (ну, вот, как бы, то есть), повторы фраз, \
незаконченные предложения — оставляй финальную версию мысли
8. НЕ убирай: примеры, пояснения, аналогии, детали, контекст

## Стиль форматирования

- Пиши СВЯЗНЫМ ТЕКСТОМ, абзацами — как в учебнике, а НЕ списками
- НЕ превращай текст в маркированные/нумерованные списки, если лектор не перечислял пункты
- Списки допустимы ТОЛЬКО когда лектор явно перечислял: «во-первых... во-вторых...» \
или «есть три типа: ...»
- Определения и ключевые термины выделяй **жирным**
- Подзаголовки (###) можно использовать внутри раздела если есть явная подтема

## Формат выхода

Выведи ТОЛЬКО оформленный текст раздела (без заголовка раздела и без таймкодов — \
они будут добавлены автоматически). Начинай сразу с содержания.

## Фрагмент транскрипта (SRT):

"""


def extract_plain_text(srt_content: str) -> str:
    """Извлекает чистый текст из SRT, убирая нумерацию и таймкоды."""
    lines = []
    for line in srt_content.split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\d+$", line):
            continue
        if re.match(r"\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->", line):
            continue
        lines.append(line)
    return " ".join(lines)


def parse_srt_time(time_str: str) -> float:
    """Переводит таймкод SRT (ЧЧ:ММ:СС,МСС или ММ:СС,МСС) в секунды."""
    time_str = time_str.replace(",", ".")
    parts = time_str.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    else:
        return float(parts[0])


def format_time(time_str: str) -> str:
    """Нормализует формат таймкода до ЧЧ:ММ:СС."""
    return time_str.split(",")[0].split(".")[0]


def extract_srt_fragment(srt_content: str, start: str, end: str) -> str:
    """Вырезает фрагмент SRT по таймкодам."""
    start_sec = parse_srt_time(start.replace(".", ",") if "," not in start else start)
    end_sec = parse_srt_time(end.replace(".", ",") if "," not in end else end)

    blocks = re.split(r"\n\n+", srt_content.strip())
    result = []

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue

        time_match = re.search(
            r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})",
            block,
        )
        if not time_match:
            continue

        block_start = parse_srt_time(time_match.group(1))
        block_end = parse_srt_time(time_match.group(2))

        if block_end >= start_sec and block_start <= end_sec:
            result.append(block)

    return "\n\n".join(result)


# Максимум запросов в минуту на один ключ (с запасом от реальных 15)
RPM_PER_KEY = 12


class KeyPool:
    """Пул API-ключей с round-robin, rate limiting и исключением мёртвых ключей."""

    def __init__(self, clients: list[genai.Client]):
        self._clients = clients
        self._lock = threading.Lock()
        # Время последнего запроса для каждого ключа
        self._last_request: list[float] = [0.0] * len(clients)
        # Время до которого ключ заблокирован (после 429)
        self._blocked_until: list[float] = [0.0] * len(clients)
        # Счётчик round-robin
        self._next_idx = 0
        # Минимальный интервал между запросами на один ключ
        self._interval = 60.0 / RPM_PER_KEY

    def acquire(self) -> tuple[genai.Client, int]:
        """Получить клиент для запроса. Ждёт если нужно соблюсти rate limit."""
        while True:
            with self._lock:
                now = time.time()
                # Ищем первый доступный ключ (round-robin)
                for _ in range(len(self._clients)):
                    idx = self._next_idx
                    self._next_idx = (self._next_idx + 1) % len(self._clients)

                    # Пропускаем заблокированные ключи
                    if now < self._blocked_until[idx]:
                        continue

                    # Проверяем rate limit
                    wait = self._last_request[idx] + self._interval - now
                    if wait <= 0:
                        self._last_request[idx] = now
                        return self._clients[idx], idx

                # Все ключи либо заблокированы, либо под rate limit
                # Считаем минимальное время ожидания
                min_wait = float("inf")
                for i in range(len(self._clients)):
                    unblock = max(self._blocked_until[i], self._last_request[i] + self._interval)
                    min_wait = min(min_wait, unblock - now)

            # Ждём вне лока
            sleep_time = max(0.1, min_wait)
            time.sleep(sleep_time)

    def mark_rate_limited(self, idx: int):
        """Пометить ключ как получивший 429 — блокируем на 60 сек."""
        with self._lock:
            self._blocked_until[idx] = time.time() + 60.0
            alive = sum(1 for t in self._blocked_until if t <= time.time())
            print(f"  Ключ {idx + 1} заблокирован на 60с (живых ключей: {alive}/{len(self._clients)})")

    def alive_count(self) -> int:
        """Количество незаблокированных ключей."""
        with self._lock:
            now = time.time()
            return sum(1 for t in self._blocked_until if t <= now)


def call_llm(pool: KeyPool, model: str, prompt: str, retries: int = 5) -> str:
    """Вызов LLM с rate limiting и повторами."""
    last_error = None

    for attempt in range(retries):
        client, idx = pool.acquire()
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )
            return response.text
        except Exception as e:
            last_error = e
            err = str(e)
            is_rate_limit = "429" in err or "RESOURCE_EXHAUSTED" in err
            is_overload = "503" in err or "UNAVAILABLE" in err

            if is_rate_limit:
                pool.mark_rate_limited(idx)
            elif is_overload:
                wait = 10 * (attempt + 1)
                print(f"  Сервер перегружен (503), жду {wait}с...")
                time.sleep(wait)
            else:
                raise

    raise RuntimeError(f"Не удалось получить ответ после {retries} попыток: {last_error}")


def process_section(
    idx: int,
    total: int,
    section: dict,
    srt_content: str,
    pool: KeyPool,
    model: str,
) -> tuple[int, str, str]:
    """Обрабатывает один раздел. Возвращает (индекс, результат, лог)."""
    title = section["title"]
    start = section["start"]
    end = section["end"]

    log_lines = [f"[{idx + 1}/{total}] {title} [{start} - {end}]"]

    # Вырезаем фрагмент SRT
    fragment = extract_srt_fragment(srt_content, start, end)
    if not fragment:
        log_lines.append("  Предупреждение: пустой фрагмент, пропускаю")
        result = f"## {title}\n[{format_time(start)} - {format_time(end)}]\n\n[пустой фрагмент]\n"
        return idx, result, "\n".join(log_lines)

    # Считаем чистый текст
    plain_text = extract_plain_text(fragment)
    plain_len = len(plain_text)
    log_lines.append(f"  SRT фрагмент: {len(fragment)} сим. | Чистый текст: {plain_len} сим.")

    # Формируем промпт и отправляем
    section_prompt = SECTION_PROMPT.format(title=title, start=start, end=end) + fragment
    section_text = call_llm(pool, model, section_prompt)

    ratio = (len(section_text) / plain_len * 100) if plain_len > 0 else 0
    log_lines.append(f"  Результат: {len(section_text)} сим. | {ratio:.0f}% от чистого текста")

    result = f"## {title}\n[{format_time(start)} - {format_time(end)}]\n\n{section_text}\n"
    return idx, result, "\n".join(log_lines)


def main():
    if len(sys.argv) < 2:
        print("Использование: python structurize.py <путь_к_srt>")
        sys.exit(1)

    srt_path = Path(sys.argv[1])
    if not srt_path.exists():
        print(f"Файл не найден: {srt_path}")
        sys.exit(1)

    # Загружаем API-ключи из файла scripts/api_keys.txt
    keys_file = Path(__file__).parent / "api_keys.txt"
    api_keys = []
    if keys_file.exists():
        for line in keys_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                api_keys.append(line)
    if not api_keys:
        env_key = os.environ.get("GEMINI_API_KEY")
        if env_key:
            api_keys.append(env_key)
    if not api_keys:
        print("Добавьте API-ключи в scripts/api_keys.txt или установите GEMINI_API_KEY")
        sys.exit(1)

    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
    clients = [genai.Client(api_key=key) for key in api_keys]
    pool = KeyPool(clients)
    print(f"API-ключей: {len(clients)} | Модель: {model} | Параллельность: {MAX_WORKERS} | RPM/ключ: {RPM_PER_KEY}")

    # Читаем транскрипт
    srt_content = srt_path.read_text(encoding="utf-8")
    print(f"Прочитан файл: {srt_path} ({len(srt_content)} символов)")

    # --- Этап 1: разбивка на разделы ---
    print(f"\n=== Этап 1: разбивка на разделы ===")
    split_response = call_llm(pool, model, SPLIT_PROMPT + srt_content)

    json_text = split_response.strip()
    json_text = re.sub(r"^```(?:json)?\s*", "", json_text)
    json_text = re.sub(r"\s*```$", "", json_text)

    try:
        sections = json.loads(json_text)
    except json.JSONDecodeError as e:
        print(f"Ошибка парсинга JSON: {e}")
        print(f"Ответ модели:\n{split_response}")
        sys.exit(1)

    print(f"Найдено разделов: {len(sections)}")
    for i, s in enumerate(sections, 1):
        print(f"  {i}. [{s['start']} - {s['end']}] {s['title']}")

    # --- Этап 2: параллельное оформление разделов ---
    print(f"\n=== Этап 2: оформление разделов (параллельно, {MAX_WORKERS} потоков) ===")
    results = [None] * len(sections)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(
                process_section, i, len(sections), section, srt_content, pool, model
            ): i
            for i, section in enumerate(sections)
        }

        for future in as_completed(futures):
            idx, result, log = future.result()
            results[idx] = result
            print(f"\n{log}")

    # --- Сборка конспекта ---
    lecture_title = "Конспект лекции"
    full_text = f"# {lecture_title}\n\n" + "\n---\n\n".join(results)

    # Сохраняем
    output_path = srt_path.with_name(srt_path.stem + "-конспект.md")
    output_path.write_text(full_text, encoding="utf-8")

    # Итоговая статистика
    total_plain = len(extract_plain_text(srt_content))
    total_result = len(full_text)
    total_ratio = (total_result / total_plain * 100) if total_plain > 0 else 0

    print(f"\n=== Готово ===")
    print(f"Конспект сохранён: {output_path}")
    print(f"Чистый текст SRT: {total_plain} сим.")
    print(f"Конспект:          {total_result} сим.")
    print(f"Соотношение:       {total_ratio:.0f}%")
    if total_ratio < 70:
        print("⚠ Конспект значительно короче оригинала — возможно сжатие")


if __name__ == "__main__":
    main()
