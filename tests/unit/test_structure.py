from __future__ import annotations

from lecturelog.domain.models import Section, Topic
from lecturelog.infrastructure.export.structure import build_structure


def _targets(output_root, media_kind, n_media, n_slides):
    media_dir = output_root / media_kind
    slides_dir = output_root / "slides"
    media_dir.mkdir(parents=True, exist_ok=True)
    slides_dir.mkdir(parents=True, exist_ok=True)
    media_targets = []
    for i in range(n_media):
        p = media_dir / f"{i + 1:02d}-s.mp3"
        p.write_bytes(b"m")
        media_targets.append(p)
    slide_targets = []
    for i in range(n_slides):
        p = slides_dir / f"slide-{i + 1:02d}.png"
        p.write_bytes(b"s")
        slide_targets.append(p)
    return media_targets, slide_targets


def test_build_structure_maps_topic_to_sections_and_section_to_subtopics(tmp_path):
    output_root = tmp_path / "output"
    media_targets, slide_targets = _targets(output_root, "audio", 2, 3)

    topics = [
        Topic(
            title="Тема 1",
            start="00:00",
            end="05:00",
            sections=[
                Section(
                    title="Подтема 1",
                    start="00:00",
                    end="02:00",
                    content="# md один",
                    slide_indices=[1, 2],
                ),
                Section(
                    title="Подтема 2",
                    start="02:00",
                    end="05:00",
                    content="# md два",
                    slide_indices=[3],
                ),
            ],
        ),
    ]

    tree = build_structure(
        topics=topics,
        media_targets=media_targets,
        slide_targets=slide_targets,
        output_root=output_root,
        task_id="s1",
        media_kind="audio",
    )

    # ЛОВУШКА УРОВНЕЙ: верхний уровень дерева sections == домен Topic.
    assert [s["title"] for s in tree["sections"]] == ["Тема 1"]
    subtopics = tree["sections"][0]["subtopics"]
    # subtopics == домен Section.
    assert [st["title"] for st in subtopics] == ["Подтема 1", "Подтема 2"]

    # source.
    assert tree["source"]["kind"] == "audio"
    assert tree["source"]["title"] is None
    assert tree["source"]["duration"] == "05:00"  # end последней секции

    # media.key — ПОЛНЫЙ реальный ключ MinIO, та же формула что заливка.
    assert subtopics[0]["media"]["key"] == "results/s1/output/audio/01-s.mp3"
    assert subtopics[0]["media"]["kind"] == "audio"
    assert subtopics[0]["media"]["start"] == "00:00"
    assert subtopics[0]["media"]["end"] == "02:00"
    assert subtopics[1]["media"]["key"] == "results/s1/output/audio/02-s.mp3"

    # slide_keys — полные ключи по slide_indices (1-based).
    assert subtopics[0]["slide_keys"] == [
        "results/s1/output/slides/slide-01.png",
        "results/s1/output/slides/slide-02.png",
    ]
    assert subtopics[1]["slide_keys"] == ["results/s1/output/slides/slide-03.png"]

    # content_md — markdown из Section.content (не html).
    assert subtopics[0]["content_md"] == "# md один"
    assert subtopics[1]["content_md"] == "# md два"


def test_build_structure_no_media_and_no_slides_edge(tmp_path):
    output_root = tmp_path / "output"
    media_targets, slide_targets = _targets(output_root, "audio", 0, 0)

    topics = [
        Topic(
            title="Тема",
            start="00:00",
            end="01:00",
            sections=[
                Section(title="Без медиа", start="00:00", end="01:00", content="c"),
            ],
        ),
    ]
    tree = build_structure(
        topics=topics,
        media_targets=media_targets,
        slide_targets=slide_targets,
        output_root=output_root,
        task_id="s2",
        media_kind="audio",
    )
    st = tree["sections"][0]["subtopics"][0]
    assert st["media"] is None  # нет медиа -> null
    assert st["slide_keys"] == []  # нет слайдов -> []
    assert tree["source"]["duration"] == "01:00"


def test_build_structure_empty_topics_duration_none(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir()
    tree = build_structure(
        topics=[],
        media_targets=[],
        slide_targets=[],
        output_root=output_root,
        task_id="s3",
        media_kind="video",
    )
    assert tree["sections"] == []
    assert tree["source"]["duration"] is None
    assert tree["source"]["kind"] == "video"


def test_build_structure_slide_index_out_of_range_skipped(tmp_path):
    output_root = tmp_path / "output"
    media_targets, slide_targets = _targets(output_root, "audio", 1, 1)
    topics = [
        Topic(
            title="Т",
            start="0",
            end="1",
            sections=[
                Section(title="С", start="0", end="1", content="c", slide_indices=[1, 99]),
            ],
        ),
    ]
    tree = build_structure(
        topics=topics,
        media_targets=media_targets,
        slide_targets=slide_targets,
        output_root=output_root,
        task_id="s4",
        media_kind="audio",
    )
    # index 99 вне диапазона -> пропускается; остаётся только slide-01.
    assert tree["sections"][0]["subtopics"][0]["slide_keys"] == [
        "results/s4/output/slides/slide-01.png"
    ]
    # slide_nums выравнены с slide_keys: 99 тоже пропущен.
    assert tree["sections"][0]["subtopics"][0]["slide_nums"] == [1]


def test_build_structure_slide_nums_parallel_to_keys(tmp_path):
    # slide_nums[i] — глобальный номер кадра из маркеров <!-- slide:N -->,
    # соответствует slide_keys[i]; web сопоставляет маркер с URL по нему.
    output_root = tmp_path / "output"
    media_targets, slide_targets = _targets(output_root, "audio", 1, 3)
    topics = [
        Topic(
            title="Т",
            start="0",
            end="1",
            sections=[
                Section(title="С", start="0", end="1", content="c", slide_indices=[2, 3]),
            ],
        ),
    ]
    tree = build_structure(
        topics=topics,
        media_targets=media_targets,
        slide_targets=slide_targets,
        output_root=output_root,
        task_id="s5",
        media_kind="audio",
    )
    st = tree["sections"][0]["subtopics"][0]
    assert st["slide_nums"] == [2, 3]
    assert st["slide_keys"] == [
        "results/s5/output/slides/slide-02.png",
        "results/s5/output/slides/slide-03.png",
    ]
