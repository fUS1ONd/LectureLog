from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable


async def _run_subprocess(*args: str):
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="ignore"))


async def _convert_pptx_to_pdf(path: Path, work_dir: Path) -> Path:
    await _run_subprocess(
        "soffice",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(work_dir),
        str(path),
    )
    candidate = work_dir / f"{path.stem}.pdf"
    if not candidate.exists():
        raise RuntimeError("LibreOffice не создал PDF")
    return candidate


async def _render_pdf_to_png(path: Path, output_dir: Path) -> list[Path]:
    import fitz

    rendered: list[Path] = []
    with fitz.open(path) as doc:
        for page_idx, page in enumerate(doc):
            pix = page.get_pixmap(dpi=200)
            target = output_dir / f"slide-{page_idx + 1:02d}.png"
            pix.save(target)
            rendered.append(target)
    return rendered


async def convert_slides(
    path: Path,
    output_dir: Path,
    on_progress: Callable[[int, str], None],
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    ext = path.suffix.lower()
    on_progress(10, "Подготовка слайдов")

    if ext == ".pdf":
        result = await _render_pdf_to_png(path, output_dir)
        on_progress(100, "Слайды из PDF готовы")
        return result

    if ext == ".pptx":
        on_progress(40, "Конвертация PPTX в PDF")
        pdf_path = await _convert_pptx_to_pdf(path, output_dir)
        on_progress(70, "Рендеринг PNG")
        result = await _render_pdf_to_png(pdf_path, output_dir)
        on_progress(100, "Слайды из PPTX готовы")
        return result

    raise ValueError("Поддерживаются только PDF и PPTX")
