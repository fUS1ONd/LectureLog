from lecturelog.infrastructure.slides.alignment.schemas import (
    CatalogBatchResponse,
    SemanticMatchResponse,
    strict_json_schema,
)


def _objects(node):
    """Все объектные подсхемы, включая вложенные через $defs."""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _objects(item)


def test_strict_schema_requires_every_property():
    """strict-режим провайдера обязывает перечислить все поля в required."""
    schema = strict_json_schema(CatalogBatchResponse)
    objects = list(_objects(schema))
    assert len(objects) >= 2, "схема должна описывать и batch, и запись каталога"
    for obj in objects:
        assert set(obj["required"]) == set(obj["properties"]), obj.get("title")


def test_strict_schema_forbids_extra_properties_and_defaults():
    schema = strict_json_schema(SemanticMatchResponse)
    objects = list(_objects(schema))
    assert objects, "схема не должна быть пустой"
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert all("default" not in prop for prop in obj["properties"].values())


def test_catalog_schema_requires_proper_nouns():
    """Имена собственные — отдельное поле: из них строится справочник написаний."""
    from lecturelog.infrastructure.slides.alignment.schemas import (
        CatalogBatchResponse,
        strict_json_schema,
    )

    schema = strict_json_schema(CatalogBatchResponse)
    entry = schema["$defs"]["CatalogEntryResponse"]

    assert "proper_nouns" in entry["properties"]
    assert "proper_nouns" in entry["required"]
