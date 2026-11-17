import { api } from "./api.js";
import { h, na } from "./dom.js";
import { renderMarkdown } from "./md.js";

const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
const fmtDate = (s) => {
  if (!s) return h("span", { class: "na" }, "Not published");
  const d = new Date(s.slice(0, 10) + "T00:00:00");
  return isNaN(d) ? s : d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
};
const md = (text) => h("div", { class: "bubble", html: renderMarkdown(text) });
const srcLine = (url, label = "Source: christuniversity.in") => h("p", { class: "srcline" }, url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, label) : label);

export const loading = () => h("div", { class: "page" }, h("div", { class: "grid" }, [1, 2, 3].map(() => h("div", { class: "skeleton" }))));
export const errorState = (e, retry) => h("div", { class: "page" }, h("div", { class: "state err", role: "alert" },
  h("p", {}, e.message || "Something went wrong."), retry ? h("button", { class: "btn", type: "button", onclick: retry }, "Try again") : null));
const empty = (t) => h("div", { class: "state" }, t);
const head = (title, lede) => [h("h1", {}, title), h("p", { class: "lede" }, lede)];
const askBtn = (text, navigate) => h("button", { class: "btn ghost", type: "button", onclick: () => {
  try { sessionStorage.setItem("silc_prefill", text); } catch { /* ignore */ }
  navigate("/");
} }, "Ask the assistant");

// ---------------------------------------------------------------- programmes
export async function programmesPage() {
  const first = await api("/programmes?limit=60");
  const grid = h("div", { class: "grid" });
  const count = h("p", { class: "lede", role: "status" });
  const moreBtn = h("button", { class: "btn ghost more", type: "button", hidden: true }, "Show more");
  const q = h("input", { type: "search", placeholder: "Search programmes (e.g. MBA, computer science)", "aria-label": "Search programmes", style: "min-width:260px" });
  const lvl = h("select", { "aria-label": "Level" }, [["", "All levels"], ["undergraduate", "Undergraduate"], ["postgraduate", "Postgraduate"], ["doctoral", "Doctoral"], ["other", "Other / certificate"]]
    .map(([v, t]) => h("option", { value: v }, t)));
  const campus = h("select", { "aria-label": "Campus" }, [h("option", { value: "" }, "All campuses"), ...first.campuses.map((c) => h("option", { value: c }, c))]);
  let offset = 0, total = 0, timer;

  const card = (p) => h("div", { class: "card link" }, h("a", { class: "cardlink", href: `/programmes/${p.id}`, "data-link": "" },
    h("h3", {}, p.name), h("p", {}, p.campus || ""),
    h("span", { class: "tag" }, cap(p.level)), p.duration ? h("span", { class: "tag" }, p.duration) : null,
    p.application_close ? h("span", { class: "tag ok" }, "Apply by ", fmtDate(p.application_close)) : null));

  async function load(reset) {
    if (reset) { offset = 0; grid.replaceChildren(); }
    const params = new URLSearchParams({ limit: "60", offset: String(offset) });
    if (q.value.trim()) params.set("q", q.value.trim());
    if (lvl.value) params.set("level", lvl.value);
    if (campus.value) params.set("campus", campus.value);
    try {
      const d = await api(`/programmes?${params}`);
      total = d.total; offset += d.programmes.length;
      grid.append(...d.programmes.map(card));
      if (!total) grid.replaceChildren(empty("No programmes match your search. Try a different keyword, level or campus."));
      count.textContent = `${total} programme${total === 1 ? "" : "s"}`;
      moreBtn.hidden = offset >= total;
    } catch (e) { grid.replaceChildren(h("div", { class: "state err", role: "alert" }, e.message)); }
  }
  const reload = () => { clearTimeout(timer); timer = setTimeout(() => load(true), 250); };
  q.addEventListener("input", reload); lvl.addEventListener("change", reload); campus.addEventListener("change", reload);
  moreBtn.addEventListener("click", () => load(false));
  load(true);
  return h("div", { class: "page" }, ...head("Explore programmes", "Programmes listed on the CHRIST website, with campus and application dates."),
    h("div", { class: "toolbar" }, q, lvl, campus), count, grid, moreBtn, srcLine(first.source, "Source: CHRIST programme catalogue"));
}

export async function programmeDetailPage({ navigate, params }) {
  const { programme: p } = await api(`/programmes/${encodeURIComponent(params.id)}`);
  const qf = p.quick_facts || {};
  const fact = (k, v) => h("div", { class: "fact" }, h("small", {}, k), v);
  const links = [h("a", { class: "btn", href: p.url, target: "_blank", rel: "noopener noreferrer" }, "Official programme page")];
  if (p.apply_url) links.push(h("a", { class: "btn ghost", href: p.apply_url, target: "_blank", rel: "noopener noreferrer" }, "Apply online"));
  if (p.brochure_url) links.push(h("a", { class: "btn ghost", href: p.brochure_url, target: "_blank", rel: "noopener noreferrer" }, "Brochure (PDF)"));
  links.push(askBtn(`Tell me about ${p.name}`, navigate));
  const overview = (p.overview || "").split("About the Programme")[1] || p.overview || "";

  const sec = (title, text, open = false) => text ? h("details", { class: "sec", open: open || null }, h("summary", {}, title), md(text)) : null;
  return h("div", { class: "page" }, h("a", { class: "crumb", href: "/programmes", "data-link": "" }, "← All programmes"),
    ...head(p.name, `${cap(p.level)}${p.campus ? " · " + p.campus : ""}`),
    h("div", { class: "toolbar" }, links),
    h("div", { class: "facts" }, fact("Duration", qf.duration || na(null)), fact("Semesters", qf.semesters ?? na(null)), fact("Mode", qf.mode || na(null)),
      fact("Medium", qf.medium || na(null)), fact("Applications open", fmtDate(p.application_open)), fact("Applications close", fmtDate(p.application_close))),
    overview ? h("div", { class: "card", style: "margin-bottom:12px" }, h("h3", {}, "About the programme"), md(overview.trim().slice(0, 1800))) : null,
    sec("Eligibility", p.eligibility, true), sec("Fees", p.fees, true),
    ...p.tabs.map((t) => sec(t.title, t.text)),
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
  const list = h("datalist", { id: "plist" }, all.map((p) => h("option", { value: p.campus ? `${p.name} — ${p.campus}` : p.name })));
  const input = h("input", { list: "plist", placeholder: "Type a programme name…", "aria-label": "Programme", style: "min-width:min(100%,420px)" });
  const pick = () => { const hit = all.find((p) => (p.campus ? `${p.name} — ${p.campus}` : p.name) === input.value); if (hit) onPick(hit); };
  input.addEventListener("change", pick); input.addEventListener("input", pick);
  return { list, input };
}

// ---------------------------------------------------------------- eligibility
export async function eligibilityPage() {
  const out = h("div", { "aria-live": "polite" });
  let chosen = null;
  const pct = h("input", { type: "number", min: "0", max: "100", step: "0.01", placeholder: "e.g. 72", "aria-label": "Your percentage" });
  const go = h("button", { class: "btn", type: "button", disabled: true }, "Show published criteria");
  const run = async () => {
    if (!chosen) return;
    out.replaceChildren(h("div", { class: "skeleton" }));
    try {
      const r = await api("/eligibility", { method: "POST", body: { programme_id: chosen.id, ...(pct.value !== "" ? { percentage: Number(pct.value) } : {}) } });
      const cls = r.result.outcome === "may_not_meet" ? "not" : "unk";
      out.replaceChildren(
        h("div", { class: `result ${cls}` }, h("h3", { style: "margin:0 0 4px" }, r.result.label), h("p", { style: "margin:0" }, r.result.detail), h("div", { class: "disclaimer" }, r.result.disclaimer)),
        h("h2", {}, `Published eligibility — ${r.programme.name}`), r.eligibility ? md(r.eligibility) : empty("No eligibility text is published for this programme."),
        srcLine(r.programme.url));
    } catch (e) { out.replaceChildren(h("div", { class: "state err", role: "alert" }, e.message)); }
  };
  go.addEventListener("click", run);
  const { list, input } = await programmePicker((p) => { chosen = p; go.disabled = false; });
  return h("div", { class: "page" }, ...head("Check my eligibility", "Pick a programme to read its published criteria. Add your percentage for a preliminary comparison."),
    h("div", { class: "card" }, h("div", { class: "field" }, h("label", {}, "Programme"), input, list),
      h("div", { class: "field" }, h("label", {}, "Your percentage (optional)"), pct, h("small", {}, "I only compare it with percentages mentioned in the published text.")), go),
    h("div", { style: "height:16px" }), out);
}

// ---------------------------------------------------------------- admissions / dates
export async function admissionsPage() {
  const d = await api("/dates");
  const sorted = [...d.windows].sort((a, b) => String(a.close || "9").localeCompare(String(b.close || "9")));
  return h("div", { class: "page" }, ...head("Admissions & application dates", "Application windows as listed in the CHRIST programme catalogue."),
    sorted.length ? h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Level", "Campus", "Opens", "Closes", "Programmes"].map((t) => h("th", {}, t)))),
      h("tbody", {}, sorted.map((w) => h("tr", {}, h("td", {}, cap(w.level)), h("td", {}, na(w.campus)), h("td", {}, fmtDate(w.open)), h("td", {}, fmtDate(w.close)), h("td", {}, String(w.programmes))))))) : empty("No application dates were found."),
    srcLine(d.source, "Source: CHRIST programme catalogue"),
    h("p", { class: "lede" }, "Open a programme for its exact dates, eligibility and fees, or ask the assistant how to apply."),
    h("a", { class: "btn", href: "/", "data-link": "" }, "Ask about the application process"));
}

// ---------------------------------------------------------------- fees
export async function feesPage() {
  const box = h("div", {}, empty("Choose a programme to see its published fee structure."));
  const { list, input } = await programmePicker(async (p) => {
    box.replaceChildren(h("div", { class: "skeleton" }));
    try {
      const { programme } = await api(`/programmes/${p.id}`);
      box.replaceChildren(programme.fees ? h("div", {}, h("h2", {}, programme.name), md(programme.fees), srcLine(programme.url)) :
        empty("A fee structure for this programme is not published on the CHRIST website pages I collected. Please contact the Admissions Office."));
    } catch (e) { box.replaceChildren(h("div", { class: "state err", role: "alert" }, e.message)); }
  });
  return h("div", { class: "page" }, ...head("Fee structure", "Fees are shown exactly as published, by applicant category. Missing fees are never estimated."),
    h("div", { class: "toolbar" }, input, list), box);
}

// ---------------------------------------------------------------- topic pages (scraped pages grouped by subject)
function topicPage(name, title, lede) {
  return async function () {
    const d = await api(`/topic/${name}`);
    return h("div", { class: "page" }, ...head(title, lede),
      d.results.length ? h("div", { class: "grid" }, d.results.map((r) => h("div", { class: "card" }, h("h3", {}, r.heading && r.heading !== r.title ? `${r.heading}` : r.title),
        h("p", { class: "srcline", style: "margin:0 0 8px" }, r.title), md(r.excerpt + (r.excerpt.length >= 700 ? "…" : "")),
        h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer" }, r.external ? "Source (external)" : "Read on christuniversity.in →"))))
        : empty("I couldn't find pages on this topic in the information collected from the CHRIST website."));
  };
}
export const scholarshipsPage = topicPage("scholarships", "Scholarships & financial assistance", "Pages from the CHRIST website about scholarships and fee support.");
export const documentsPage = topicPage("documents", "Required documents", "What the CHRIST website says about documents for application and admission.");
export const hostelPage = topicPage("hostel", "Hostel & accommodation", "Hostel and dining information from the CHRIST website.");
export const campusesPage = topicPage("campuses", "Campuses & facilities", "Campus and facilities information from the CHRIST website.");
export const placementsPage = topicPage("placements", "Placements & careers", "Placement and career-support information from the CHRIST website.");
export const internationalPage = topicPage("international", "International students", "Information for international, NRI and foreign applicants.");

export async function supportPage({ navigate }) {
  const t = await topicPage("contact", "Help & Admissions contact", "Contact information found on the CHRIST website.")();
  t.append(h("div", { class: "disclaimer" }, "This assistant is unofficial. It can't create support tickets, verify payments or documents, or change your application. Please contact the CHRIST Admissions Office directly for those."),
    h("p", {}), askBtn("How do I contact the Admissions Office?", navigate),
    h("h2", {}, "Privacy"), h("p", {}, "Questions you type are processed by the configured AI service to understand what you ask. Do not enter passwords, OTPs, card details or identity numbers."));
  return t;
}
