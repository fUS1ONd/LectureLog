from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def strict_json_schema(model: type[BaseModel]) -> dict:
    """JSON Schema в виде, который принимает strict-режим провайдера.

    Провайдер гарантирует схему, только если каждое поле объявлено обязательным,
    а лишние поля запрещены. Pydantic же выносит в required лишь поля без
    значения по умолчанию, поэтому схему приходится дожимать.
    """
    return _tighten(model.model_json_schema())


def _tighten(node: object) -> object:
    if isinstance(node, list):
        return [_tighten(item) for item in node]
    if not isinstance(node, dict):
        return node
    tightened = {key: _tighten(value) for key, value in node.items() if key != "default"}
    if tightened.get("type") == "object" and "properties" in tightened:
        tightened["required"] = list(tightened["properties"])
        tightened["additionalProperties"] = False
    return tightened


class CatalogEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slide_num: int = Field(ge=1)
    role: Literal["content", "title", "agenda", "section_divider", "closing", "appendix", "blank"]
    title: str | None = None
    visible_text: str = Field(max_length=6000)
    source_concepts: list[str] = Field(default_factory=list, max_length=40)
    transcript_language_terms: list[str] = Field(default_factory=list, max_length=40)
    visual_summary: str = Field(default="", max_length=2000)
    formulas: list[str] = Field(default_factory=list, max_length=40)


class CatalogBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slides: list[CatalogEntryResponse]

    @model_validator(mode="after")
    def unique_slide_nums(self) -> CatalogBatchResponse:
        nums = [item.slide_num for item in self.slides]
        if len(nums) != len(set(nums)):
            raise ValueError("Ответ содержит повторяющиеся slide_num")
        return self


class SemanticMatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slide_num: int = Field(ge=1)
    global_section_id: int = Field(ge=0)
    evidence_block_ids: list[int] = Field(default_factory=list)
    evidence_quote: str | None = None
    semantic_tier: Literal["explicit", "strong", "weak", "none"]

    @model_validator(mode="after")
    def explicit_requires_evidence(self) -> SemanticMatchResponse:
        if self.semantic_tier == "explicit" and (
            not self.evidence_block_ids or not (self.evidence_quote or "").strip()
        ):
            raise ValueError("explicit match требует evidence IDs и цитату")
        return self


class AnchorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slide_num: int = Field(ge=1)
    block_index: int = Field(ge=0)
    side: Literal["before", "after"]
