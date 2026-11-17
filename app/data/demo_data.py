"""=====================  DEMO / SAMPLE DATA  =====================
Everything here is FICTIONAL sample content for development only. It is not real
university information. Replace by implementing AdmissionsRepository against the
institution's real source. Gaps (None / empty) are deliberate: contacts,
placements and some fees are unknown so the "do not invent facts" behaviour is
exercised.
=================================================================="""
from .models import (Campus, ContactInfo, EligibilityCriteria, FeeStructure, HostelInfo,
                     ImportantDate, PlacementReport, Programme, RequiredDocument, Scholarship)

YEAR = "2027-28"
CUR = "DEMO-INR"


def _P(**k): return Programme.model_validate(k)


PROGRAMMES = [
    _P(id="bsc-cs", name="B.Sc. Computer Science", level="undergraduate", discipline="Computer Science",
       summary="Three-year undergraduate programme covering programming, data structures, databases and software engineering.",
       duration_months=36, modes=["full-time"], medium_of_instruction="English", campus_ids=["main", "city"],
       specializations=["Artificial Intelligence", "Cyber Security"], internship="optional", research_component=False,
       career_paths=["Software developer", "Data analyst", "Systems engineer"],
       curriculum=[
           {"semester": 1, "items": [{"name": "Programming Fundamentals", "type": "core"},
                                     {"name": "Discrete Mathematics", "type": "core"},
                                     {"name": "Programming Lab I", "type": "practical"}]},
           {"semester": 2, "items": [{"name": "Data Structures", "type": "core"},
                                     {"name": "Database Systems", "type": "core"},
                                     {"name": "Data Structures Lab", "type": "practical"}]}]),
    _P(id="bcom", name="B.Com.", level="undergraduate", discipline="Commerce",
       summary="Three-year undergraduate programme in accounting, finance and business law.",
       duration_months=36, modes=["full-time", "part-time"], medium_of_instruction="English", campus_ids=["main"],
       internship="mandatory", research_component=False,
       curriculum=[{"semester": 1, "items": [{"name": "Financial Accounting", "type": "core"},
                                             {"name": "Business Law", "type": "core"}]}]),
    _P(id="mba", name="MBA", level="postgraduate", discipline="Management",
       summary="Two-year postgraduate management programme with finance, marketing and analytics tracks.",
       duration_months=24, modes=["full-time"], medium_of_instruction="English", campus_ids=["main"],
       specializations=["Finance", "Marketing", "Business Analytics"], internship="mandatory", research_component=False,
       career_paths=["Management trainee", "Business analyst", "Marketing manager"],
       curriculum=[{"semester": 1, "items": [{"name": "Managerial Economics", "type": "core"},
                                             {"name": "Organisational Behaviour", "type": "core"},
                                             {"name": "Financial Accounting for Managers", "type": "core"}]}]),
    _P(id="msc-ds", name="M.Sc. Data Science", level="postgraduate", discipline="Data Science",
       summary="Two-year postgraduate programme in statistics, machine learning and data engineering.",
       duration_months=24, modes=["full-time", "online"], medium_of_instruction="English", campus_ids=["main", "city"],
       internship="optional", research_component=True, career_paths=["Data scientist", "ML engineer"],
       curriculum=[{"semester": 1, "items": [{"name": "Probability and Statistics", "type": "core"},
                                             {"name": "Machine Learning", "type": "core"},
                                             {"name": "Data Engineering Lab", "type": "practical"}]}]),
    # Deliberately sparse: shows how missing information is handled.
    _P(id="phd-mgmt", name="Ph.D. (Management)", level="doctoral", discipline="Management",
       summary="Doctoral research programme in management.", modes=["full-time", "part-time"],
       campus_ids=["main"], research_component=True),
]


def _E(**k): return EligibilityCriteria.model_validate(k)


ELIGIBILITY = [  # phd-mgmt intentionally has NO criteria record
    _E(programme_id="bsc-cs", academic_year=YEAR, min_qualification_level="class12",
       min_qualification_text="Class 12 (or equivalent)", min_percentage=50, required_subjects=["Mathematics"],
       allowed_streams=["Science"], entrance_exam={"required": False}, final_year_allowed=True,
       backlogs_allowed=False, foreign_needs_equivalence=True, source_doc_id="demo-eligibility-ug"),
    _E(programme_id="bcom", academic_year=YEAR, min_qualification_level="class12",
       min_qualification_text="Class 12 (or equivalent)", min_percentage=45, entrance_exam={"required": False},
       final_year_allowed=True, foreign_needs_equivalence=True, source_doc_id="demo-eligibility-ug"),
    _E(programme_id="mba", academic_year=YEAR, min_qualification_level="bachelor",
       min_qualification_text="Bachelor's degree in any discipline", min_percentage=50,
       entrance_exam={"required": True, "name": "Sample Management Aptitude Test", "min_score": 40},
       final_year_allowed=True, foreign_needs_equivalence=True, source_doc_id="demo-eligibility-pg"),
    _E(programme_id="msc-ds", academic_year=YEAR, min_qualification_level="bachelor",
       min_qualification_text="Bachelor's degree with Mathematics or Statistics", min_percentage=55,
       required_subjects=["Mathematics"], entrance_exam={"required": False}, final_year_allowed=False,
       backlogs_allowed=False, foreign_needs_equivalence=True, source_doc_id="demo-eligibility-pg"),
]

DATES = [ImportantDate.model_validate(d) for d in [
    dict(id="d1", label="Applications open", date="2027-01-15", status="published", academic_year=YEAR, source_doc_id="demo-calendar"),
    dict(id="d2", applicant_type="domestic", label="Last date to apply", date="2027-05-31", status="published", academic_year=YEAR, source_doc_id="demo-calendar"),
    dict(id="d3", applicant_type="international", label="Last date to apply (international)", date="2027-04-30", status="published", academic_year=YEAR, source_doc_id="demo-calendar"),
    dict(id="d4", programme_id="mba", label="Entrance examination", date="2027-06-12", status="published", academic_year=YEAR, source_doc_id="demo-calendar"),
    dict(id="d5", label="Selection results", date=None, status="not_confirmed", academic_year=YEAR, source_doc_id="demo-calendar"),
    dict(id="d6", label="Classes begin", date="2027-08-02", status="published", academic_year=YEAR, source_doc_id="demo-calendar"),
]]

FEES = [FeeStructure.model_validate(f) for f in [  # bcom and phd-mgmt: no fee record
    dict(programme_id="bsc-cs", academic_year=YEAR, applicant_category="domestic", currency=CUR, application_fee=1000, source_doc_id="demo-fees",
         components=[dict(name="Tuition", amount=90000, frequency="annual", refundable=False),
                     dict(name="Laboratory", amount=8000, frequency="annual", refundable=False),
                     dict(name="Caution deposit", amount=5000, frequency="one-time", refundable=True)]),
    dict(programme_id="mba", academic_year=YEAR, applicant_category="domestic", currency=CUR, application_fee=1500, source_doc_id="demo-fees",
         components=[dict(name="Tuition", amount=250000, frequency="annual", refundable=False),
                     dict(name="Library", amount=None, frequency="annual"),
                     dict(name="Caution deposit", amount=10000, frequency="one-time", refundable=True)]),
    dict(programme_id="msc-ds", academic_year=YEAR, applicant_category="domestic", currency=CUR, application_fee=1200, source_doc_id="demo-fees",
         components=[dict(name="Tuition", amount=140000, frequency="annual", refundable=False),
                     dict(name="Laboratory", amount=12000, frequency="annual", refundable=False)]),
]]

SCHOLARSHIPS = [Scholarship.model_validate(s) for s in [
    dict(id="sch-merit", name="Merit Scholarship (demo)", applicable_levels=["undergraduate", "postgraduate"],
         criteria="Awarded on the basis of the qualifying-examination score, as published by the university.",
         academic_year=YEAR, source_doc_id="demo-scholarships"),
    dict(id="sch-sports", name="Sports Excellence Concession (demo)", applicable_levels=["undergraduate"],
         criteria="For applicants with documented state-level or higher sporting achievement.",
         amount_description="Partial tuition concession (percentage not specified in demo data).",
         separate_application=True, deadline="2027-06-15", academic_year=YEAR, source_doc_id="demo-scholarships"),
]]

DOCUMENTS = [RequiredDocument.model_validate(d) for d in [
    dict(id="photo", name="Passport-size photograph", applies_to=["all"], stage="application", mandatory=True),
    dict(id="id", name="Government photo ID", applies_to=["all"], stage="application", mandatory=True),
    dict(id="c10", name="Class 10 marks card", applies_to=["undergraduate", "postgraduate"], stage="application", mandatory=True),
    dict(id="c12", name="Class 12 marks card", applies_to=["undergraduate", "postgraduate"], stage="application", mandatory=True),
    dict(id="degree", name="Degree certificate / provisional certificate", applies_to=["postgraduate"], stage="admission",
         mandatory=True, notes="Final-year applicants: confirm provisional-document rules with Admissions."),
    dict(id="passport", name="Passport", applies_to=["international"], stage="application", mandatory=True),
    dict(id="equiv", name="Equivalence certificate for foreign qualifications", applies_to=["international"],
         stage="admission", notes="Requirement not confirmed in demo data."),
]]

CAMPUSES = [
    Campus(id="main", name="Main Campus (demo)", facilities=["Library", "Computer laboratories", "Sports ground"], hostel_available=True),
    Campus(id="city", name="City Campus (demo)", facilities=["Library", "Computer laboratories"]),
]

HOSTELS = [HostelInfo.model_validate(dict(
    campus_id="main", source_doc_id="demo-hostel", facilities=["Wi-Fi", "Laundry"],
    room_types=[dict(type="double", annual_fee=60000, currency=CUR), dict(type="triple", annual_fee=48000, currency=CUR)]))]

PLACEMENTS: list[PlacementReport] = []  # none supplied -> assistant must say unavailable

CONTACTS = ContactInfo(office="Admissions Office")  # phone/email/hours/portal not provided by the institution

DOC_TITLES = {
    "demo-eligibility-ug": "Undergraduate Eligibility Criteria (demo)",
    "demo-eligibility-pg": "Postgraduate Eligibility Criteria (demo)",
    "demo-calendar": "Admission Calendar 2027-28 (demo)",
    "demo-fees": "Fee Structure 2027-28 (demo)",
    "demo-scholarships": "Scholarship Policy (demo)",
    "demo-hostel": "Hostel Information (demo)",
}
