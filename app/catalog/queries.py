"""Fixed, parameterised SQL for the 16 catalog tables. Nothing here is built from user or model text.

`QUERIES` maps a query name to (table, sql). The reader refuses any table that is not in `ALLOWED_TABLES`, so a personal or
operational table (enquiry, log, member_payments, pdp_registrations, admin_rights, ...) can never be read by the builder.
"""
from __future__ import annotations

CORE_CATEGORIES = (1, 2, 8, 17, 22, 27, 28, 29)
_CATS = ",".join(str(c) for c in CORE_CATEGORIES)
_CORE = f"c.course_status='Y' AND c.course_cat_id IN ({_CATS}) AND c.chrDupFlag<>'Y'"

ALLOWED_TABLES = frozenset({
    "courses", "department_courses", "course_category", "campus_master", "settings", "division", "departments",
    "department_specifications", "course_related_links", "course_common_links", "course_info", "programme_roadmap",
    "academic_curriculum_master", "academic_curriculum_details", "campus_fecilities", "content"})

# Tables that are required for a usable catalog; the rest degrade to a warning when the account has no SELECT grant.
REQUIRED = frozenset({"courses", "course_category", "campus_master", "course_related_links", "course_info"})

QUERIES: dict[str, tuple[str, str]] = {
    "programs": ("courses",
                 "SELECT c.course_id, c.department_id, c.course_cat_id, c.campus_id, c.main_campus_deptID, c.chrCourseCode, c.course_name, "
                 "c.course_shortdesc, c.course_description, c.urlparam, c.course_web_prog_id, c.offline, c.intDuration, c.chrTermType, "
                 "c.intSemester, c.intSpecialisations, c.chrOthers, c.chrPlacement, c.chrPgmBrochure, c.course_modified_date "
                 f"FROM courses c WHERE {_CORE} ORDER BY c.course_id"),
    "placements": ("department_courses",
                   "SELECT dc.department_id, dc.course_id, dc.campus_id, dc.main_campus_deptID FROM department_courses dc "
                   f"JOIN courses c ON c.course_id=dc.course_id WHERE {_CORE} ORDER BY dc.course_id, dc.campus_id"),
    "categories": ("course_category",
                   f"SELECT course_cat_id, course_cat_name, course_cat_code, course_cat_description FROM course_category WHERE course_cat_id IN ({_CATS})"),
    "campuses": ("campus_master",
                 "SELECT campus_id, campus_name, short_code, campus_website, campus_address, campus_location, campus_description, "
                 "campus_email1, campus_email2, campus_phone1, campus_phone2, upload_folder FROM campus_master "
                 "WHERE campus_status='Y' AND campus_id<>28 ORDER BY campus_id"),
    "settings": ("settings",
                 "SELECT campus_id, settings_email, settings_phone_number, settings_phone_number2, settings_mobile, settings_address FROM settings"),
    "schools": ("division",
                "SELECT division_id, campus_id, division_name, division_description FROM division WHERE division_status='1'"),
    "departments": ("departments",
                    "SELECT department_id, campus_id, division_id, department_name, department_code, shortDesc, introduction, department_email, "
                    "department_phone1, department_url_param FROM departments WHERE department_status='Y'"),
    "department_pages": ("department_specifications",
                         "SELECT department_id, submenu_id, dept_spec_description FROM department_specifications "
                         "WHERE submenu_id IN (1, 9) AND dept_spec_type='T'"),
    "sections": ("course_related_links",
                 "SELECT r.rel_link_id, r.course_id, r.rel_link_name, r.rel_link_type, r.rel_link_description, r.rel_link_category, "
                 "r.rel_link_added_date, r.rel_link_expire_date, r.sort_order, r.short_description FROM course_related_links r "
                 f"JOIN courses c ON c.course_id=r.course_id WHERE {_CORE} AND r.rel_link_status='Y' AND r.rel_link_category IN ('E','R','O','A') "
                 "ORDER BY r.course_id, r.sort_order, r.rel_link_id"),
    "shared_sections": ("course_common_links",
                        "SELECT intAutoNo, chrName, chrTemplateName, chrType, chrDescription, course_ids, campus_id FROM course_common_links "
                        "WHERE chrStatus='Y' ORDER BY intAutoNo"),
    "details": ("course_info",
                "SELECT i.intAutoNo, i.chrType, i.intCourseID, i.chrTitle, i.chrDescription FROM course_info i "
                f"JOIN courses c ON c.course_id=i.intCourseID WHERE {_CORE} AND i.status=1 AND i.chrType BETWEEN 1 AND 5 ORDER BY i.intAutoNo"),
    "roadmap": ("programme_roadmap",
                "SELECT p.intAutoNo, p.course_id, p.specID, p.chrTitle, p.chrDescription, p.chrShortDesc, p.term_range, p.chrOutcomes, p.chrPackage "
                f"FROM programme_roadmap p JOIN courses c ON c.course_id=p.course_id WHERE {_CORE} AND p.chrStatus='Y' ORDER BY p.course_id, p.specID, p.intAutoNo"),
    "curriculum_master": ("academic_curriculum_master",
                          "SELECT m.intAutoNo, m.course_id, m.campus_id, m.chrYear, m.chrType, m.chrSem, m.chrTitle FROM academic_curriculum_master m "
                          f"JOIN courses c ON c.course_id=m.course_id WHERE {_CORE} AND m.chrStatus='1' ORDER BY m.course_id, m.chrYear, m.chrSem"),
    "curriculum_details": ("academic_curriculum_details",
                           "SELECT d.intAutoNo, d.curriculum_id, d.chrCategory, d.chrSubject, d.credits FROM academic_curriculum_details d "
                           "JOIN academic_curriculum_master m ON m.intAutoNo=d.curriculum_id "
                           f"JOIN courses c ON c.course_id=m.course_id WHERE {_CORE} AND m.chrStatus='1' ORDER BY d.curriculum_id, d.intAutoNo"),
    "facilities": ("campus_fecilities",
                   "SELECT cam_fecilities_id, campus_id, cam_fecilities_name, cam_fecilities_menutype, cam_fecilities_description, "
                   "cam_fecilities_short_description FROM campus_fecilities WHERE cam_fecilities_status='Y' ORDER BY campus_id, cam_fecilities_order, cam_fecilities_id"),
    # published fee sections: read ON DEMAND by app/live.py (never loaded into the catalog); the ids are bound as %s placeholders
    "fees": ("course_related_links",
             "SELECT rel_link_id, course_id, rel_link_name, rel_link_description, rel_link_added_date, rel_link_expire_date FROM course_related_links "
             "WHERE rel_link_category='F' AND rel_link_status='Y' AND rel_link_type='T' AND course_id IN ({ids}) ORDER BY course_id, rel_link_id DESC"),
    # content pages: only the reviewed ids from config/topic_mapping.yaml (the id list is the single parameter set, bound as %s placeholders)
    "topic_pages": ("content",
                    "SELECT content_id, campus_id, content_title, content_shortdesc, content_description, content_url_param, content_updateddate, "
                    "content_category, page_id FROM content WHERE content_status='1' AND content_id IN ({ids})"),
    # candidate generator for the topic review list (never loaded into the catalog)
    "topic_candidates": ("content",
                         "SELECT content_id, campus_id, content_title, content_category, page_id, content_updateddate FROM content WHERE content_status='1' AND "
                         "(content_category IN ('CMS Menu','Hostel and Dining','Students Life in Campus','Admission','International and Placement') "
                         "OR page_id IN ('4','6','7','8')) ORDER BY campus_id, content_category, content_title"),
}
