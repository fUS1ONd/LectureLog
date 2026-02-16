from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from lecturelog.models import PipelineStatus
from server.task_manager import TaskManager

router = APIRouter(prefix="/api/v1")


def _get_manager(request: Request) -> TaskManager:
    manager = getattr(request.app.state, "task_manager", None)
    if manager is None:
        raise HTTPException(status_code=500, detail="Task manager is not initialized")
    return manager


@router.post("/tasks")
async def create_task(
    request: Request,
    audio: UploadFile = File(...),
    slides: UploadFile | None = File(None),
):
    manager = _get_manager(request)
    task_id = await manager.create_task(audio=audio, slides=slides)
    return {"task_id": task_id}


@router.get("/tasks/{task_id}", response_model=PipelineStatus)
async def get_task_status(task_id: str, request: Request):
    manager = _get_manager(request)
    status = manager.get_status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return status


@router.get("/tasks/{task_id}/result")
async def get_task_result(task_id: str, request: Request):
    manager = _get_manager(request)
    status = manager.get_status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if not status.result_path:
        raise HTTPException(status_code=404, detail="Result is not ready")

    path = Path(status.result_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Result file not found")

    return FileResponse(path=path, filename=path.name, media_type="application/zip")


@router.get("/health")
async def health():
    return {"status": "ok"}
