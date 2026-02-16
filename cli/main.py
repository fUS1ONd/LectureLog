from __future__ import annotations

import argparse
import time
from pathlib import Path
from zipfile import ZipFile

import httpx


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="lecturelog-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    process = subparsers.add_parser("process")
    process.add_argument("--audio", required=True)
    process.add_argument("--slides")
    process.add_argument("--api-url", default="http://localhost:8000")
    process.add_argument("--output", default="./result")
    return parser.parse_args()


def _submit_task(client: httpx.Client, api_url: str, audio: Path, slides: Path | None) -> str:
    files = {"audio": (audio.name, audio.read_bytes(), "audio/mpeg")}
    if slides is not None:
        content_type = "application/pdf" if slides.suffix.lower() == ".pdf" else "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        files["slides"] = (slides.name, slides.read_bytes(), content_type)

    resp = client.post(f"{api_url}/api/v1/tasks", files=files)
    resp.raise_for_status()
    return resp.json()["task_id"]


def _poll_status(client: httpx.Client, api_url: str, task_id: str):
    while True:
        resp = client.get(f"{api_url}/api/v1/tasks/{task_id}")
        resp.raise_for_status()
        status = resp.json()
        stage = status.get("stage") or "PENDING"
        pct = status.get("progress_pct", 0)
        print(f"[{task_id}] {stage}: {pct}%")

        if status.get("error"):
            raise RuntimeError(status["error"])

        if status.get("result_path"):
            return

        time.sleep(3)


def _download_result(client: httpx.Client, api_url: str, task_id: str, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)

    resp = client.get(f"{api_url}/api/v1/tasks/{task_id}/result")
    resp.raise_for_status()

    zip_path = output_dir / "result.zip"
    zip_path.write_bytes(resp.content)

    with ZipFile(zip_path, "r") as archive:
        archive.extractall(output_dir)

    print(f"Результат сохранён: {output_dir}")


def main():
    args = _parse_args()
    if args.command != "process":
        raise RuntimeError("Неизвестная команда")

    audio = Path(args.audio)
    if not audio.exists():
        raise FileNotFoundError(audio)

    slides = Path(args.slides) if args.slides else None
    if slides is not None and not slides.exists():
        raise FileNotFoundError(slides)

    with httpx.Client(timeout=300) as client:
        task_id = _submit_task(client, args.api_url, audio, slides)
        print(f"Создана задача: {task_id}")
        _poll_status(client, args.api_url, task_id)
        _download_result(client, args.api_url, task_id, Path(args.output))


if __name__ == "__main__":
    main()
