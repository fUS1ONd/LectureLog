from pathlib import Path

import pytest

from lecturelog.application.pipeline_service import PipelineService
from lecturelog.application.progress_plan import ProgressPlan
from lecturelog.domain.enums import PipelineStage, TaskStatus
from lecturelog.domain.media_source import VideoFileSource, VideoUrlSource
from lecturelog.domain.models import Section, Task, Topic
from lecturelog.domain.ports import ExportResult, SlideImage


class InMemoryRepo:
    def __init__(self):
        self.tasks = {}
        self.stages = []

    async def create(self, t):
        self.tasks[t.task_id] = t

    async def get(self, tid):
        return self.tasks.get(tid)

    async def update(self, t):
        self.tasks[t.task_id] = t
        self.stages.append((t.stage, t.progress_pct))

    async def mark_stale_as_interrupted(self):
        return 0


class FakeIngestor:
    def __init__(self):
        self.ingested = None
        self.extracted_from = None

    async def ingest(self, source, output_dir):
        self.ingested = source
        return Path("/work/video.mp4")

    async def extract_audio(self, video_path, output_dir):
        self.extracted_from = video_path
        return Path("/work/extracted/audio.mp3")


class FakeTranscriber:
    def __init__(self):
        self.audio_arg = None

    async def transcribe(self, audio_path, output_dir, on_progress=None, on_usage=None):
        self.audio_arg = audio_path
        if on_progress:
            r = on_progress(100)
            if r is not None:
                await r
        if on_usage:
            r = on_usage({"audio_seconds": 300, "provider": "groq", "model": "whisper-large-v3"})
            if r is not None:
                await r
        return Path("/work/t.srt")


class FakeStructurizer:
    def __init__(self, topics):
        self._t = topics
        self.slide_images_arg = None

    async def structurize(
        self, srt_path, slide_images, output_dir, on_progress=None, on_usage=None
    ):
        self.slide_images_arg = slide_images
        if on_usage:
            r = on_usage({"model": "gemini-3", "prompt": 10, "output": 5})
            if r is not None:
                await r
        return self._t


class RecordingCutter:
    def __init__(self, tag):
        self.tag = tag
        self.source_arg = None

    async def cut(self, source_path, sections, output_dir):
        self.source_arg = source_path
        # Создаём реальные файлы фрагментов на диске (нужно для раскладки/zip).
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        frags = []
        for i in range(len(sections)):
            p = out / f"{self.tag}_{i}.mp4"
            p.write_bytes(b"frag")
            frags.append(p)
        return frags


class FakeExporter:
    """Раскладывает минимальный output/ на диск и возвращает ExportResult (без zip)."""

    def __init__(self):
        self.media_kind = None

    async def export(self, topics, media_fragments, slide_images, output_dir, media_kind):
        self.media_kind = media_kind
        output_root = Path(output_dir) / "output"
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "конспект.md").write_text("# конспект", encoding="utf-8")
        return ExportResult(output_root=output_root, media_targets=[], slide_targets=[])


def _service(repo, ingestor, transcriber, structurizer, audio_cutter, video_cutter, exporter):
    return PipelineService(
        repository=repo,
        transcriber=transcriber,
        structurizer=structurizer,
        audio_cutter=audio_cutter,
        video_cutter=video_cutter,
        ingestor=ingestor,
        exporter=exporter,
        progress_plan_factory=ProgressPlan.for_audio,
    )


@pytest.mark.asyncio
async def test_video_pipeline_ingests_extracts_and_completes(tmp_path):
    repo = InMemoryRepo()
    task = Task(task_id="v1", source_kind="video_url")
    await repo.create(task)
    sec = Section(title="s", start="0:00", end="5:00", content="c", slide_indices=[])
    topics = [Topic(title="T", start="0:00", end="5:00", sections=[sec], slide_indices=[])]

    ingestor = FakeIngestor()
    transcriber = FakeTranscriber()
    audio_cutter = RecordingCutter("audio")
    video_cutter = RecordingCutter("video")
    exporter = FakeExporter()
    service = _service(
        repo, ingestor, transcriber, FakeStructurizer(topics), audio_cutter, video_cutter, exporter
    )

    await service.run(
        task=task,
        source=VideoUrlSource(url="https://youtu.be/x"),
        slide_provider=None,
        work_dir=tmp_path,
    )

    final = await repo.get("v1")
    assert final.status == TaskStatus.DONE
    assert final.progress_pct == 100
    assert transcriber.audio_arg == Path("/work/extracted/audio.mp3")
    assert ingestor.extracted_from == Path("/work/video.mp4")
    assert video_cutter.source_arg == Path("/work/video.mp4")
    assert audio_cutter.source_arg is None
    assert exporter.media_kind == "video"


@pytest.mark.asyncio
async def test_video_stages_include_ingest_and_extract(tmp_path):
    repo = InMemoryRepo()
    task = Task(task_id="v2", source_kind="video_file")
    await repo.create(task)
    sec = Section(title="s", start="0:00", end="5:00", content="c", slide_indices=[])
    topics = [Topic(title="T", start="0:00", end="5:00", sections=[sec], slide_indices=[])]
    service = _service(
        repo,
        FakeIngestor(),
        FakeTranscriber(),
        FakeStructurizer(topics),
        RecordingCutter("audio"),
        RecordingCutter("video"),
        FakeExporter(),
    )
    await service.run(
        task=task,
        source=VideoFileSource(path=tmp_path / "v.mp4"),
        slide_provider=None,
        work_dir=tmp_path,
    )
    seen = [stage for stage, _ in repo.stages]
    assert PipelineStage.VIDEO_INGEST in seen
    assert PipelineStage.AUDIO_EXTRACT in seen
    assert PipelineStage.VIDEO_CUT in seen
    progress = [p for _, p in repo.stages]
    assert progress == sorted(progress)
    assert progress[-1] == 100


class FakeFrameProvider:
    """Фейковый провайдер кадров: get_slides либо отдаёт кадры, либо кидает."""

    def __init__(self, frames=None, error: Exception | None = None):
        self._frames = frames or []
        self._error = error
        self.usage_called = False

    async def get_slides(self, output_dir, on_usage=None):
        if on_usage:
            r = on_usage({"model": "gemini-3-flash", "prompt": 1, "output": 1})
            if r is not None:
                await r
            self.usage_called = True
        if self._error is not None:
            raise self._error
        return self._frames


class FakeDocumentSlideProvider:
    """Фейковый документ-провайдер (старый путь) — фабрика кадров не должна вызываться."""

    def __init__(self, items):
        self._items = items
        self.called = False

    async def get_slides(self, output_dir, on_usage=None):
        self.called = True
        return self._items


@pytest.mark.asyncio
async def test_video_frames_stage_runs_after_transcribe_and_binds(tmp_path):
    repo = InMemoryRepo()
    task = Task(task_id="v3", source_kind="video_file")
    await repo.create(task)
    sec = Section(title="s", start="0:00", end="5:00", content="c", slide_indices=[])
    topics = [Topic(title="T", start="0:00", end="5:00", sections=[sec], slide_indices=[])]

    frame = SlideImage(path=Path("/work/frames/f1.jpg"), timestamp=30.0, caption="Слайд")
    frames_provider = FakeFrameProvider(frames=[frame])

    factory_calls = []

    def factory(video_path, srt_path):
        factory_calls.append((video_path, srt_path))
        return frames_provider

    exporter = FakeExporter()
    structurizer = FakeStructurizer(topics)
    service = _service(
        repo,
        FakeIngestor(),
        FakeTranscriber(),
        structurizer,
        RecordingCutter("audio"),
        RecordingCutter("video"),
        exporter,
    )

    await service.run(
        task=task,
        source=VideoFileSource(path=tmp_path / "v.mp4"),
        slide_provider=None,
        work_dir=tmp_path,
        video_slide_provider_factory=factory,
    )

    final = await repo.get("v3")
    assert final.status == TaskStatus.DONE
    seen = [stage for stage, _ in repo.stages]
    assert PipelineStage.VIDEO_SLIDES in seen
    assert len(factory_calls) == 1
    assert structurizer.slide_images_arg == []
    assert sec.slide_indices == [1]
    # exporter получил кадры на входе
    assert exporter.media_kind == "video"
    assert final.usage["video_slides"]["by_model"]["gemini-3-flash"]["calls"] == 1
    # slides_origin проставлен видео-режимом
    assert final.usage["total"]["slides_origin"] == "video_extracted"


@pytest.mark.asyncio
async def test_video_frames_stage_failure_does_not_fail_task(tmp_path):
    repo = InMemoryRepo()
    task = Task(task_id="v4", source_kind="video_file")
    await repo.create(task)
    sec = Section(title="s", start="0:00", end="5:00", content="c", slide_indices=[])
    topics = [Topic(title="T", start="0:00", end="5:00", sections=[sec], slide_indices=[])]

    frames_provider = FakeFrameProvider(error=RuntimeError("vlm недоступен"))

    def factory(video_path, srt_path):
        return frames_provider

    service = _service(
        repo,
        FakeIngestor(),
        FakeTranscriber(),
        FakeStructurizer(topics),
        RecordingCutter("audio"),
        RecordingCutter("video"),
        FakeExporter(),
    )

    await service.run(
        task=task,
        source=VideoFileSource(path=tmp_path / "v.mp4"),
        slide_provider=None,
        work_dir=tmp_path,
        video_slide_provider_factory=factory,
    )

    final = await repo.get("v4")
    assert final.status == TaskStatus.DONE
    assert sec.slide_indices == []


@pytest.mark.asyncio
async def test_document_slides_still_win_over_video_frames(tmp_path):
    repo = InMemoryRepo()
    task = Task(task_id="v5", source_kind="video_file")
    await repo.create(task)
    sec = Section(title="s", start="0:00", end="5:00", content="c", slide_indices=[])
    topics = [Topic(title="T", start="0:00", end="5:00", sections=[sec], slide_indices=[])]

    doc_item = SlideImage(path=Path("/work/slides/doc1.png"), timestamp=None, caption=None)
    doc_provider = FakeDocumentSlideProvider(items=[doc_item])

    factory_called = False

    def factory(video_path, srt_path):
        nonlocal factory_called
        factory_called = True
        return FakeFrameProvider(frames=[])

    structurizer = FakeStructurizer(topics)
    service = _service(
        repo,
        FakeIngestor(),
        FakeTranscriber(),
        structurizer,
        RecordingCutter("audio"),
        RecordingCutter("video"),
        FakeExporter(),
    )

    await service.run(
        task=task,
        source=VideoFileSource(path=tmp_path / "v.mp4"),
        slide_provider=doc_provider,
        work_dir=tmp_path,
        video_slide_provider_factory=factory,
    )

    final = await repo.get("v5")
    assert final.status == TaskStatus.DONE
    assert doc_provider.called is True
    assert factory_called is False
    assert structurizer.slide_images_arg == [doc_item.path]
    seen = [stage for stage, _ in repo.stages]
    assert PipelineStage.VIDEO_SLIDES not in seen


@pytest.mark.asyncio
async def test_video_frames_markers_placed_into_section_content(tmp_path):
    # После привязки кадров пайплайн расставляет маркеры <!-- slide:N -->
    # внутри content секции (placement, дизайн 2026-07-07).
    repo = InMemoryRepo()
    task = Task(task_id="v6", source_kind="video_file")
    await repo.create(task)
    sec = Section(
        title="s",
        start="0:00",
        end="5:00",
        content="Абзац раз.\n\nАбзац два.",
        slide_indices=[],
    )
    topics = [Topic(title="T", start="0:00", end="5:00", sections=[sec], slide_indices=[])]

    # ts=280 из 300 c -> второй абзац
    frame = SlideImage(path=Path("/work/frames/f1.jpg"), timestamp=280.0, caption="Слайд")
    service = _service(
        repo,
        FakeIngestor(),
        FakeTranscriber(),
        FakeStructurizer(topics),
        RecordingCutter("audio"),
        RecordingCutter("video"),
        FakeExporter(),
    )
    await service.run(
        task=task,
        source=VideoFileSource(path=tmp_path / "v.mp4"),
        slide_provider=None,
        work_dir=tmp_path,
        video_slide_provider_factory=lambda v, s: FakeFrameProvider(frames=[frame]),
    )

    assert (await repo.get("v6")).status == TaskStatus.DONE
    assert sec.content == "Абзац раз.\n\nАбзац два.\n\n<!-- slide:1 -->"
