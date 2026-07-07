from __future__ import annotations

import asyncio
from pathlib import Path

from lecturelog.domain.models import Section
from lecturelog.domain.ports import MediaCutter
from lecturelog.infrastructure.media.ffmpeg_utils import ffmpeg_timestamp
from lecturelog.infrastructure.srt import parse_srt_time


class FfmpegVideoCutter(MediaCutter):
    """Реализация порта MediaCutter: нарезка видео по секциям через ffmpeg.

    Фрагменты перекодируются (H.264/AAC), а не копируются: -c copy при разрезе
    в середине GOP оставляет во фрагменте видео-преролл с discard-флагами от
    предыдущего keyframe (до GOP-длины), тогда как аудио-преролл ~1 c —
    синхронность тогда держится только на mp4 edit list, который часть плееров
    игнорирует → рассинхрон картинки и звука. Перекодирование даёт точные
    границы, старт с keyframe и синк во всех плеерах; заодно нормализует
    VP9/Opus-исходники в универсально играемый H.264/AAC mp4.

    -ss стоит до -i: быстрый входной seek по keyframe + точный декод до нужного
    кадра, вместо декодирования всего префикса файла.
    """

    async def cut(
        self,
        source_path: Path,
        sections: list[Section],
        output_dir: Path,
    ) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        result: list[Path] = []

        for idx, section in enumerate(sections):
            target = output_dir / f"section_{idx + 1:02d}.mp4"
            duration = max(0.0, parse_srt_time(section.end) - parse_srt_time(section.start))
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-ss",
                ffmpeg_timestamp(section.start),
                "-i",
                str(source_path),
                # -t (длительность выхода), а не -to: после входного -ss удобнее
                # не смешивать абсолютную шкалу исходника со шкалой фрагмента
                "-t",
                f"{duration:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",  # аудио опционально — немое видео не должно валить нарезку
                "-sn",
                "-dn",
                # чётные размеры обязательны для yuv420p; vfr не плодит дропы/дубли
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
                "-fps_mode",
                "vfr",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",  # 23 подмыливает мелкий текст слайдов
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
                "-af",
                "aresample=async=1:first_pts=0",
                "-movflags",
                "+faststart",
                str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(stderr.decode("utf-8", errors="ignore"))
            result.append(target)

        return result
