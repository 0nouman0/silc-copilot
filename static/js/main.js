import { api } from "./api.js";
import { mountChat } from "./chat.js";
import { h } from "./dom.js";
import * as P from "./pages.js";

const state = { meta: null, session: { authenticated: false, user: null, demo_users: [] } };
const $ = (id) => document.getElementById(id);

const NAV = [["/", "Home"], ["/programmes", "Explore Programmes"], ["/admissions", "Admissions"], ["/support", "Help"]];
const QUICK = [["/programmes", "Explore Programmes"], ["/eligibility", "Check Eligibility"], ["/admissions", "Application Dates"],
  ["/fees", "Fee Structure"], ["/scholarships", "Scholarships"], ["/documents", "Documents"], ["/hostel", "Hostel"], ["/campuses", "Campuses"],
  ["/placements", "Placements"], ["/international", "International"], ["/support", "Contact Admissions"]];

const ROUTES = [
  [/^\/$/, "home"],
  [/^\/programmes$/, P.programmesPage],
  [/^\/programmes\/([^/]+)$/, P.programmeDetailPage, ["id"]],
  [/^\/eligibility$/, P.eligibilityPage],
  [/^\/admissions$/, P.admissionsPage],
  [/^\/fees$/, P.feesPage],
  [/^\/scholarships$/, P.scholarshipsPage],
  [/^\/documents$/, P.documentsPage],
  [/^\/hostel$/, P.hostelPage],
  [/^\/campuses$/, P.campusesPage],
  [/^\/placements$/, P.placementsPage],
  [/^\/international$/, P.internationalPage],
  [/^\/support$/, P.supportPage],
];

export function navigate(path) {
  history.pushState({}, "", path);
  render();
}

document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-link], .nav a, .quick a, .brand");
  if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || a.target === "_blank") return;
  const url = new URL(a.href, location.origin);
  if (url.origin !== location.origin) return;
  e.preventDefault();
  navigate(url.pathname);
});
window.addEventListener("popstate", render);

// ---------------------------------------------------------------- header / session
function renderHeader() {
  const path = location.pathname;
  const s = state.session;
  const name = s.user?.name || "Guest";
  const pop = h("div", { class: "menu-pop", hidden: true, role: "menu" });
  const btn = h("button", { type: "button", "aria-haspopup": "menu", "aria-expanded": "false", onclick: () => {
    pop.hidden = !pop.hidden; btn.setAttribute("aria-expanded", String(!pop.hidden));
  } }, h("span", { class: "avatar", "aria-hidden": "true" }, name[0]), s.authenticated ? name : "Sign in");

  if (s.authenticated) {
    pop.append(h("div", { class: "lbl" }, "Signed in as a demo applicant (no live CHRIST system)"),
      h("button", { type: "button", role: "menuitem", onclick: async () => { await api("/session/logout", { method: "POST" }); sessionStorage.removeItem("silc_chat_v1"); await refreshSession(); render(); } }, "Sign out"));
  } else if (s.demo_users.length) {
    pop.append(h("div", { class: "lbl" }, "Demo sign-in (mock applicant records)"), ...s.demo_users.map((u) => h("button", {
      type: "button", role: "menuitem", onclick: async () => { await api("/session/demo-login", { method: "POST", body: { user_id: u.id } }); sessionStorage.removeItem("silc_chat_v1"); await refreshSession(); render(); },
    }, u.name)));
  } else {
    pop.append(h("div", { class: "lbl" }, "Sign-in is provided by the university's authentication system."));
  }
  document.addEventListener("click", (e) => { if (!pop.hidden && !pop.parentElement.contains(e.target)) { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); } });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) { pop.hidden = true; btn.focus(); } });

  $("topbar").replaceChildren(
    h("a", { class: "brand", href: "/" }, h("span", { class: "mark", html: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 19l7-12 7 12"/></svg>' }),
      h("span", {}, "CHRIST Admissions Copilot", h("small", {}, "Unofficial assistant · " + (state.meta?.university || "")))),
    h("nav", { class: "nav", "aria-label": "Primary" }, NAV.map(([href, label]) => h("a", { href, "aria-current": path === href || (href !== "/" && path.startsWith(href)) ? "page" : null }, label))),
    h("div", { class: "menu" }, btn, pop));

  $("quick").replaceChildren(...QUICK.map(([href, label]) => h("a", { href }, label)));
}

function renderChrome() {
  const m = state.meta;
  const b = $("demo-banner");
  const d = m?.data;
  if (d?.ready) { b.hidden = false; b.textContent = `Unofficial assistant. Answers come from CHRIST's public website (christuniversity.in), collected ${String(d.scraped_at || "").slice(0, 10)} · ${d.programmes} programmes, ${d.pages} pages. Please confirm important details with the Admissions Office.`; }
  else if (d) { b.hidden = false; b.textContent = "Website data has not been collected yet. Run: python -m scraper.run"; }
  else b.hidden = true;
  $("foot").replaceChildren("Answers are based on available university information. ",
    h("button", { class: "linkbtn", type: "button", onclick: openPrivacy }, "Privacy & data use"));
}

function openPrivacy() {
  const d = $("privacy");
  d.replaceChildren(h("h2", { id: "privacy-title" }, "Privacy & data use"),
    h("p", {}, "Your messages are sent to the configured AI provider to generate answers and are kept in this browser tab only until you close it or start a new chat."),
    h("p", {}, "Please do not enter passwords, OTPs, card details or identity numbers. This assistant gives preliminary guidance, not official decisions."),
    h("p", {}, "Personal application information is shown only after you sign in, and only for your own account."),
    h("form", { method: "dialog" }, h("button", { class: "btn" }, "Close")));
  d.showModal();
}

async function refreshSession() {
  try { state.session = await api("/session"); } catch { state.session = { authenticated: false, user: null, demo_users: [] }; }
  renderHeader();
}

// ---------------------------------------------------------------- router
let token = 0;
async function render() {
  const my = ++token;
  const path = location.pathname.replace(/\/+$/, "") || "/";
  renderHeader();
  const main = $("main");
  const hit = ROUTES.map(([re, view, names]) => [re.exec(path), view, names]).find(([m]) => m);
  if (!hit) {
    main.replaceChildren(h("div", { class: "page" }, h("div", { class: "state" }, h("h1", {}, "Page not found"), h("a", { class: "btn", href: "/", "data-link": "" }, "Back to home"))));
    document.title = "Not found · CHRIST Admissions Copilot"; return;
  }
  const [m, view, names] = hit;
  document.title = (path === "/" ? "Admissions Assistant" : NAV.concat(QUICK).find(([p]) => p === path)?.[1] || "Programme") + " · CHRIST Admissions Copilot";
  if (view === "home") { mountChat(main, { session: () => state.session, navigate }); return; }
  main.replaceChildren(P.loading());
  const params = {}; (names || []).forEach((n, i) => { params[n] = decodeURIComponent(m[i + 1]); });
  try {
    const node = await view({ navigate, params });
    if (my !== token) return; // a newer navigation won
    main.replaceChildren(node);
    main.focus({ preventScroll: true }); window.scrollTo(0, 0);
  } catch (e) {
    if (my !== token) return;
    main.replaceChildren(e.status === 404 ? h("div", { class: "page" }, h("div", { class: "state" }, h("p", {}, e.message), h("a", { class: "btn", href: "/programmes", "data-link": "" }, "Browse programmes")))
      : P.errorState(e, () => render()));
  }
}

(async function boot() {
  try { state.meta = await api("/meta"); } catch { state.meta = null; }
  renderChrome();
  await refreshSession();
  render();
})();
