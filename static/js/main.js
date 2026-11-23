import { api } from "./api.js";
import { mountChat } from "./chat.js";
import { h, icon } from "./dom.js";

// The whole product is one conversation. There are no accounts and no pages to pick a programme, fees or dates from:
// the assistant asks what the applicant needs, in natural language, and shows the options itself.
const state = { meta: null };
const $ = (id) => document.getElementById(id);

function renderHeader() {
  $("topbar").replaceChildren(h("nav", { class: "navbar navbar-dark navbar-cu", "aria-label": "Primary" },
    h("div", { class: "container-xl" },
      h("a", { class: "navbar-brand brand d-flex align-items-center gap-2 py-0", href: "/" },
        h("span", { class: "brand-mark" }, icon("mortarboard-fill")),
        h("span", {}, h("span", { class: "brand-name" }, "CHRIST University"),
          h("small", { class: "brand-sub" }, "Admissions assistant · unofficial"))),
      h("span", { class: "badge rounded-pill text-bg-light bg-opacity-25 text-white fw-medium d-none d-sm-inline-flex align-items-center gap-1" },
        icon("shield-check"), "Answers from the CHRIST programme database"))));
}

function renderChrome() {
  const d = state.meta?.data, n = $("notice");
  if (d?.ready) {
    n.hidden = false;
    n.replaceChildren(icon("info-circle", "me-1"),
      h("span", { class: "d-none d-md-inline" }, `Unofficial assistant. Answers come from the CHRIST programme database (${d.programmes} programme listings). Application dates stored there can be out of date: please confirm important details with the Admissions Office.`),
      h("span", { class: "d-md-none" }, "Unofficial · data from the CHRIST programme database"));
  } else if (d) {
    n.hidden = false;
    n.replaceChildren(icon("exclamation-triangle", "me-1"), "The CHRIST programme database cannot be reached right now, so programme information is unavailable.");
  } else n.hidden = true;
  $("foot").replaceChildren("Answers are based on the CHRIST programme database · ",
    h("button", { class: "linkbtn", type: "button", onclick: () => window.bootstrap?.Modal.getOrCreateInstance($("privacy")).show() }, "Privacy & data use"));
}

function render() {
  renderHeader();
  if (location.pathname !== "/") history.replaceState({}, "", "/");   // any old page link lands in the chat
  document.title = "CHRIST University · Admissions Assistant";
  mountChat($("main"));
}

document.addEventListener("click", (e) => {
  const a = e.target.closest("a.brand");
  if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey) return;
  e.preventDefault();
});

(async function boot() {
  try { state.meta = await api("/meta"); } catch { state.meta = null; }
  renderChrome();
  render();
})();
