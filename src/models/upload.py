"""Pydantic models for the Excel-upload source."""

from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    name: str = Field(description="Original header text")
    index: int = Field(description="0-based column position")
    sample_values: list[str] = Field(default_factory=list, description="A few distinct non-null samples")
    is_filter: bool = Field(default=False, description="Detected as the status/exclude filter column")


class DetectedFilters(BaseModel):
    status_column: str | None = None
    status_filter_value: str = "submitted"
    exclude_column: str | None = None
    status_detected: bool = False
    exclude_detected: bool = False
    estimated_rows: int | None = None


class UploadResponse(BaseModel):
    upload_id: str
    sheet_name: str
    columns: list[ColumnInfo] = Field(default_factory=list)
    candidate_labels: list[str] = Field(default_factory=list)
    filters: DetectedFilters
    warnings: list[str] = Field(default_factory=list)
