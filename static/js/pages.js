import { api } from "./api.js";
import { h, icon, na } from "./dom.js";
import { paragraphize, renderMarkdown } from "./md.js";

const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
const fmtDate = (s) => {
  if (!s) return "Not published";
  const d = new Date(s.slice(0, 10) + "T00:00:00");
  return isNaN(d) ? s : d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
};
const prose = (text, extra = "") => h("div", { class: `prose ${extra}`.trim(), html: renderMarkdown(paragraphize(text)) });

// open / upcoming / closed, from the catalogue's application window (ISO strings compare correctly)
const windowStatus = (open, close) => {
  const today = new Date().toISOString().slice(0, 10);
  if (close && close < today) return "closed";
  if (open && open > today) return "upcoming";
  return open || close ? "open" : null;
};
const STATUS = {
  open: { label: "Open now", tone: "success", icon: "check-circle" },
  upcoming: { label: "Opens soon", tone: "primary", icon: "hourglass-split" },
  closed: { label: "Closed", tone: "secondary", icon: "x-circle" },
};
const statusBadge = (open, close, long = false) => {
  const st = windowStatus(open, close);
  if (!st) return null;
  const m = STATUS[st];
  const text = !long ? m.label : st === "closed" ? `Closed ${fmtDate(close)}` : st === "upcoming" ? `Opens ${fmtDate(open)}` : `Apply by ${fmtDate(close)}`;
  return h("span", { class: `badge text-bg-${m.tone} fw-medium d-inline-flex align-items-center gap-1` }, icon(m.icon), text);
};
const LEVEL_TONE = { undergraduate: "primary", postgraduate: "success", doctoral: "warning", other: "secondary" };
const levelBadge = (l) => h("span", { class: `badge bg-${LEVEL_TONE[l] || "secondary"}-subtle text-${LEVEL_TONE[l] || "secondary"}-emphasis fw-medium` }, cap(l));

const wrap = (...kids) => h("div", { class: "container-xl py-4 py-lg-5" }, ...kids);
const pageHead = (title, lede, ...actions) => {
  const acts = actions.flat(Infinity).filter(Boolean);  // callers pass single nodes or (nested) arrays of nodes
  return h("div", { class: "mb-4" }, h("h1", { class: "page-title mb-1" }, title), lede ? h("p", { class: "text-secondary mb-0" }, lede) : null,
    acts.length ? h("div", { class: "d-flex flex-wrap gap-2 mt-3" }, acts) : null);
};
const empty = (t, ic = "search") => h("div", { class: "state" }, icon(ic, "fs-2 d-block mb-2"), t);
const srcLine = (url, label = "Source: christuniversity.in") => h("p", { class: "srcline mt-3 mb-0" }, icon("link-45deg", "me-1"),
  url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, label) : label);
const askBtn = (text, navigate) => h("button", { class: "btn btn-outline-primary d-inline-flex align-items-center gap-2", type: "button", onclick: () => {
  try { sessionStorage.setItem("silc_prefill", text); } catch { /* ignore */ }
  navigate("/");
} }, icon("chat-dots"), "Ask the assistant");
const extLink = (href, label, cls = "btn btn-primary", ic = "box-arrow-up-right") => h("a", { class: `${cls} d-inline-flex align-items-center gap-2`, href, target: "_blank", rel: "noopener noreferrer" }, label, icon(ic));

export const loading = () => wrap(h("div", { class: "placeholder-glow" }, h("span", { class: "placeholder col-4 mb-3 d-block", style: "height:2rem" }),
  h("div", { class: "row row-cols-1 row-cols-md-2 row-cols-xl-3 g-3" }, [1, 2, 3, 4, 5, 6].map(() => h("div", { class: "col" }, h("div", { class: "border rounded-3 bg-white p-3" },
    h("span", { class: "placeholder col-5 mb-2 d-block" }), h("span", { class: "placeholder col-10 mb-2 d-block" }), h("span", { class: "placeholder col-7 d-block" })))))));
export const errorState = (e, retry) => wrap(h("div", { class: "alert alert-danger d-flex gap-3 align-items-start", role: "alert" }, icon("exclamation-triangle", "fs-4"),
  h("div", {}, h("div", { class: "fw-semibold" }, "Something went wrong"), h("div", {}, e.message || "Please try again."),
    retry ? h("button", { class: "btn btn-sm btn-danger mt-2", type: "button", onclick: retry }, icon("arrow-clockwise", "me-1"), "Try again") : null)));

// ---------------------------------------------------------------- programmes (numbered pagination, state kept in the URL)
const PAGE_SIZE = 24;

function pager(total, current, onGo) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const nav = h("nav", { class: "pager mt-4", "aria-label": "Pagination" });
  if (pages <= 1) return nav;
  const item = (label, page, { disabled = false, cur = false, aria = null } = {}) => h("li", { class: "page-item" + (cur ? " active" : "") + (disabled ? " disabled" : "") },
    h("button", { type: "button", class: "page-link pg", disabled: disabled || null, "aria-current": cur ? "page" : null, "aria-label": aria, onclick: () => onGo(page) }, label));
  const ul = h("ul", { class: "pagination justify-content-center" });
  ul.append(item("‹ Prev", current - 1, { disabled: current <= 1, aria: "Previous page" }));
  const wanted = new Set([1, pages]);
  for (let d = -2; d <= 2; d++) wanted.add(current + d);                              // window around the current page
  if (current <= 3) for (let i = 1; i <= 5; i++) wanted.add(i);                       // near the start: always show 1-5
  if (current >= pages - 2) for (let i = pages - 4; i <= pages; i++) wanted.add(i);   // near the end: the last five
  let last = 0;
  [...wanted].filter((p) => p >= 1 && p <= pages).sort((x, y) => x - y).forEach((p) => {
    if (p - last > 1) ul.append(h("li", { class: "page-item disabled", "aria-hidden": "true" }, h("span", { class: "page-link" }, "…")));
    ul.append(item(String(p), p, { cur: p === current, aria: `Page ${p}` }));
    last = p;
  });
  ul.append(item("Next ›", current + 1, { disabled: current >= pages, aria: "Next page" }));
  nav.append(ul, h("div", { class: "pginfo" }, `Page ${current} of ${pages}`));
  return nav;
}

export async function programmesPage() {
  const first = await api("/programmes?limit=1");
  const params = new URLSearchParams(location.search);
  const grid = h("div", { class: "row row-cols-1 row-cols-md-2 row-cols-xl-3 g-3" });
  const count = h("p", { class: "text-secondary small mb-3", role: "status" });
  const top = h("div"), bottom = h("div");
  const q = h("input", { type: "search", class: "form-control", placeholder: "Search programmes (e.g. MBA, computer science)", "aria-label": "Search programmes", value: params.get("q") || "" });
  const lvl = h("select", { class: "form-select", "aria-label": "Level" }, [["", "All levels"], ["undergraduate", "Undergraduate"], ["postgraduate", "Postgraduate"], ["doctoral", "Doctoral"], ["other", "Other / certificate"]]
    .map(([v, t]) => h("option", { value: v }, t)));
  const campus = h("select", { class: "form-select", "aria-label": "Campus" }, [h("option", { value: "" }, "All campuses"), ...first.campuses.map((c) => h("option", { value: c }, c))]);
  lvl.value = params.get("level") || ""; campus.value = params.get("campus") || "";
  let page = Math.max(1, parseInt(params.get("page") || "1", 10) || 1), timer;

  const card = (p) => h("div", { class: "col" }, h("a", { class: "card card-link h-100 text-decoration-none text-body", href: `/programmes/${p.id}`, "data-link": "" },
    h("div", { class: "card-body" },
      h("div", { class: "d-flex flex-wrap gap-1 mb-2" }, levelBadge(p.level), p.duration ? h("span", { class: "badge bg-light text-secondary border fw-medium" }, icon("clock", "me-1"), p.duration) : null),
      h("h2", { class: "h6 mb-1 lh-sm" }, p.name),
      h("div", { class: "small text-secondary" }, icon("geo-alt", "me-1"), p.campus || "Campus not listed")),
    h("div", { class: "card-footer bg-transparent border-0 pt-0 pb-3" }, statusBadge(p.application_open, p.application_close, true))));

  async function load(scroll) {
    const sp = new URLSearchParams();
    if (q.value.trim()) sp.set("q", q.value.trim());
    if (lvl.value) sp.set("level", lvl.value);
    if (campus.value) sp.set("campus", campus.value);
    const apiQs = new URLSearchParams(sp);
    apiQs.set("limit", String(PAGE_SIZE)); apiQs.set("offset", String((page - 1) * PAGE_SIZE));
    try {
      const d = await api(`/programmes?${apiQs}`);
      const pages = Math.max(1, Math.ceil(d.total / PAGE_SIZE));
      if (page > pages) { page = pages; return load(scroll); }  // e.g. a stale ?page=99 in the URL
      if (page > 1) sp.set("page", String(page));
      history.replaceState({}, "", sp.toString() ? `/programmes?${sp}` : "/programmes");  // shareable / survives reload
      grid.replaceChildren(...(d.programmes.length ? d.programmes.map(card) : [h("div", { class: "col-12" }, empty("No programmes match your search. Try a different keyword, level or campus."))]));
      const from = d.total ? (page - 1) * PAGE_SIZE + 1 : 0;
      count.textContent = d.total ? `Showing ${from}–${from + d.programmes.length - 1} of ${d.total} programmes` : "0 programmes";
      const go = (n) => { page = n; load(true); };
      top.replaceChildren(pager(d.total, page, go)); bottom.replaceChildren(pager(d.total, page, go));
      if (scroll) count.scrollIntoView({ block: "start" });
    } catch (e) { grid.replaceChildren(h("div", { class: "col-12" }, h("div", { class: "alert alert-danger", role: "alert" }, e.message))); }
  }
  const refilter = () => { clearTimeout(timer); timer = setTimeout(() => { page = 1; load(false); }, 250); };
  q.addEventListener("input", refilter); lvl.addEventListener("change", refilter); campus.addEventListener("change", refilter);
  load(false);
  return wrap(pageHead("Explore programmes", "Programmes listed on the CHRIST website, with campus and application status."),
    h("div", { class: "filterbar mb-3" }, h("div", { class: "row g-2" },
      h("div", { class: "col-12 col-lg-6" }, h("div", { class: "input-group" }, h("span", { class: "input-group-text bg-white" }, icon("search")), q)),
      h("div", { class: "col-6 col-lg-3" }, lvl), h("div", { class: "col-6 col-lg-3" }, campus))),
    count, top, grid, bottom, srcLine(first.source, "Source: CHRIST programme catalogue"));
}

// ---------------------------------------------------------------- programme detail
function accordion(items, id) {
  return h("div", { class: "accordion", id }, items.filter((it) => it.text).map((it, i) => h("div", { class: "accordion-item" },
    h("h2", { class: "accordion-header" }, h("button", { class: "accordion-button" + (it.open ? "" : " collapsed"), type: "button", "data-bs-toggle": "collapse",
      "data-bs-target": `#${id}-${i}`, "aria-expanded": String(!!it.open), "aria-controls": `${id}-${i}` }, it.icon ? icon(it.icon, "me-2 text-primary") : null, it.title)),
    h("div", { id: `${id}-${i}`, class: "accordion-collapse collapse" + (it.open ? " show" : "") }, h("div", { class: "accordion-body" }, prose(it.text))))));
}

export async function programmeDetailPage({ navigate, params }) {
  const { programme: p } = await api(`/programmes/${encodeURIComponent(params.id)}`);
  const qf = p.quick_facts || {};
  const stat = (ic, k, v) => h("div", { class: "col" }, h("div", { class: "stat" }, h("div", { class: "k" }, icon(ic), k), h("div", { class: "v" }, v ?? na(null, "Not listed"))));
  const overview = (p.overview || "").split("About the Programme")[1] || p.overview || "";
  const buttons = [extLink(p.url, "Official programme page")];
  if (p.apply_url) buttons.push(extLink(p.apply_url, "Apply online", "btn btn-outline-primary"));
  if (p.brochure_url) buttons.push(extLink(p.brochure_url, "Brochure (PDF)", "btn btn-outline-primary", "file-earmark-pdf"));
  buttons.push(askBtn(`Tell me about ${p.name}`, navigate));
  const sections = [{ title: "Eligibility", text: p.eligibility, open: true, icon: "patch-check" }, { title: "Fees", text: p.fees, open: true, icon: "cash-stack" },
    ...p.tabs.map((t) => ({ title: t.title, text: t.text, icon: "journal-text" }))];

  return wrap(
    h("nav", { "aria-label": "breadcrumb" }, h("ol", { class: "breadcrumb small" },
      h("li", { class: "breadcrumb-item" }, h("a", { href: "/programmes", "data-link": "" }, "Programmes")), h("li", { class: "breadcrumb-item active", "aria-current": "page" }, p.name))),
    h("div", { class: "mb-4" },
      h("div", { class: "d-flex flex-wrap gap-2 mb-2" }, levelBadge(p.level), p.campus ? h("span", { class: "badge bg-light text-secondary border fw-medium" }, icon("geo-alt", "me-1"), p.campus) : null,
        statusBadge(p.application_open, p.application_close)),
      h("h1", { class: "page-title mb-3" }, p.name), h("div", { class: "d-flex flex-wrap gap-2" }, buttons)),
    h("div", { class: "row row-cols-2 row-cols-md-3 row-cols-xl-6 g-3 mb-4" },
      stat("clock", "Duration", qf.duration), stat("collection", "Semesters", qf.semesters), stat("person-workspace", "Mode", qf.mode),
      stat("translate", "Medium", qf.medium), stat("calendar-event", "Applications open", fmtDate(p.application_open)), stat("calendar-check", "Applications close", fmtDate(p.application_close))),
    overview.trim() ? h("div", { class: "card mb-4" }, h("div", { class: "card-body p-4" }, h("h2", { class: "h5 mb-3" }, "About the programme"), prose(overview.trim().slice(0, 2200)))) : null,
    accordion(sections, "prog"),
    srcLine(p.url, "Source: this programme's page on christuniversity.in"));
}

// ---------------------------------------------------------------- shared programme picker
async function programmePicker(onPick) {
  const all = [];
  for (let off = 0; ; off += 500) {
    const d = await api(`/programmes?limit=500&offset=${off}`);
    all.push(...d.programmes);
    if (all.length >= d.total) break;
  }
  const label = (p) => (p.campus ? `${p.name} — ${p.campus}` : p.name);
  const list = h("datalist", { id: "plist" }, all.map((p) => h("option", { value: label(p) })));
  const input = h("input", { class: "form-control", list: "plist", placeholder: "Type a programme name…", "aria-label": "Programme", autocomplete: "off" });
  const pick = () => { const hit = all.find((p) => label(p) === input.value); if (hit) onPick(hit); };
  input.addEventListener("change", pick); input.addEventListener("input", pick);
  return { list, input: h("div", { class: "input-group" }, h("span", { class: "input-group-text bg-white" }, icon("search")), input, list) };
}

// ---------------------------------------------------------------- eligibility
export async function eligibilityPage({ navigate } = {}) {
  const out = h("div", { "aria-live": "polite" }, empty("Choose a programme to read its published eligibility criteria.", "patch-check"));
  let chosen = null;
  const pct = h("input", { type: "number", class: "form-control", min: "0", max: "100", step: "0.01", placeholder: "e.g. 72", "aria-label": "Your percentage" });
  const go = h("button", { class: "btn btn-primary w-100", type: "button", disabled: true }, "Show published criteria");
  const run = async () => {
    if (!chosen) return;
    out.replaceChildren(h("div", { class: "placeholder-glow" }, h("span", { class: "placeholder col-12 mb-2 d-block", style: "height:5rem" })));
    try {
      const r = await api("/eligibility", { method: "POST", body: { programme_id: chosen.id, ...(pct.value !== "" ? { percentage: Number(pct.value) } : {}) } });
      const tone = r.result.outcome === "may_not_meet" ? "danger" : "warning";
      out.replaceChildren(
        h("div", { class: `result alert alert-${tone} d-flex gap-3 align-items-start`, role: "status" }, icon(tone === "danger" ? "x-octagon" : "patch-exclamation", "fs-4"),
          h("div", {}, h("div", { class: "fw-semibold" }, r.result.label), h("div", {}, r.result.detail), h("div", { class: "disclaimer small mt-2 opacity-75" }, r.result.disclaimer))),
        h("div", { class: "card" }, h("div", { class: "card-body p-4" }, h("h2", { class: "h5 mb-3" }, `Published eligibility — ${r.programme.name}`),
          r.eligibility ? prose(r.eligibility) : empty("No eligibility text is published for this programme."), srcLine(r.programme.url))));
    } catch (e) { out.replaceChildren(h("div", { class: "alert alert-danger", role: "alert" }, e.message)); }
  };
  go.addEventListener("click", run);
  const { input } = await programmePicker((p) => { chosen = p; go.disabled = false; });
  return wrap(pageHead("Check my eligibility", "Pick a programme to read its published criteria. Add your percentage for a preliminary comparison."),
    h("div", { class: "row g-4" },
      h("div", { class: "col-12 col-lg-4" }, h("div", { class: "card" }, h("div", { class: "card-body p-4" },
        h("div", { class: "mb-3" }, h("label", { class: "form-label fw-semibold" }, "Programme"), input),
        h("div", { class: "mb-3" }, h("label", { class: "form-label fw-semibold" }, "Your percentage ", h("span", { class: "text-secondary fw-normal" }, "(optional)")), pct,
          h("div", { class: "form-text" }, "I only compare it with percentages mentioned in the published text.")), go)),
        h("div", { class: "mt-3" }, askBtn("Help me check my eligibility for a programme.", navigate))),
      h("div", { class: "col-12 col-lg-8" }, out)));
}

// ---------------------------------------------------------------- admissions / dates
export async function admissionsPage() {
  const d = await api("/dates");
  const rows = d.windows.map((w) => ({ ...w, st: windowStatus(w.open, w.close) })).filter((w) => w.st)
    .sort((a, b) => (a.st === "closed") - (b.st === "closed") || String(a.close || "9").localeCompare(String(b.close || "9")));
  const count = (s) => rows.filter((r) => r.st === s).length;
  const body = h("tbody");
  const toggle = h("input", { class: "form-check-input", type: "checkbox", role: "switch", id: "showClosed" });
  const draw = () => {
    const shown = rows.filter((r) => toggle.checked || r.st !== "closed");
    body.replaceChildren(...shown.map((w) => h("tr", {}, h("td", { class: "fw-medium" }, cap(w.level)), h("td", {}, w.campus || "—"), h("td", {}, fmtDate(w.open)), h("td", {}, fmtDate(w.close)),
      h("td", {}, statusBadge(w.open, w.close)), h("td", { class: "text-end" }, String(w.programmes)))));
    if (!shown.length) body.append(h("tr", {}, h("td", { colspan: "6", class: "text-center text-secondary py-4" }, "No open or upcoming application windows were found. Tick “Show closed windows” to see earlier ones.")));
  };
  toggle.addEventListener("change", draw); draw();
  const tile = (ic, tone, label, n) => h("div", { class: "col" }, h("div", { class: "stat d-flex align-items-center gap-3" }, h("span", { class: `icon-tile bg-${tone}-subtle text-${tone}-emphasis` }, icon(ic)),
    h("div", {}, h("div", { class: "fs-4 fw-semibold lh-1" }, String(n)), h("div", { class: "small text-secondary" }, label))));
  return wrap(pageHead("Admissions & application dates", "Application windows as listed in the CHRIST programme catalogue."),
    h("div", { class: "row row-cols-1 row-cols-sm-3 g-3 mb-4" }, tile("check-circle", "success", "Open now", count("open")), tile("hourglass-split", "primary", "Opening soon", count("upcoming")), tile("x-circle", "secondary", "Closed", count("closed"))),
    h("div", { class: "form-check form-switch mb-3" }, toggle, h("label", { class: "form-check-label", for: "showClosed" }, "Show closed windows")),
    h("div", { class: "card" }, h("div", { class: "table-responsive" }, h("table", { class: "table table-hover align-middle mb-0" },
      h("thead", { class: "table-light" }, h("tr", {}, ["Level", "Campus", "Opens", "Closes", "Status", "Programmes"].map((t, i) => h("th", { scope: "col", class: i === 5 ? "text-end" : "" }, t)))), body))),
    srcLine(d.source, "Source: CHRIST programme catalogue"),
    h("div", { class: "d-flex flex-wrap gap-2 mt-4" }, h("a", { class: "btn btn-primary", href: "/apply", "data-link": "" }, "How to apply"), h("a", { class: "btn btn-outline-primary", href: "/programmes", "data-link": "" }, "Browse programmes")));
}

// ---------------------------------------------------------------- fees
export async function feesPage({ navigate } = {}) {
  const box = h("div", {}, empty("Choose a programme to see its published fee structure.", "cash-stack"));
  const { input } = await programmePicker(async (p) => {
    box.replaceChildren(h("div", { class: "placeholder-glow" }, h("span", { class: "placeholder col-12 mb-2 d-block", style: "height:6rem" })));
    try {
      const { programme } = await api(`/programmes/${p.id}`);
      box.replaceChildren(programme.fees
        ? h("div", { class: "card" }, h("div", { class: "card-body p-4" }, h("h2", { class: "h5 mb-3" }, programme.name), prose(programme.fees), srcLine(programme.url)))
        : empty("A fee structure for this programme is not published on the CHRIST website pages I collected. Please contact the Admissions Office.", "cash-stack"));
    } catch (e) { box.replaceChildren(h("div", { class: "alert alert-danger", role: "alert" }, e.message)); }
  });
  return wrap(pageHead("Fee structure", "Fees are shown exactly as published, by applicant category. Missing fees are never estimated."),
    h("div", { class: "mb-3", style: "max-width:640px" }, input), box,
    h("div", { class: "mt-4" }, askBtn("Show me the programme fee structure and other charges.", navigate)));
}

// ---------------------------------------------------------------- topic pages (scraped pages grouped by subject)
function topicPage(name, title, lede, toolbar) {
  return async function ({ navigate } = {}) {
    const d = await api(`/topic/${name}`);
    return wrap(pageHead(title, lede, toolbar ? toolbar(d, navigate) : null),
      d.results.length
        ? h("div", { class: "row row-cols-1 row-cols-lg-2 g-3" }, d.results.map((r) => h("div", { class: "col" }, h("div", { class: "card h-100" },
          h("div", { class: "card-body p-4" },
            h("h2", { class: "h6 mb-1" }, r.heading && r.heading !== r.title ? r.heading : r.title),
            h("div", { class: "srcline mb-3" }, r.title),
            prose(r.excerpt + (r.excerpt.length >= 700 ? "…" : ""))),
          h("div", { class: "card-footer bg-transparent border-0 pb-3" },
            h("a", { href: r.url, class: "text-decoration-none fw-medium", target: "_blank", rel: "noopener noreferrer" }, r.external ? "Source (external)" : "Read on christuniversity.in", " ", icon("box-arrow-up-right", "small")))))))
        : empty("I couldn't find pages on this topic in the information collected from the CHRIST website."));
  };
}
export const scholarshipsPage = topicPage("scholarships", "Scholarships & financial assistance", "Pages from the CHRIST website about scholarships and fee support.");
export const documentsPage = topicPage("documents", "Required documents", "What the CHRIST website says about documents for application and admission.");
export const hostelPage = topicPage("hostel", "Hostel & accommodation", "Hostel and dining information from the CHRIST website.");
export const campusesPage = topicPage("campuses", "Campuses & facilities", "Campus and facilities information from the CHRIST website.");
export const placementsPage = topicPage("placements", "Placements & careers", "Placement and career-support information from the CHRIST website.");
export const internationalPage = topicPage("international", "International students", "Information for international, NRI and foreign applicants.");
export const applyPage = topicPage("apply", "How to apply", "Application steps and guidance from the CHRIST website.", (d, navigate) => [
  d.apply_url ? extLink(d.apply_url, "Apply online") : null,
  h("a", { class: "btn btn-outline-primary", href: "/admissions", "data-link": "" }, "Application dates"),
  h("a", { class: "btn btn-outline-primary", href: "/programmes", "data-link": "" }, "Choose a programme"),
  askBtn("Guide me through the application process.", navigate)]);

export async function supportPage({ navigate }) {
  const page = await topicPage("contact", "Help & Admissions contact", "Contact information found on the CHRIST website.", (d, nav) => [askBtn("How do I contact the Admissions Office?", nav)])({ navigate });
  page.append(h("div", { class: "alert alert-warning d-flex gap-3 mt-4 mb-0", role: "note" }, icon("info-circle", "fs-5"),
    h("div", {}, "This assistant is unofficial. It can't create support tickets, verify payments or documents, or change your application. Please contact the CHRIST Admissions Office directly for those.")),
  h("div", { class: "card mt-3" }, h("div", { class: "card-body" }, h("h2", { class: "h6" }, icon("shield-check", "me-2 text-primary"), "Privacy"),
    h("p", { class: "mb-0 small text-secondary" }, "Questions you type are processed by the configured AI service to understand what you ask. Do not enter passwords, OTPs, card details or identity numbers."))));
  return page;
}
