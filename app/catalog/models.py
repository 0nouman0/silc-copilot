"""The in-memory catalog: a tree of frozen dataclasses rooted at one `Catalog`.

Every text item carries provenance, nothing can be modified after the build, and nothing holds a fee, an application date,
a seat count or personal data (those are ERP-only facts, see `app/live.py`). Programmes are keyed by `course_id`; a programme
that is listed under several campuses/departments keeps all of them in `placements`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping


class SectionKind(str, Enum):
    OVERVIEW = "overview"; HIGHLIGHTS = "highlights"; WHY_CHOOSE = "why_choose"
    WHAT_YOU_LEARN = "what_you_learn"; CAREERS = "careers"; CAREER_ROLES = "career_roles"
    MODULES = "modules"; ELIGIBILITY = "eligibility"; SELECTION_PROCESS = "selection_process"
    APPLICATION_PROCESS = "application_process"; ADMISSION_PROCESS = "admission_process"
    ADMISSION_QUERIES = "admission_queries"; ENTRANCE_TEST = "entrance_test"
    NRI = "nri"; INTERNATIONAL = "international"; SCHOLARSHIP = "scholarship"
    PROGRAMME_STRUCTURE = "programme_structure"; SYLLABUS = "syllabus"
    OUTCOMES = "outcomes"; OBJECTIVES = "objectives"; PLACEMENT = "placement"
    CANCELLATION_POLICY = "cancellation_policy"; FAQ = "faq"; MISC = "misc"
    # no FEES and no DATES kind: both come only from the ERP


class Audience(str, Enum):
    ALL = "all"; INDIAN = "indian"; NRI = "nri"; INTERNATIONAL = "international"


@dataclass(frozen=True)
class Provenance:
    source: str                       # "replica" | "brochure" | "config"
    table: str | None = None          # "course_related_links"
    row_id: int | None = None         # rel_link_id, content_id, ...
    file: str | None = None           # PDF filename for brochure text
    page: int | None = None           # PDF page
    url: str | None = None            # public page to cite
    content_hash: str = ""            # change detection between builds


@dataclass(frozen=True)
class Table:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Section:
    kind: SectionKind
    title: str                        # original title, e.g. "Eligibility-2027"
    text: str                         # cleaned plain text
    tables: tuple[Table, ...] = ()
    academic_year: str | None = None  # detected from the title, e.g. "2027"
    audience: Audience = Audience.ALL
    shared: bool = False              # True if from course_common_links
    expires_on: date | None = None    # rel_link_expire_date, kept until its meaning is confirmed
    provenance: Provenance | None = None


@dataclass(frozen=True)
class RoadmapPhase:
    title: str
    term_range: str
    description: str
    career_roles: tuple[str, ...] = ()
    published_package: str | None = None   # chrPackage, shown exactly as published


@dataclass(frozen=True)
class Specialization:
    id: int                           # rel_link_id
    name: str
    description: str
    note: str | None = None
    roadmap: tuple[RoadmapPhase, ...] = ()


@dataclass(frozen=True)
class Subject:
    name: str
    category: str
    credits: float | None = None


@dataclass(frozen=True)
class Semester:
    year: str
    term_type: str
    number: int
    title: str
    subjects: tuple[Subject, ...] = ()


@dataclass(frozen=True)
class DocumentRef:
    kind: str                         # brochure | programme_structure | syllabus | placement_brochure | other
    filename: str
    url: str
    extracted: tuple[Section, ...] = ()   # filled by the PDF pipeline


@dataclass(frozen=True)
class EligibilityRule:
    """One reviewed rule per programme and audience (loaded from config/eligibility_rules.yaml)."""
    program_id: int
    audience: Audience
    academic_year: str
    min_qualification: str            # "10+2" | "UG degree" | "PG degree"
    min_percent: float | None = None
    min_cgpa: float | None = None
    required_subjects: tuple[str, ...] = ()
    accepted_boards: tuple[str, ...] | None = None
    entrance_tests: tuple[str, ...] = ()
    relaxations: Mapping[str, float] = field(default_factory=dict)   # category -> percent points
    notes: str = ""
    reviewed_by: str = ""
    reviewed_on: date | None = None
    source_section: Provenance | None = None


@dataclass(frozen=True)
class DataQuality:
    missing: tuple[SectionKind, ...] = ()
    no_erp_id: bool = False
    campus_inferred: bool = False
    expired_sections: int = 0
    unreviewed_eligibility: bool = True


@dataclass(frozen=True)
class Placement:
    campus_id: int
    department_id: int | None


@dataclass(frozen=True)
class Program:
    id: int
    name: str
    code: str | None
    aliases: tuple[str, ...]
    level: str                        # UG | PG | PhD | Diploma | Certificate
    category_id: int
    campus_id: int
    department_id: int | None
    school_id: int | None
    erp_program_id: str | None        # internal only, never shown to applicants
    duration_years: float | None
    term_type: str | None             # "semester" | "trimester" | "year"
    semesters: int | None
    highlights: tuple[str, ...]
    placement_highlights: tuple[str, ...]
    summary: str
    overview: str
    url: str
    sections: Mapping[SectionKind, tuple[Section, ...]]
    specializations: tuple[Specialization, ...]
    curriculum: tuple[Semester, ...]
    documents: tuple[DocumentRef, ...]
    eligibility_rules: tuple[EligibilityRule, ...]
    quality: DataQuality
    placements: tuple[Placement, ...] = ()
    specialization_count: int | None = None
    brochure_file: str | None = None
    offline_flag: int | None = None
    source_modified: date | None = None
    # derived from the stored name (never invented): see app/catalog/normalize.py
    degree_type: str = "Other"        # B.Tech | BSc | BCA | BA | B.Com | BBA | MSc | M.Tech | MBA | PhD ...
    academic_level: str = "Undergraduate"   # Undergraduate | Postgraduate | Doctoral
    field: str = ""                   # e.g. "Computer Science and Engineering"
    specialization: str | None = None  # e.g. "Artificial Intelligence and Machine Learning"
    subjects: tuple[str, ...] = ()
    entry: str = "regular"            # regular | lateral
    dedupe_key: str = ""              # same key = same programme listed under several course ids


@dataclass(frozen=True)
class Campus:
    id: int
    name: str
    short_code: str
    website: str
    address: str
    location: str
    description: str
    admissions_email: str
    phones: tuple[str, ...]
    emails: tuple[str, ...] = ()


@dataclass(frozen=True)
class School:
    id: int
    campus_id: int
    name: str
    description: str


@dataclass(frozen=True)
class Department:
    id: int
    campus_id: int
    school_id: int | None
    name: str
    code: str | None
    intro: str
    overview_page: str | None
    placement_page: str | None
    email: str | None
    phone: str | None


@dataclass(frozen=True)
class TopicItem:
    topic: str                        # "hostel", "scholarship", ...
    campus_id: int | None             # None = university-wide
    title: str
    text: str
    tables: tuple[Table, ...] = ()
    provenance: Provenance | None = None
    link: str | None = None


@dataclass(frozen=True)
class BuildInfo:
    built_at: datetime
    source_checksums: Mapping[str, int]
    counts: Mapping[str, int]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Catalog:
    programs: Mapping[int, Program]
    campuses: Mapping[int, Campus]
    departments: Mapping[int, Department]
    schools: Mapping[int, School]
    topics: Mapping[str, tuple[TopicItem, ...]]
    shared_sections: tuple[Section, ...]
    indexes: Any                      # app.catalog.indexes.Indexes
    build: BuildInfo
    categories: Mapping[int, str] = field(default_factory=dict)

    def program(self, pid: int) -> Program | None:
        return self.programs.get(pid)
