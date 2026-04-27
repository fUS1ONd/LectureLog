from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile, Message

from lecturelog.config import get_settings

router = Router()


class ProcessStates(StatesGroup):
    WAIT_AUDIO = State()
    WAIT_SLIDES = State()
    PROCESSING = State()


def _status_text(stage: str | None, progress: int) -> str:
    mapping = {
        "transcribe": "Транскрибация",
        "slides": "Обработка слайдов",
        "structurize": "Структурирование",
        "audio_cut": "Нарезка аудио",
        "export": "Сборка результата",
    }
    stage_name = mapping.get(stage or "", stage or "Ожидание")
    return f"⏳ {stage_name}... {progress}%"


async def _download_file(message: Message, file_id: str, target: Path):
    bot = message.bot
    file = await bot.get_file(file_id)
    await bot.download_file(file.file_path, destination=target)


async def _run_remote_pipeline(message: Message, state: FSMContext, slides_path: Path | None):
    data = await state.get_data()
    audio_path = Path(data["audio_path"])
    settings = get_settings()

    files = {"audio": (audio_path.name, audio_path.read_bytes(), "audio/mpeg")}
    if slides_path is not None:
        content_type = "application/pdf" if slides_path.suffix.lower() == ".pdf" else "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        files["slides"] = (slides_path.name, slides_path.read_bytes(), content_type)

    async with httpx.AsyncClient(timeout=300) as client:
        create_resp = await client.post(f"{settings.API_BASE_URL}/api/v1/tasks", files=files)
        create_resp.raise_for_status()
        task_id = create_resp.json()["task_id"]

        while True:
            await asyncio.sleep(5)
            status_resp = await client.get(f"{settings.API_BASE_URL}/api/v1/tasks/{task_id}")
            status_resp.raise_for_status()
            status = status_resp.json()

            if status.get("error"):
                await message.answer(f"Ошибка: {status['error']}")
                break

            await message.answer(_status_text(status.get("stage"), status.get("progress_pct", 0)))

            if status.get("result_path"):
                result_resp = await client.get(f"{settings.API_BASE_URL}/api/v1/tasks/{task_id}/result")
                result_resp.raise_for_status()
                zip_path = audio_path.parent / f"{task_id}.zip"
                zip_path.write_bytes(result_resp.content)
                await message.answer_document(FSInputFile(zip_path))
                break


@router.message(F.text == "/start")
async def start(message: Message, state: FSMContext):
    await state.set_state(ProcessStates.WAIT_AUDIO)
    await message.answer("Отправьте аудиофайл лекции")


@router.message(ProcessStates.WAIT_AUDIO, F.audio)
@router.message(ProcessStates.WAIT_AUDIO, F.document)
async def on_audio(message: Message, state: FSMContext):
    file_obj = message.audio or message.document
    if file_obj is None:
        await message.answer("Нужен аудиофайл")
        return

    work_dir = Path("/tmp/lecturelog-bot") / str(message.from_user.id)
    work_dir.mkdir(parents=True, exist_ok=True)
    audio_path = work_dir / (file_obj.file_name or "lecture.mp3")
    await _download_file(message, file_obj.file_id, audio_path)

    await state.update_data(audio_path=str(audio_path))
    await state.set_state(ProcessStates.WAIT_SLIDES)
    await message.answer("Прикрепите слайды (PDF/PPTX) или отправьте /skip")


@router.message(ProcessStates.WAIT_SLIDES, F.text == "/skip")
async def on_skip_slides(message: Message, state: FSMContext):
    await state.set_state(ProcessStates.PROCESSING)
    await _run_remote_pipeline(message, state, None)
    await state.clear()


@router.message(ProcessStates.WAIT_SLIDES, F.document)
async def on_slides(message: Message, state: FSMContext):
    if message.document is None:
        await message.answer("Нужен файл PDF или PPTX")
        return

    ext = Path(message.document.file_name or "").suffix.lower()
    if ext not in {".pdf", ".pptx"}:
        await message.answer("Поддерживаются только PDF и PPTX")
        return

    work_dir = Path("/tmp/lecturelog-bot") / str(message.from_user.id)
    work_dir.mkdir(parents=True, exist_ok=True)
    slides_path = work_dir / (message.document.file_name or "slides.pdf")
    await _download_file(message, message.document.file_id, slides_path)

    await state.set_state(ProcessStates.PROCESSING)
    await _run_remote_pipeline(message, state, slides_path)
    await state.clear()
