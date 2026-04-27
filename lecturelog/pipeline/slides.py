from __future__ import annotations

import asyncio
import inspect
import tempfile
from pathlib import Path
from typing import Any, Callable


async def _emit_progress(on_progress: Callable[[int], Any], value: int) -> None:
    maybe_awaitable = on_progress(value)
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


async def _convert_pdf_to_png(pdf_path: Path, output_dir: Path) -> list[Path]:
    def _render() -> list[Path]:
        try:
            import pymupdf  # type: ignore[import-not-found]
        except ModuleNotFoundError:  # pragma: no cover
            import fitz as pymupdf  # type: ignore[import-not-found]

        doc = pymupdf.open(str(pdf_path))
        images: list[Path] = []
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            pixmap = page.get_pixmap(dpi=200)
            out_path = output_dir / f"slide-{page_idx + 1:02d}.png"
            pixmap.save(str(out_path))
            images.append(out_path)
        doc.close()
        return images

    return await asyncio.to_thread(_render)


async def _run_soffice_convert(pptx_path: Path, out_dir: Path) -> Path:
    process = await asyncio.create_subprocess_exec(
        "soffice",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(pptx_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"LibreOffice завершился с ошибкой: {stderr.decode('utf-8', errors='ignore')}"
        )

    direct_pdf = out_dir / f"{pptx_path.stem}.pdf"
    if direct_pdf.exists():
        return direct_pdf

    pdf_candidates = sorted(out_dir.glob("*.pdf"))
    if not pdf_candidates:
        raise RuntimeError("LibreOffice не создал PDF из PPTX")
    return pdf_candidates[0]


async def _convert_pptx_to_png(pptx_path: Path, output_dir: Path) -> list[Path]:
    with tempfile.TemporaryDirectory(prefix="lecturelog-slides-") as tmp:
        tmp_dir = Path(tmp)
        pdf_path = await _run_soffice_convert(pptx_path, tmp_dir)
        return await _convert_pdf_to_png(pdf_path, output_dir)


async def convert_slides(path: Path, output_dir: Path, on_progress: Callable[[int], Any]) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    await _emit_progress(on_progress, 10)

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        images = await _convert_pdf_to_png(path, output_dir)
    elif suffix == ".pptx":
        images = await _convert_pptx_to_png(path, output_dir)
    else:
        raise ValueError(f"Неподдерживаемый формат слайдов: {path.suffix}")

    await _emit_progress(on_progress, 100)
    return images

