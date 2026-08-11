from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class HalResponse(BaseModel):
    """
    Base schema for HAL/HATEOAS-style responses.

    Uses `links` internally, but serializes as `_links`.
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

    links: dict[str, dict[str, str]] | None = Field(
        default=None,
        alias="_links",
    )


def build_links(
    paper_id: uuid.UUID | str,
) -> dict[str, dict[str, str]]:
    pid = str(paper_id)

    return {
        "self": {"href": f"/papers/{pid}"},
        "citations": {"href": f"/papers/{pid}/citations"},
        "authors": {"href": f"/papers/{pid}/authors"},
        "similar": {"href": f"/papers/{pid}/similar"},
    }
