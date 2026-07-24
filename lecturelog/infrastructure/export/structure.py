from __future__ import annotations

from pathlib import Path

from lecturelog.domain.models import Topic


def result_key(path: Path, output_root: Path, task_id: str) -> str:
    """ЕДИНАЯ формула ключа MinIO для объекта результата.

    Один источник истины для заливки объектов и для structure.json —
    ключи в дереве обязаны совпадать с реально залитыми объектами.
    """
    rel = Path(path).relative_to(output_root).as_posix()
    return f"results/{task_id}/output/{rel}"


def transcript_result_key(task_id: str) -> str:
    """Ключ SRT в постоянном результате задачи.

    После успешной обработки worker удаляет локальный workspace, поэтому
    endpoint транскрипта для завершённой задачи читает этот объект из MinIO.
    """
    return f"results/{task_id}/output/transcript.srt"


def build_structure(
    topics: list[Topic],
    media_targets: list[Path],
    slide_targets: list[Path],
    output_root: Path,
    task_id: str,
    media_kind: str,
) -> dict:
    """Сериализовать домен Topic/Section в нейтральное дерево structure.json.

    ЛОВУШКА УРОВНЕЙ:
      - structure.sections  <- topics      (домен Topic, ВЕРХНИЙ уровень)
      - subtopics           <- topic.sections (домен Section)
    media.key / slide_keys строятся ТОЙ ЖЕ формулой ключа (result_key),
    что и реальная заливка -> 100% совпадение с объектами MinIO.
    content_md = Section.content (markdown, как вставляется в конспект.md).
    """
    # Плоский список секций в порядке обхода — для сопоставления с media_targets
    # (медиа нумеруются глобально по плоскому списку секций, как в exporter).
    all_sections = [s for t in topics for s in t.sections]

    # source.duration — end последней секции; пустые темы/секции -> None.
    duration = all_sections[-1].end if all_sections else None

    sections_tree: list[dict] = []
    global_idx = 0
    for topic in topics:
        # Уровень структуры sections == домен Topic.
        subtopics: list[dict] = []
        for section in topic.sections:
            # Уровень subtopics == домен Section.
            media = None
            if global_idx < len(media_targets):
                media = {
                    "kind": media_kind,
                    "start": section.start,
                    "end": section.end,
                    "key": result_key(media_targets[global_idx], output_root, task_id),
                }

            # slide_nums[i] соответствует slide_keys[i] — глобальный номер кадра,
            # тот же N, что в маркерах <!-- slide:N --> внутри content_md.
            slide_keys: list[str] = []
            slide_nums: list[int] = []
            for slide_idx in section.slide_indices:
                pos = slide_idx - 1  # slide_indices 1-based
                if 0 <= pos < len(slide_targets):
                    slide_keys.append(result_key(slide_targets[pos], output_root, task_id))
                    slide_nums.append(slide_idx)

            subtopics.append(
                {
                    "title": section.title,
                    "media": media,
                    "slide_keys": slide_keys,
                    "slide_nums": slide_nums,
                    "content_md": section.content,
                }
            )
            global_idx += 1

        sections_tree.append({"title": topic.title, "subtopics": subtopics})

    return {
        "source": {"title": None, "kind": media_kind, "duration": duration},
        "sections": sections_tree,
    }
