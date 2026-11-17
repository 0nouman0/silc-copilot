"""Domain model. A `None` field always means "not available in the current data
source" — the assistant must report it as unknown and never guess."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Level = Literal["undergraduate", "postgraduate", "doctoral"]
QualLevel = Literal["class12", "bachelor", "master"]


class CurriculumItem(BaseModel):
    name: str
    type: Literal["core", "elective", "practical"]


class SemesterPlan(BaseModel):
    semester: int
    items: list[CurriculumItem]


class Programme(BaseModel):
    id: str
    name: str
    level: Level
    discipline: str
    summary: str
    duration_months: int | None = None
    modes: list[str] = Field(default_factory=list)
    medium_of_instruction: str | None = None
    accreditation: str | None = None
    campus_ids: list[str] = Field(default_factory=list)
    specializations: list[str] = Field(default_factory=list)
    internship: Literal["mandatory", "optional", "none"] | None = None
    research_component: bool | None = None
    career_paths: list[str] | None = None
    curriculum: list[SemesterPlan] = Field(default_factory=list)


class EntranceExam(BaseModel):
    required: bool
    name: str | None = None
    min_score: float | None = None


class EligibilityCriteria(BaseModel):
    programme_id: str
    academic_year: str
    min_qualification_level: QualLevel | None = None
    min_qualification_text: str | None = None
    min_percentage: float | None = None
    required_subjects: list[str] = Field(default_factory=list)
    allowed_streams: list[str] | None = None
    entrance_exam: EntranceExam | None = None
    final_year_allowed: bool | None = None
    backlogs_allowed: bool | None = None
    foreign_needs_equivalence: bool | None = None
    source_doc_id: str


class ImportantDate(BaseModel):
    id: str
    programme_id: str | None = None  # None = applies to all
    applicant_type: Literal["all", "domestic", "international"] = "all"
    label: str
    date: str | None = None  # ISO date; None when unpublished / not confirmed
    status: Literal["published", "not_confirmed"]
    academic_year: str
    source_doc_id: str


class FeeComponent(BaseModel):
    name: str
    amount: float | None = None
    frequency: Literal["one-time", "annual", "semester"]
    refundable: bool | None = None


class FeeStructure(BaseModel):
    programme_id: str
    academic_year: str
    applicant_category: Literal["domestic", "international"]
    currency: str
    application_fee: float | None = None
    components: list[FeeComponent]
    source_doc_id: str


class Scholarship(BaseModel):
    id: str
    name: str
    applicable_levels: list[Level]
    criteria: str
    amount_description: str | None = None
    separate_application: bool | None = None
    deadline: str | None = None
    academic_year: str
    source_doc_id: str


class RequiredDocument(BaseModel):
    id: str
    name: str
    applies_to: list[str]
    stage: Literal["application", "admission"]
    mandatory: bool | None = None
    notes: str | None = None


class Campus(BaseModel):
    id: str
    name: str
    location: str | None = None
    facilities: list[str] = Field(default_factory=list)
    hostel_available: bool | None = None


class RoomType(BaseModel):
    type: Literal["single", "double", "triple"]
    annual_fee: float | None = None
    currency: str


class HostelInfo(BaseModel):
    campus_id: str
    room_types: list[RoomType]
    mess_charges_included: bool | None = None
    facilities: list[str] = Field(default_factory=list)
    registration_deadline: str | None = None
    availability_guaranteed: bool | None = None
    source_doc_id: str


class PlacementReport(BaseModel):
    programme_id: str
    academic_year: str
    cohort: str
    summary: str
    source_doc_id: str


class ContactInfo(BaseModel):
    office: str
    phone: str | None = None
    email: str | None = None
    hours: str | None = None
    portal_url: str | None = None


class SourceRef(BaseModel):
    doc_id: str
    title: str
    demo: bool
    academic_year: str | None = None
