import { api } from "./api.js";
import { h, na } from "./dom.js";

const DEMO_TAG = () => h("span", { class: "tag demo" }, "Demo data — not official");
const money = (n, cur) => (n == null ? h("span", { class: "na" }, "Not published") : `${cur ? cur + " " : ""}${Number(n).toLocaleString("en-IN")}`);
const months = (m) => (m == null ? na(null) : m % 12 === 0 ? `${m / 12} years` : `${m} months`);
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

export const loading = () => h("div", { class: "page" }, h("div", { class: "grid" }, [1, 2, 3].map(() => h("div", { class: "skeleton" }))));
export const errorState = (e, retry) => h("div", { class: "page" }, h("div", { class: "state err", role: "alert" },
  h("p", {}, e.message || "Something went wrong."), retry ? h("button", { class: "btn", type: "button", onclick: retry }, "Try again") : null));
const empty = (t) => h("div", { class: "state" }, t);
const head = (title, lede, demo) => [h("h1", {}, title, " ", demo ? DEMO_TAG() : null), h("p", { class: "lede" }, lede)];
const askBtn = (text, navigate) => h("button", { class: "btn ghost", type: "button", onclick: () => {
  try { sessionStorage.setItem("silc_prefill", text); } catch { /* ignore */ }
  navigate("/");
} }, "Ask the copilot");

// ---------------------------------------------------------------- programmes
export async function programmesPage({ navigate }) {
  const d = await api("/programmes");
  const grid = h("div", { class: "grid" });
  const q = h("input", { type: "search", placeholder: "Search programmes", "aria-label": "Search programmes" });
  const lvl = h("select", { "aria-label": "Level" }, [["", "All levels"], ["undergraduate", "Undergraduate"], ["postgraduate", "Postgraduate"], ["doctoral", "Doctoral"]]
    .map(([v, t]) => h("option", { value: v }, t)));
  const draw = () => {
    const text = q.value.trim().toLowerCase();
    const rows = d.programmes.filter((p) => (!lvl.value || p.level === lvl.value) &&
      (!text || `${p.name} ${p.discipline} ${p.specializations.join(" ")}`.toLowerCase().includes(text)));
    grid.replaceChildren(...(rows.length ? rows.map((p) => h("div", { class: "card link" },
      h("a", { class: "cardlink", href: `/programmes/${p.id}`, "data-link": "" },
        h("h3", {}, p.name), h("p", {}, p.summary),
        h("span", { class: "tag" }, cap(p.level)), h("span", { class: "tag" }, months(p.duration_months)),
        p.modes.map((m) => h("span", { class: "tag" }, m))))) : [empty("No programmes match your search. Try a different keyword or level.")]));
  };
  q.addEventListener("input", draw); lvl.addEventListener("change", draw); draw();
  return h("div", { class: "page" }, head("Explore programmes", "Browse undergraduate, postgraduate and doctoral programmes.", d.demo),
    h("div", { class: "toolbar" }, q, lvl), grid);
}

export async function programmeDetailPage({ navigate, params }) {
  const d = await api(`/programmes/${encodeURIComponent(params.id)}`);
  const p = d.programme, c = d.eligibility;
  const row = (k, v) => h("tr", {}, h("th", { scope: "row" }, k), h("td", {}, v));
  return h("div", { class: "page" }, h("a", { class: "crumb", href: "/programmes", "data-link": "" }, "← All programmes"),
    ...head(p.name, p.summary, d.demo),
    h("div", { class: "toolbar" },
      h("a", { class: "btn", href: "/eligibility", "data-link": "" }, "Check my eligibility"), askBtn(`Tell me more about ${p.name}.`, navigate)),
    h("h2", {}, "At a glance"),
    h("div", { class: "tablewrap" }, h("table", {}, h("tbody", {},
      row("Level", cap(p.level)), row("Duration", months(p.duration_months)),
      row("Modes", p.modes.length ? p.modes.join(", ") : na(null)), row("Medium of instruction", na(p.medium_of_instruction)),
      row("Accreditation / approval", na(p.accreditation)),
      row("Specializations", p.specializations.length ? p.specializations.join(", ") : "None listed"),
      row("Internship", na(p.internship)), row("Research component", p.research_component == null ? na(null) : p.research_component ? "Yes" : "No"),
      row("Career paths", p.career_paths ? p.career_paths.join(", ") : na(null))))),
    h("h2", {}, "Published eligibility"),
    c ? h("div", { class: "tablewrap" }, h("table", {}, h("tbody", {},
      row("Minimum qualification", na(c.min_qualification_text)), row("Minimum percentage", c.min_percentage == null ? na(null) : `${c.min_percentage}%`),
      row("Required subjects", c.required_subjects.length ? c.required_subjects.join(", ") : "None listed"),
      row("Entrance examination", c.entrance_exam ? (c.entrance_exam.required ? `Required${c.entrance_exam.name ? ": " + c.entrance_exam.name : ""}` : "Not required") : na(null)),
      row("Academic year", c.academic_year))))
      : empty("Published eligibility criteria are not available for this programme. Please contact the Admissions Office."),
    h("h2", {}, "Fees (domestic)"),
    d.fees ? feeTable(d.fees) : empty("A fee structure is not available for this programme."),
    h("h2", {}, "Curriculum"),
    p.curriculum.length ? p.curriculum.map((s) => h("div", { class: "card", style: "margin-bottom:10px" }, h("h3", {}, `Semester ${s.semester}`),
      s.items.map((i) => h("span", { class: "tag" }, `${i.name} · ${i.type}`)))) : empty("Curriculum details are not available."));
}

function feeTable(f) {
  return h("div", {}, h("p", { class: "lede" }, `Academic year ${f.academic_year} · ${cap(f.applicant_category)} applicants`),
    h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Item", "Amount", "Frequency", "Refundable"].map((t) => h("th", {}, t)))),
      h("tbody", {}, h("tr", {}, h("td", {}, "Application fee"), h("td", {}, money(f.application_fee, f.currency)), h("td", {}, "One-time"), h("td", {}, na(null, "—"))),
        f.components.map((c) => h("tr", {}, h("td", {}, c.name), h("td", {}, money(c.amount, f.currency)), h("td", {}, c.frequency),
          h("td", {}, c.refundable == null ? na(null, "Not stated") : c.refundable ? "Yes" : "No")))))));
}

// ---------------------------------------------------------------- eligibility
export async function eligibilityPage({ navigate }) {
  const d = await api("/programmes");
  const out = h("div", { "aria-live": "polite" });
  const sel = h("select", { id: "programme_id", required: true }, d.programmes.map((p) => h("option", { value: p.id }, p.name)));
  const f = (id, label, input, hint) => h("div", { class: "field" }, h("label", { for: id }, label), input, hint ? h("small", {}, hint) : null);
  const qual = h("select", { id: "q" }, [["", "Prefer not to say"], ["class12", "Class 12 / equivalent"], ["bachelor", "Bachelor's degree"], ["master", "Master's degree"]].map(([v, t]) => h("option", { value: v }, t)));
  const status = h("select", { id: "cs" }, [["", "Not specified"], ["completed", "Completed"], ["final_year", "Final year (result pending)"]].map(([v, t]) => h("option", { value: v }, t)));
  const stream = h("input", { id: "stream", placeholder: "e.g. Science" });
  const subjects = h("input", { id: "subjects", placeholder: "e.g. Mathematics, Physics" });
  const pct = h("input", { id: "pct", type: "number", min: "0", max: "100", step: "0.01" });
  const back = h("select", { id: "bk" }, [["", "Not specified"], ["no", "No backlogs"], ["yes", "I have backlogs"]].map(([v, t]) => h("option", { value: v }, t)));
  const ent = h("select", { id: "ent" }, [["", "Not specified / not applicable"], ["yes", "Yes, I have taken it"], ["no", "No, not yet"]].map(([v, t]) => h("option", { value: v }, t)));
  const score = h("input", { id: "score", type: "number", min: "0", step: "0.01" });
  const country = h("input", { id: "country", placeholder: "e.g. India" });
  const btn = h("button", { class: "btn", type: "submit" }, "Check preliminary eligibility");

  const form = h("form", { class: "card", novalidate: true, onsubmit: async (e) => {
    e.preventDefault(); btn.disabled = true; out.replaceChildren(h("div", { class: "skeleton" }));
    const body = { programme_id: sel.value };
    if (qual.value) body.highest_qualification = qual.value;
    if (status.value) body.completion_status = status.value;
    if (stream.value.trim()) body.stream = stream.value.trim();
    if (subjects.value.trim()) body.subjects = subjects.value.split(",").map((x) => x.trim()).filter(Boolean);
    if (pct.value !== "") body.percentage = Number(pct.value);
    if (back.value) body.has_backlogs = back.value === "yes";
    if (ent.value) body.entrance_taken = ent.value === "yes";
    if (score.value !== "") body.entrance_score = Number(score.value);
    if (country.value.trim()) body.qualification_country = country.value.trim();
    try { out.replaceChildren(resultCard(await api("/eligibility", { method: "POST", body }))); }
    catch (err) { out.replaceChildren(h("div", { class: "state err", role: "alert" }, err.message)); }
    btn.disabled = false;
  } },
  h("div", { class: "two" }, f("programme_id", "Programme", sel), f("q", "Highest qualification", qual)),
  h("div", { class: "two" }, f("stream", "Stream / discipline", stream), f("subjects", "Subjects studied", subjects, "Separate with commas")),
  h("div", { class: "two" }, f("pct", "Marks (percentage)", pct), f("cs", "Completion status", status)),
  h("div", { class: "two" }, f("bk", "Backlogs / supplementary exams", back), f("country", "Country of qualification", country)),
  h("div", { class: "two" }, f("ent", "Entrance examination", ent), f("score", "Entrance score", score)),
  btn);
  return h("div", { class: "page" }, ...head("Check my eligibility", "Share only what you know. I compare it with the published criteria and tell you what matched.", d.demo),
    form, h("div", { style: "height:16px" }), out);
}

function resultCard(r) {
  const o = r.result;
  const cls = o.outcome === "appears_to_meet" ? "met" : o.outcome === "may_not_meet" ? "not" : "unk";
  const tagFor = { met: "ok", not_met: "bad" };
  const label = { met: "Matched", not_met: "Not matched", missing_input: "More information needed", criteria_unavailable: "Criteria not published", needs_verification: "Needs official verification" };
  return h("div", { class: `result ${cls}` }, h("h3", { style: "margin:0 0 4px" }, o.outcome_label),
    h("p", { class: "lede", style: "margin:0 0 10px" }, "Preliminary indication"),
    h("ul", { style: "padding-left:1.1em;margin:0" }, o.checks.map((c) => h("li", {}, h("span", { class: `tag ${tagFor[c.status] || ""}` }, label[c.status]), h("b", {}, c.criterion + ": "), c.detail))),
    r.source ? h("p", { class: "lede", style: "margin:10px 0 0" }, `Source: ${r.source.title}${r.source.academic_year ? " · " + r.source.academic_year : ""}`) : null,
    r.demo ? h("p", {}, DEMO_TAG()) : null, h("div", { class: "disclaimer" }, o.disclaimer));
}

// ---------------------------------------------------------------- admissions / dates
export async function admissionsPage() {
  const d = await api("/dates");
  const fmt = (s) => new Date(s + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
  const sorted = [...d.dates].sort((a, b) => (a.date || "9999").localeCompare(b.date || "9999"));
  return h("div", { class: "page" }, ...head("Admissions & important dates", "Published dates are shown with their status. Unconfirmed dates are marked clearly.", d.demo),
    h("ol", { class: "timeline" }, sorted.map((x) => h("li", { class: x.status === "published" ? "" : "tbc" },
      h("b", {}, x.label), " ", x.status === "published" ? fmt(x.date) : h("span", { class: "tag bad" }, "Date not confirmed"),
      h("div", { class: "lede", style: "margin:0" }, `${x.academic_year}${x.programme_id ? " · " + x.programme_id.toUpperCase() : " · All programmes"}${x.applicant_type !== "all" ? " · " + x.applicant_type : ""}`)))),
    h("p", { class: "lede" }, "Application steps, portal statuses and troubleshooting are available in the assistant."),
    h("a", { class: "btn", href: "/", "data-link": "" }, "Ask about the application process"));
}

// ---------------------------------------------------------------- fees
export async function feesPage() {
  const p = await api("/programmes");
  const box = h("div");
  const sel = h("select", { "aria-label": "Programme" }, p.programmes.map((x) => h("option", { value: x.id }, x.name)));
  const cat = h("select", { "aria-label": "Applicant category" }, [["domestic", "Domestic"], ["international", "International"]].map(([v, t]) => h("option", { value: v }, t)));
  const draw = async () => {
    box.replaceChildren(h("div", { class: "skeleton" }));
    try {
      const r = await api(`/fees/${sel.value}?category=${cat.value}`);
      box.replaceChildren(r.fees ? feeTable(r.fees) : empty("A fee structure for this selection is not available in the current knowledge base. Please contact the Admissions Office."));
    } catch (e) { box.replaceChildren(h("div", { class: "state err", role: "alert" }, e.message)); }
  };
  sel.addEventListener("change", draw); cat.addEventListener("change", draw); draw();
  return h("div", { class: "page" }, ...head("Fee structure", "Amounts are shown with their academic year and applicant category. Missing amounts are never estimated.", p.demo),
    h("div", { class: "toolbar" }, sel, cat), box);
}

// ---------------------------------------------------------------- scholarships
export async function scholarshipsPage({ navigate }) {
  const d = await api("/scholarships");
  return h("div", { class: "page" }, ...head("Scholarships & concessions", "Only published criteria are shown. Eligibility is never assumed.", d.demo),
    d.scholarships.length ? h("div", { class: "grid" }, d.scholarships.map((s) => h("div", { class: "card" }, h("h3", {}, s.name), h("p", {}, s.criteria),
      s.applicable_levels.map((l) => h("span", { class: "tag" }, cap(l))),
      h("p", {}, h("b", {}, "Amount: "), na(s.amount_description)), h("p", {}, h("b", {}, "Separate application: "), s.separate_application == null ? na(null) : s.separate_application ? "Yes" : "No"),
      h("p", {}, h("b", {}, "Deadline: "), na(s.deadline)), h("span", { class: "tag" }, s.academic_year)))) : empty("No scholarship information is available."),
    h("p", {}), askBtn("Are scholarships available?", navigate));
}

// ---------------------------------------------------------------- documents
export async function documentsPage() {
  const list = h("div");
  const sel = h("select", { "aria-label": "Applicant type" }, [["", "All applicants"], ["undergraduate", "Undergraduate"], ["postgraduate", "Postgraduate"], ["doctoral", "Doctoral"], ["international", "International"]].map(([v, t]) => h("option", { value: v }, t)));
  let demo = true;
  const draw = async () => {
    list.replaceChildren(h("div", { class: "skeleton" }));
    try {
      const r = await api(`/documents${sel.value ? "?applicant_type=" + sel.value : ""}`); demo = r.demo;
      list.replaceChildren(r.documents.length ? h("ul", { class: "checklist" }, r.documents.map((d) => h("li", {},
        h("input", { type: "checkbox", id: "doc-" + d.id, "aria-labelledby": "lbl-" + d.id }),
        h("div", { id: "lbl-" + d.id }, h("b", {}, d.name), " ", h("span", { class: "tag" }, cap(d.stage)),
          d.mandatory == null ? h("span", { class: "tag" }, "Requirement not confirmed") : null,
          d.notes ? h("div", { class: "lede", style: "margin:0" }, d.notes) : null)))) : empty("No documents listed for this selection."));
    } catch (e) { list.replaceChildren(h("div", { class: "state err", role: "alert" }, e.message)); }
  };
  sel.addEventListener("change", draw); draw();
  return h("div", { class: "page" }, ...head("Required documents", "Use this checklist to prepare. Ticks stay in your browser only — nothing is uploaded here.", demo),
    h("div", { class: "toolbar" }, sel), list);
}

// ---------------------------------------------------------------- campuses
export async function campusesPage() {
  const d = await api("/campuses");
  return h("div", { class: "page" }, ...head("Campuses & facilities", "Campus and hostel information currently available.", d.demo),
    h("div", { class: "grid" }, d.campuses.map((c) => {
      const hs = d.hostels.find((x) => x.campus_id === c.id);
      return h("div", { class: "card" }, h("h3", {}, c.name), h("p", {}, h("b", {}, "Location: "), na(c.location)),
        h("p", {}, c.facilities.map((f) => h("span", { class: "tag" }, f))),
        h("p", {}, h("b", {}, "Hostel: "), c.hostel_available == null ? na(null, "Not confirmed") : c.hostel_available ? "Available" : "Not available"),
        hs ? h("div", { class: "tablewrap" }, h("table", {}, h("tbody", {}, hs.room_types.map((r) => h("tr", {}, h("td", {}, cap(r.type) + " sharing"), h("td", {}, money(r.annual_fee, r.currency) , " / year")))))) : null);
    })));
}

// ---------------------------------------------------------------- support
export async function supportPage({ navigate }) {
  const d = await api("/contacts"), c = d.contact;
  const row = (k, v) => h("tr", {}, h("th", { scope: "row" }, k), h("td", {}, v == null ? na(null, "Not provided yet") : v));
  return h("div", { class: "page" }, ...head("Help & applicant support", "How to reach the Admissions Office.", d.demo),
    h("div", { class: "card" }, h("h3", {}, c.office), h("div", { class: "tablewrap" }, h("table", {}, h("tbody", {},
      row("Phone", c.phone), row("Email", c.email), row("Office hours", c.hours), row("Applicant portal", c.portal_url))))),
    h("div", { class: "disclaimer" }, "The assistant cannot create support tickets, verify payments or documents, or change your application. For those, please contact the Admissions Office directly."),
    h("p", {}), askBtn("How do I contact the Admissions Office?", navigate),
    h("h2", {}, "Privacy"), h("p", {}, "Questions you type are sent to the configured AI provider to generate answers. Do not enter passwords, OTPs, card details or identity numbers."));
}
