"""Pydantic models for the MongoDB survey source."""

from datetime import datetime

from pydantic import BaseModel, Field


class CandidateLabel(BaseModel):
    """A question label the researcher can choose to segment by."""

    label: str = Field(description="Human-readable question label (becomes an Excel column)")
    type: str | None = Field(default=None, description="Question type, e.g. checkBoxes / radio / text")
    question_text: str | None = Field(default=None, description="Full question wording, when resolvable")


class SurveyCounts(BaseModel):
    total: int = Field(description="All respondents for this survey")
    submitted: int = Field(description="Respondents with status == 'submitted'")
    usable: int = Field(description="Submitted respondents that are not excluded")


class SurveyListItem(BaseModel):
    id: str
    name: str
    type: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    is_active: bool | None = None


class SurveyDetail(BaseModel):
    id: str
    name: str
    counts: SurveyCounts
    candidate_labels: list[CandidateLabel] = Field(default_factory=list)
    date_bounds: dict[str, str | None] | None = Field(
        default=None, description="Min/max submitDate over the eligible cohort, for the date picker"
    )
    wave_capable: bool = Field(default=False, description="True when the survey has 2+ collection waves (gaps in the submission timeline)")
    detected_waves: list[dict] | None = Field(
        default=None, description="Detected waves: [{label, period, date_from, date_to, n}], time-ordered"
    )
