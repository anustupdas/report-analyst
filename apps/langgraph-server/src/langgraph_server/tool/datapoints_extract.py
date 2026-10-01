"""Structured FTE + sustainability goals models and helpers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FteDatapoint(BaseModel):
    value: float | None = Field(default=None, description="Numeric FTE / headcount value, or null if unknown.")
    unit: str | None = Field(default="FTE", description="Unit label, usually FTE.")
    as_of: str | None = Field(default=None, description="Date or period label if stated, else null.")
    verbatim: str | None = Field(default=None, description="Short verbatim quote from the report.")
    page: int | None = Field(default=None, description="Page number of the quote, if known.")


class SustainabilityGoal(BaseModel):
    label: str = Field(description="Short name of the goal.")
    target: str | None = Field(default=None, description="Target wording or metric.")
    deadline: str | None = Field(default=None, description="Deadline / year if stated.")
    verbatim: str | None = Field(default=None, description="Short verbatim quote from the report.")
    page: int | None = Field(default=None, description="Page number of the quote, if known.")


class KeyDatapoints(BaseModel):
    fte: FteDatapoint | None = Field(default=None, description="FTE / headcount extraction, or null if unknown.")
    sustainability_goals: list[SustainabilityGoal] = Field(
        default_factory=list,
        description="Zero or more sustainability / climate / ESG goals.",
    )


def datapoints_payload(result: KeyDatapoints) -> dict[str, Any]:
    return result.model_dump(mode="json")


def is_empty_datapoints(result: KeyDatapoints) -> bool:
    fte_missing = result.fte is None or result.fte.value is None
    goals_missing = not result.sustainability_goals
    return fte_missing and goals_missing
