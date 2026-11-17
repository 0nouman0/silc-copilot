import { api } from "./api.js";
import { mountChat } from "./chat.js";
import { h, icon } from "./dom.js";
import * as P from "./pages.js";

const state = { meta: null, session: { authenticated: false, user: null, demo_users: [] } };
const $ = (id) => document.getElementById(id);

const NAV = [["/", "Home"], ["/programmes", "Explore Programmes"], ["/admissions", "Admissions"], ["/apply", "How to Apply"], ["/support", "Help"]];
const QUICK = [
  ["/programmes", "Explore Programmes", "compass"], ["/eligibility", "Check Eligibility", "patch-check"], ["/admissions", "Application Dates", "calendar-event"],
  ["/apply", "How to Apply", "pencil-square"], ["/fees", "Fee Structure", "cash-stack"], ["/scholarships", "Scholarships", "award"],
  ["/documents", "Documents", "file-earmark-text"], ["/hostel", "Hostel", "house-door"], ["/campuses", "Campuses", "buildings"],
  ["/placements", "Placements", "briefcase"], ["/international", "International", "globe2"], ["/support", "Contact Admissions", "telephone"],
];

const ROUTES = [
  [/^\/$/, "home"],
  [/^\/programmes$/, P.programmesPage],
  [/^\/programmes\/([^/]+)$/, P.programmeDetailPage, ["id"]],
  [/^\/eligibility$/, P.eligibilityPage],
  [/^\/admissions$/, P.admissionsPage],
  [/^\/apply$/, P.applyPage],
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
  if (location.pathname + location.search === path) history.replaceState({}, "", path);  // same page: don't stack history entries
  else history.pushState({}, "", path);
  render();
}

document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-link], .navbar-nav a, .quick a, a.brand");
  if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || a.target === "_blank") return;
  const url = new URL(a.href, location.origin);
  if (url.origin !== location.origin) return;
  e.preventDefault();
  // the logo and the Home tab always land on the welcome screen (an open conversation is kept and can be resumed)
  if (a.matches("a.brand") || (a.closest(".navbar-nav") && url.pathname === "/")) { try { sessionStorage.setItem("silc_welcome", "1"); } catch { /* ignore */ } }
  const menu = $("navmenu");
  if (menu?.classList.contains("show")) window.bootstrap?.Collapse.getOrCreateInstance(menu).hide();  // close the mobile menu
  navigate(url.pathname + url.search);
});
window.addEventListener("popstate", render);

// ---------------------------------------------------------------- header / session
async function signIn(id) {
  await api("/session/demo-login", { method: "POST", body: { user_id: id } });
  sessionStorage.removeItem("silc_chat_v1");
  await refreshSession(); render();
}
async function signOut() {
  await api("/session/logout", { method: "POST" });
  sessionStorage.removeItem("silc_chat_v1");
  await refreshSession(); render();
}

function accountMenu() {
  const s = state.session;
  const label = s.authenticated ? s.user.name : "Sign in";
  const items = [];
  if (s.authenticated) {
    items.push(h("li", {}, h("h6", { class: "dropdown-header" }, "Signed in as a demo applicant")),
      h("li", {}, h("span", { class: "dropdown-item-text small text-secondary" }, "No live CHRIST applicant system is connected.")),
      h("li", {}, h("hr", { class: "dropdown-divider" })),
      h("li", {}, h("button", { class: "dropdown-item", type: "button", role: "menuitem", onclick: signOut }, icon("box-arrow-right", "me-2"), "Sign out")));
  } else if (s.demo_users.length) {
    items.push(h("li", {}, h("h6", { class: "dropdown-header" }, "Demo sign-in (mock applicant records)")),
      ...s.demo_users.map((u) => h("li", {}, h("button", { class: "dropdown-item", type: "button", role: "menuitem", onclick: () => signIn(u.id) }, icon("person", "me-2"), u.name))));
  } else {
    items.push(h("li", {}, h("span", { class: "dropdown-item-text small text-secondary" }, "Sign-in is provided by the university's authentication system.")));
  }
  return h("div", { class: "dropdown" },
    h("button", { class: "btn btn-outline-light btn-sm rounded-pill px-3 d-inline-flex align-items-center gap-2", type: "button", "data-bs-toggle": "dropdown", "aria-expanded": "false" },
      icon("person-circle", "fs-6"), label),
    h("ul", { class: "dropdown-menu dropdown-menu-end shadow-sm" }, items));
}

function renderHeader() {
  const path = location.pathname;
  const active = (href) => path === href || (href !== "/" && path.startsWith(href));
  $("topbar").replaceChildren(h("nav", { class: "navbar navbar-expand-lg navbar-dark navbar-cu", "aria-label": "Primary" },
    h("div", { class: "container-xl" },
      h("a", { class: "navbar-brand brand d-flex align-items-center gap-2 py-0 me-lg-4", href: "/" },
        h("span", { class: "brand-mark" }, icon("mortarboard-fill")),
        h("span", {}, h("span", { class: "brand-name" }, "CHRIST Admissions Copilot"),
          h("small", { class: "brand-sub" }, "Unofficial assistant · " + (state.meta?.university || "CHRIST (Deemed to be University)")))),
      h("button", { class: "navbar-toggler border-0", type: "button", "data-bs-toggle": "collapse", "data-bs-target": "#navmenu", "aria-controls": "navmenu", "aria-expanded": "false", "aria-label": "Toggle navigation" },
        h("span", { class: "navbar-toggler-icon" })),
      h("div", { class: "collapse navbar-collapse", id: "navmenu" },
        h("ul", { class: "navbar-nav me-auto gap-lg-1 mt-2 mt-lg-0" }, NAV.map(([href, label]) =>
          h("li", { class: "nav-item" }, h("a", { class: "nav-link" + (active(href) ? " active" : ""), href, "aria-current": active(href) ? "page" : null }, label)))),
        h("div", { class: "mt-2 mt-lg-0 pb-2 pb-lg-0" }, accountMenu())))));

  $("quick").replaceChildren(h("div", { class: "container-xl" }, h("div", { class: "scroller" }, QUICK.map(([href, label, ic]) =>
    h("a", { class: "btn btn-sm rounded-pill d-inline-flex align-items-center gap-1" + (path === href ? " active" : ""), href, "aria-current": path === href ? "page" : null },
      icon(ic), label)))));
}

function renderChrome() {
  const d = state.meta?.data, n = $("notice");
  const when = d?.scraped_at ? new Date(d.scraped_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
  if (d?.ready) {
    n.hidden = false;
    n.replaceChildren(icon("info-circle", "me-1"),
      h("span", { class: "d-none d-md-inline" }, `Unofficial assistant. Answers come from CHRIST's public website (christuniversity.in), collected ${when}. Please confirm important details with the Admissions Office.`,
        d.complete === false ? " Data collection is still in progress, so some information may be missing." : ""),
      h("span", { class: "d-md-none" }, `Unofficial · data from christuniversity.in (${when})${d.complete === false ? " · still being collected" : ""}`));
  } else if (d) {
    n.hidden = false;
    n.replaceChildren(icon("exclamation-triangle", "me-1"), "Website data has not been collected yet. Run: python scrape.py");
  } else n.hidden = true;
  $("foot").replaceChildren("Answers are based on information published on christuniversity.in · ",
    h("button", { class: "linkbtn", type: "button", onclick: () => window.bootstrap?.Modal.getOrCreateInstance($("privacy")).show() }, "Privacy & data use"));
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
    main.replaceChildren(h("div", { class: "container-xl py-5" }, h("div", { class: "state" }, icon("compass", "fs-2 d-block mb-2"), h("h1", { class: "h4" }, "Page not found"),
      h("a", { class: "btn btn-primary mt-2", href: "/", "data-link": "" }, "Back to home"))));
    document.title = "Not found · CHRIST Admissions Copilot"; return;
  }
  const [m, view, names] = hit;
  document.title = (path === "/" ? "Admissions Assistant" : NAV.concat(QUICK).find(([p]) => p === path)?.[1] || "Programme") + " · CHRIST Admissions Copilot";
  if (view === "home") { main.scrollTop = 0; mountChat(main, { session: () => state.session, navigate }); return; }
  main.replaceChildren(P.loading());
  const params = {}; (names || []).forEach((n, i) => { params[n] = decodeURIComponent(m[i + 1]); });
  try {
    const node = await view({ navigate, params });
    if (my !== token) return; // a newer navigation won
    main.replaceChildren(node);
    main.focus({ preventScroll: true }); main.scrollTop = 0;
  } catch (e) {
    if (my !== token) return;
    main.replaceChildren(e.status === 404
      ? h("div", { class: "container-xl py-5" }, h("div", { class: "state" }, icon("search", "fs-2 d-block mb-2"), h("p", {}, e.message), h("a", { class: "btn btn-primary", href: "/programmes", "data-link": "" }, "Browse programmes")))
      : P.errorState(e, () => render()));
  }
}

(async function boot() {
  try { state.meta = await api("/meta"); } catch { state.meta = null; }
  renderChrome();
  await refreshSession();
  render();
})();
