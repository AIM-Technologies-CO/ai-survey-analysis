"""Models for the segmentation run/job lifecycle."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from models.segmentation import ProgressEvent


class WaveWindow(BaseModel):
    label: str = Field(description="Display label for the wave, e.g. '2024' or 'W3'")
    survey_id: str | None = Field(default=None, description="Sibling survey for this wave (name+time family); when set, the whole survey is used and dates are ignored")
    date_from: str | None = Field(default=None, description="Lower submitDate bound 'YYYY-MM-DD' (date-slice waves)")
    date_to: str | None = Field(default=None, description="Upper submitDate bound 'YYYY-MM-DD' (date-slice waves)")


class RunRequest(BaseModel):
    source: Literal["mongo", "upload"] = Field(description="Where the survey data comes from")
    ref: str = Field(description="survey_id (mongo) or upload_id (upload)")
    segment_by: list[str] = Field(default_factory=list, description="Question labels to segment by (empty = let the AI choose; max 3)")
    additional_details: str = Field(default="", description="Free-text guidance for the agent")
    date_from: str | None = Field(default=None, description="Lower submitDate bound 'YYYY-MM-DD' (mongo source only)")
    date_to: str | None = Field(default=None, description="Upper submitDate bound 'YYYY-MM-DD' (mongo source only)")
    include_all: bool = Field(default=True, description="Ignore the date range and use all submissions")
    waves: list[WaveWindow] | None = Field(default=None, description="Two date windows for wave-over-wave comparison (mongo only); overrides the single date filter")


class SuggestAxesRequest(BaseModel):
    source: Literal["mongo", "upload"] = Field(description="Where the survey data comes from")
    ref: str = Field(description="survey_id (mongo) or upload_id (upload)")


class JobCreatedResponse(BaseModel):
    job_id: str
    status: str
    status_url: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    events: list[ProgressEvent] = Field(default_factory=list)
    error: str | None = None
    cost_usd: float | None = None
    num_turns: int = 0
    report_url: str | None = None
    pptx_url: str | None = None
    created_at: float
    updated_at: float
