from __future__ import annotations

import asyncio
import inspect
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lecturelog.domain.exceptions import InvalidSlidesDocument
from lecturelog.domain.ports import ProgressCallback, SlideProvider, UsageCallback
from lecturelog.domain.slides import NativeTextQuality, SlideAsset


@dataclass(frozen=True)
class _RenderedPage:
    slide_num: int
    path: Path
    text: str
    text_quality: NativeTextQuality


def _text_quality(text: str) -> NativeTextQuality:
    compact = " ".join(text.split())
    if not compact:
        return "none"
    alnum_count = sum(ch.isalnum() for ch in compact)
    return "good" if alnum_count >= 40 else "sparse"


async def _emit_progress(on_progress: ProgressCallback | None, value: int) -> None:
    if on_progress is None:
        return
    maybe_awaitable = on_progress(value)
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


async def _convert_pdf_to_png(pdf_path: Path, output_dir: Path) -> list[_RenderedPage]:
    def _render() -> list[_RenderedPage]:
        try:
            import pymupdf  # type: ignore[import-not-found]
        except ModuleNotFoundError:  # pragma: no cover
            import fitz as pymupdf  # type: ignore[import-not-found]

        try:
            with pymupdf.open(str(pdf_path)) as doc:
                if len(doc) == 0:
                    raise InvalidSlidesDocument("документ не содержит страниц")
                pages: list[_RenderedPage] = []
                for page_idx in range(len(doc)):
                    page = doc[page_idx]
                    text = page.get_text("text").strip()
                    pixmap = page.get_pixmap(dpi=200, alpha=False)
                    out_path = output_dir / f"slide-{page_idx + 1:02d}.png"
                    pixmap.save(str(out_path))
                    if not out_path.is_file() or out_path.stat().st_size == 0:
                        raise InvalidSlidesDocument(f"страница {page_idx + 1} не отрендерилась")
                    pages.append(
                        _RenderedPage(page_idx + 1, out_path, text, _text_quality(text))
                    )
                return pages
        except InvalidSlidesDocument:
            raise
        except Exception as exc:
            raise InvalidSlidesDocument(str(exc)) from exc

    try:
        return await asyncio.to_thread(_render)
    except Exception:
        for partial in output_dir.glob("slide-*.png"):
            partial.unlink(missing_ok=True)
        raise


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


async def _convert_pptx_to_png(pptx_path: Path, output_dir: Path) -> list[_RenderedPage]:
    with tempfile.TemporaryDirectory(prefix="lecturelog-slides-") as tmp:
        tmp_dir = Path(tmp)
        try:
            pdf_path = await _run_soffice_convert(pptx_path, tmp_dir)
            return await _convert_pdf_to_png(pdf_path, output_dir)
        except InvalidSlidesDocument:
            raise
        except Exception as exc:
            raise InvalidSlidesDocument(str(exc)) from exc


async def render_preview(asset: SlideAsset, output_path: Path, max_side: int = 1280) -> Path:
    """Build a bounded catalog preview without modifying the 200-DPI export asset."""
    def _resize() -> None:
        try:
            from PIL import Image
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise InvalidSlidesDocument("Pillow недоступен для preview") from exc
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(asset.path) as image:
            image.thumbnail((max_side, max_side))
            image.convert("RGB").save(output_path, format="JPEG", quality=85)

    await asyncio.to_thread(_resize)
    return output_path


class DocumentSlideProvider(SlideProvider):
    """Реализация порта SlideProvider для документов: PDF или PPTX → PNG.

    PDF рендерится напрямую через pymupdf (dpi=200); PPTX сначала
    конвертируется в PDF через LibreOffice (soffice), затем рендерится.
    """

    def __init__(self, slides_path: Path) -> None:
        self._slides_path = Path(slides_path)

    async def get_slides(
        self,
        output_dir: Path,
        on_progress: ProgressCallback | None = None,
        on_usage: UsageCallback | None = None,
    ) -> list[SlideAsset]:
        # Документ-провайдер не тратит LLM-токены: on_usage принимается ради
        # единообразия порта, но не используется (стадия document без by_model).
        output_dir.mkdir(parents=True, exist_ok=True)
        await _emit_progress(on_progress, 10)

        suffix = self._slides_path.suffix.lower()
        if suffix == ".pdf":
            pages = await _convert_pdf_to_png(self._slides_path, output_dir)
        elif suffix == ".pptx":
            pages = await _convert_pptx_to_png(self._slides_path, output_dir)
        else:
            raise ValueError(f"Неподдерживаемый формат слайдов: {self._slides_path.suffix}")

        await _emit_progress(on_progress, 100)
        expected = list(range(1, len(pages) + 1))
        if [page.slide_num for page in pages] != expected:
            shutil.rmtree(output_dir, ignore_errors=True)
            raise InvalidSlidesDocument("нарушен непрерывный порядок страниц")
        return [
            SlideAsset(
                slide_num=page.slide_num,
                path=page.path,
                origin="document",
                extracted_text=page.text,
                native_text_quality=page.text_quality,
            )
            for page in pages
        ]
