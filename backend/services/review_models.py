"""Provider-neutral, bounded source review decisions. Provider identity is server-owned."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReviewDimensions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quality: float = Field(ge=0, le=1, allow_inf_nan=False)
    spam_probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    relevance: float = Field(ge=0, le=1, allow_inf_nan=False)


class ReviewSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_token: UUID
    snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["accept", "reject", "abstain"]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    dimensions: ReviewDimensions
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    rationale: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator('reason_codes', 'tags')
    @classmethod
    def bounded_strings(cls, value: list[str]) -> list[str]:
        if any(len(item) > 100 for item in value):
            raise ValueError('each label must be at most 100 characters')
        return value
