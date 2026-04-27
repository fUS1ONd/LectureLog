"""
Транскрибация аудио через Groq Whisper.

На выходе — файл .srt рядом с исходным аудио (или в указанной папке).

Использование:
    python scripts/transcribe.py "путь/к/лекция.mp3"
    python scripts/transcribe.py "путь/к/лекция.mp3" --output "выход/"

Зависимости:
    pip install httpx
    ffmpeg должен быть установлен в системе

API-ключ:
    Добавьте GROQ_API_KEYS в scripts/.env (один ключ или несколько через запятую)
"""

import asyncio
import os
import sys
from pathlib import Path

# Загрузка .env без внешних зависимостей
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _val = _line.partition("=")
            os.environ.setdefault(_key.strip(), _val.strip())

# Добавляем корень проекта в путь поиска модулей
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lecturelog.pipeline.transcribe import transcribe


def _parse_args():
    import argparse
    parser = argparse.ArgumentParser(
        prog="transcribe",
        description="Транскрибация аудио через Groq Whisper → .srt",
    )
    parser.add_argument("audio", help="Путь к аудиофайлу (mp3, wav, m4a, ...)")
    parser.add_argument(
        "--output", "-o",
        help="Папка для результата (по умолчанию — папка рядом с аудио)",
    )
    return parser.parse_args()


async def main():
    args = _parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"Файл не найден: {audio_path}")
        sys.exit(1)

    # Поддержка одного ключа (GROQ_API_KEY) и нескольких через запятую (GROQ_API_KEYS)
    groq_keys_raw = os.environ.get("GROQ_API_KEYS", "") or os.environ.get("GROQ_API_KEY", "")
    groq_api_keys = [k.strip() for k in groq_keys_raw.split(",") if k.strip()]
    if not groq_api_keys:
        print("Добавьте GROQ_API_KEYS в scripts/.env (один ключ или несколько через запятую)")
        sys.exit(1)

    print(f"Groq ключей: {len(groq_api_keys)}")

    output_dir = Path(args.output) if args.output else audio_path.parent / (audio_path.stem + "-transcribe")

    print(f"Аудио:    {audio_path}")
    print(f"Выход:    {output_dir}")

    def on_progress(pct: int):
        print(f"  Прогресс: {pct}%")

    srt_path = await transcribe(
        audio_path=audio_path,
        output_dir=output_dir,
        groq_api_keys=groq_api_keys,
        on_progress=on_progress,
    )

    print(f"\nГотово: {srt_path}")


if __name__ == "__main__":
    asyncio.run(main())
