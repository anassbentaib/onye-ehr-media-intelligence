from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Patient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    mrn: str
    name: str
    dob: Date | None = None
    gender: Literal["male", "female", "other", "unknown"] = "unknown"


class CleanRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    source_id: str | None = None
    external_patient_id: str | None = None
    patient: Patient
    kind: Literal["note", "lab", "imaging", "discharge"]
    title: str
    date: Date | None = None
    text: str = Field(min_length=1, max_length=50000)
    encounter_id: str | None = None


class AuditEvent(BaseModel):
    source: str
    row: int
    action: Literal["normalized", "warning", "rejected", "duplicate", "accepted"]
    field: str
    reason: str
    before: str | None = None
    after: str | None = None


class IngestionResult(BaseModel):
    records: list[CleanRecord]
    audit: list[AuditEvent]
    input_count: int
    rejected_count: int
    duplicate_count: int

