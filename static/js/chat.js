import { api } from "./api.js";
import { h, icon } from "./dom.js";
import { renderMarkdown } from "./md.js";

const KEY = "silc_chat_v1";
const ACTIONS = [
  { href: "/programmes", t: "Explore programmes", d: "Browse CHRIST's undergraduate, postgraduate and doctoral programmes.", i: "compass", tone: "primary" },
  { href: "/eligibility", t: "Check my eligibility", d: "Read the published criteria and compare your marks.", i: "patch-check", tone: "success" },
  { href: "/fees", t: "Understand fees", d: "Tuition by category, registration fee and payment timing.", i: "cash-stack", tone: "warning" },
  { href: "/apply", t: "Admission help", d: "How to apply, important dates and where to start.", i: "pencil-square", tone: "info" },
];
const PROMPTS = ["What are the application dates?", "What are the fees for the MBA?", "What is the eligibility for BBA?",
  "Which documents do I need?", "Are scholarships available?", "What are the hostel facilities?",
  "How can international students apply?", "How do I contact the Admissions Office?"];
const APP_PROMPTS = ["What is the status of this application?", "What are the fees for this programme?",
  "What documents are required?", "What should I do next?"];
const STATUS_TONE = { "application incomplete": "warning", "application submitted": "primary", "under review": "info", shortlisted: "success", "application rejected": "danger" };

const ERR_COPY = {
  not_configured: "The AI assistant is not configured yet. An administrator needs to set LLM_API_KEY on the server. You can still browse programmes, fees and dates from the menu above.",
  rate_limited: "The assistant is receiving too many requests right now. Please wait a moment and try again.",
  timeout: "The assistant took too long to respond. Please try again.",
  network: "Could not reach the server. Check your connection and try again.",
};
const RETRYABLE = new Set(["rate_limited", "timeout", "network", "unavailable", "bad_response", "error"]);

function load() {
  try { return JSON.parse(sessionStorage.getItem(KEY)) || {}; } catch { return {}; }
}
function save(s) {
  try { sessionStorage.setItem(KEY, JSON.stringify({ messages: s.messages.filter((m) => m.role !== "error"), activeApp: s.activeApp })); } catch { /* storage unavailable */ }
}

export function mountChat(root, ctx) {
  const saved = load();
  const s = {
    messages: Array.isArray(saved.messages) ? saved.messages : [],
    activeApp: saved.activeApp || null, // {application_id, programme_name}
    apps: null, appsError: null, showApps: false, busy: false,
  };
  const signedIn = () => ctx.session().authenticated;
  if (!signedIn()) s.activeApp = null;
  // the logo / Home tab set this flag: show the welcome screen, but keep the conversation so it can be resumed
  s.welcome = false;
  try { if (sessionStorage.getItem("silc_welcome")) { s.welcome = true; sessionStorage.removeItem("silc_welcome"); } } catch { /* storage unavailable */ }

  const stage = h("div", { class: "stage", id: "stage" });
  const ctxbar = h("div", { class: "ctxbar" });
  const ta = h("textarea", { rows: "1", "aria-label": "Message", placeholder: "Ask about programmes, eligibility, fees, applications, scholarships...",
    maxlength: "4000", onkeydown: (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } },
    oninput: () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 160) + "px"; } });
  const sendBtn = h("button", { class: "send btn btn-primary rounded-circle", type: "button", "aria-label": "Send message", onclick: submit }, icon("arrow-up", "fs-5"));
  const clearBtn = h("button", { class: "linkbtn", type: "button", onclick: newChat }, icon("plus-lg", "me-1"), "New chat");
  const dock = h("div", { class: "dock" }, h("div", { class: "container-chat" }, ctxbar,
    h("div", { class: "composer" }, ta, sendBtn),
    h("div", { class: "dockmeta" }, h("span", {}, "Enter to send · Shift+Enter for a new line"), clearBtn)));
  root.replaceChildren(h("section", { class: "assistant", "aria-label": "Admissions assistant" }, stage, dock));

  function submit() {
    const text = ta.value.trim();
    if (!text || s.busy) return;
    ta.value = ""; ta.style.height = "auto";
    ask(text);
  }

  function newChat() {
    s.messages = []; s.activeApp = null; s.welcome = false; s.showApps = signedIn() && !!s.apps?.length; save(s); renderAll(); ta.focus();
  }

  const history = () => s.messages.filter((m) => m.role === "user" || m.role === "assistant")
    .slice(-24).map((m) => ({ role: m.role, content: m.role === "assistant" ? m.content.slice(0, 2000) : m.content }));  // earlier answers only need their gist

  async function ask(text) {
    if (text != null) s.messages.push({ role: "user", content: text });
    s.messages = s.messages.filter((m) => m.role !== "error");
    s.busy = true; s.showApps = false; s.welcome = false; renderAll();
    try {
      const r = await api("/chat", { method: "POST", body: { messages: history(), active_application_id: s.activeApp?.application_id || null } });
      s.messages.push({ role: "assistant", content: r.reply, sources: r.sources, followUps: r.follow_ups, demo: r.demo });
      if (s.activeApp && !r.active_application_id) {
        s.activeApp = null;
        s.messages.push({ role: "note", content: "That application could not be verified for your account, so the selection was cleared." });
      }
    } catch (e) {
      s.messages.push({ role: "error", code: e.code, content: ERR_COPY[e.code] || e.message });
    }
    s.busy = false; save(s); renderAll(); ta.focus();
  }

  function pickApp(a) {
    s.activeApp = { application_id: a.application_id, programme_name: a.programme_name };
    s.showApps = false; s.welcome = false;
    s.messages.push({ role: "note", content: `Now discussing ${a.programme_name} (${a.application_id}).`, followUps: APP_PROMPTS });
    save(s); renderAll(); ta.focus();
  }

  async function loadApps() {
    if (!signedIn()) { s.apps = null; return; }
    try {
      const r = await api("/applications");
      s.apps = r.applications; s.appsDemo = r.demo; s.appsError = null;
      s.showApps = (s.messages.length === 0 || s.welcome) && !s.activeApp && s.apps.length > 0;
    } catch (e) { s.apps = null; s.appsError = e.message; }
  }

  // ------------------------------------------------------------ rendering
  const chip = (text) => h("button", { class: "chip btn btn-sm rounded-pill", type: "button", onclick: () => ask(text) }, text);

  function appsPanel() {
    if (s.appsError) return h("div", { class: "apps" }, h("div", { class: "errcard alert alert-danger", role: "alert" }, s.appsError));
    if (!s.apps) return null;
    const n = s.apps.length;
    return h("div", { class: "apps text-start my-4" },
      h("h2", { class: "mb-1" }, n === 1 ? "I found 1 application associated with your account." : `I found ${n} applications associated with your account.`),
      h("p", { class: "text-secondary small mb-3" }, "Which application would you like to discuss? ",
        s.appsDemo ? h("span", { class: "badge text-bg-warning fw-medium" }, "Demo records, no live CHRIST applicant system") : null),
      h("div", { class: "row g-3 mb-3" }, s.apps.map((a) => h("div", { class: "col-12 col-md-4" }, h("button", {
        class: "appcard card card-link", type: "button", "aria-pressed": String(s.activeApp?.application_id === a.application_id), onclick: () => pickApp(a),
      }, h("div", { class: "card-body" },
        h("div", { class: "fw-semibold lh-sm mb-1" }, a.programme_name),
        h("div", { class: "small text-secondary mb-2" }, `${a.application_id}${a.campus ? " · " + a.campus : ""}`),
        h("div", { class: "d-flex flex-wrap gap-1 mb-1" },
          h("span", { class: `badge text-bg-${STATUS_TONE[a.status.toLowerCase()] || "secondary"} fw-medium` }, a.status),
          a.offer_status ? h("span", { class: "badge text-bg-success fw-medium" }, a.offer_status) : null),
        a.last_updated ? h("div", { class: "small text-secondary" }, `Last updated ${a.last_updated}`) : null))))),
      h("button", { class: "btn btn-outline-secondary btn-sm", type: "button", onclick: () => { s.showApps = false; renderAll(); ta.focus(); } }, "Ask something else"));
  }

  function hero() {
    const nUser = s.messages.filter((m) => m.role === "user").length;
    return h("div", { class: "hero container-narrow text-center" },
      h("span", { class: "badge rounded-pill text-bg-light border text-primary-emphasis fw-medium px-3 py-2 mb-3" }, icon("stars", "me-1"), "Admissions assistant for CHRIST University"),
      h("h1", { class: "mb-3" }, "What would you like to know about ", h("span", { class: "text-primary" }, "admissions?")),
      h("p", { class: "lead text-secondary mb-4" }, "Ask a question in your own words. I can help you explore programmes, understand eligibility, and navigate the admission process."),
      s.messages.length ? h("p", {}, h("button", { class: "btn btn-primary", type: "button", onclick: () => { s.welcome = false; renderAll(); ta.focus(); } },
        icon("chat-dots", "me-2"), `Continue your conversation (${nUser} question${nUser === 1 ? "" : "s"})`)) : null,
      s.showApps ? appsPanel() : null,
      h("div", { class: "actions row g-3 mb-4 text-start" }, ACTIONS.map((a) => h("div", { class: "col-12 col-md-6" },
        h("a", { class: "action card card-link h-100 text-decoration-none text-body", href: a.href, "data-link": "" },
          h("div", { class: "card-body d-flex align-items-start gap-3" },
            h("span", { class: `icon-tile bg-${a.tone}-subtle text-${a.tone}-emphasis` }, icon(a.i)),
            h("div", { class: "flex-grow-1" }, h("div", { class: "fw-semibold" }, a.t), h("div", { class: "small text-secondary" }, a.d)),
            icon("arrow-right", "text-secondary mt-1")))))),
      h("div", { class: "chips d-flex flex-wrap justify-content-center gap-2 mb-3" }, PROMPTS.map(chip)),
      signedIn() && s.apps && !s.showApps ? h("button", { class: "linkbtn", type: "button", onclick: () => { s.showApps = true; renderAll(); } }, "Show my applications") : null,
      !signedIn() ? h("p", { class: "small text-secondary mb-0" }, icon("person-circle", "me-1"), "Sign in (top right) to discuss your own applications.") : null);
  }

  function message(m, isLast) {
    if (m.role === "note") {
      return h("div", {}, h("div", { class: "note", role: "status" }, h("span", { class: "badge rounded-pill text-bg-primary-subtle text-primary-emphasis fw-medium px-3 py-2" }, m.content)),
        m.followUps ? h("div", { class: "followups justify-content-center" }, m.followUps.map(chip)) : null);
    }
    if (m.role === "error") {
      return h("div", { class: "errcard alert alert-danger d-flex gap-3 align-items-start mb-0", role: "alert" }, icon("exclamation-triangle", "fs-5 mt-1"),
        h("div", {}, h("div", {}, m.content),
          RETRYABLE.has(m.code) ? h("button", { class: "btn btn-sm btn-danger mt-2", type: "button", onclick: () => ask(null) }, icon("arrow-clockwise", "me-1"), "Try again") : null));
    }
    if (m.role === "user") return h("div", { class: "msg user" }, h("div", { class: "bubble" }, m.content));
    return h("div", { class: "msg bot" }, h("span", { class: "avatar", "aria-hidden": "true" }, icon("mortarboard-fill")),
      h("div", { class: "body" }, h("div", { class: "bubble prose", html: renderMarkdown(m.content) }),
        m.sources?.length || m.demo ? h("div", { class: "sources" }, m.sources?.length ? h("span", {}, "Sources") : null,
          (m.sources || []).map((x) => (x.url
            ? h("a", { class: "src", href: x.url, target: "_blank", rel: "noopener noreferrer", title: x.title }, icon("box-arrow-up-right"), h("span", {}, x.title))
            : h("span", { class: "src", title: x.title }, h("span", {}, x.title)))),
          m.demo ? h("span", { class: "badge text-bg-warning fw-medium" }, "Demo applicant data") : null) : null,
        isLast && m.followUps?.length && !s.busy ? h("div", { class: "followups" }, m.followUps.map(chip)) : null));
  }

  function renderStage() {
    if (!s.messages.length || s.welcome) { stage.replaceChildren(hero()); return; }
    const last = s.messages.map((m) => m.role).lastIndexOf("assistant");
    const kids = s.messages.map((m, i) => message(m, i === last));
    if (s.showApps) kids.push(appsPanel());
    if (s.busy) kids.push(h("div", { class: "msg typing", role: "status", "aria-label": "Assistant is typing" }, h("span", { class: "avatar bg-secondary-subtle text-secondary", "aria-hidden": "true" }, icon("mortarboard-fill")),
      h("div", { class: "bubble bg-white border rounded-4 px-3 py-2" }, h("span", { class: "typing" }, h("i"), h("i"), h("i")))));
    stage.replaceChildren(h("div", { class: "container-chat" }, h("div", { class: "thread", "aria-live": "polite" }, kids)));
    stage.scrollTop = stage.scrollHeight;
  }

  function renderCtx() {
    ctxbar.replaceChildren();
    if (s.activeApp) {
      ctxbar.append(h("span", { class: "badge text-bg-primary-subtle text-primary-emphasis fw-medium d-inline-flex align-items-center gap-1" }, icon("link-45deg"), `Discussing: ${s.activeApp.programme_name} · ${s.activeApp.application_id}`),
        h("button", { class: "linkbtn", type: "button", onclick: () => { s.showApps = true; renderAll(); } }, "Switch application"),
        h("button", { class: "linkbtn", type: "button", onclick: () => { s.activeApp = null; s.messages.push({ role: "note", content: "Back to general admission questions." }); save(s); renderAll(); } }, "General questions"));
    }
  }

  function renderAll() {
    renderStage(); renderCtx();
    sendBtn.disabled = s.busy; ta.disabled = false;
  }

  // ------------------------------------------------------------ boot
  renderAll();
  loadApps().then(() => { if (!s.busy) renderAll(); });
  const pre = sessionStorage.getItem("silc_prefill");
  if (pre) { try { sessionStorage.removeItem("silc_prefill"); } catch { /* ignore */ } ask(pre); }
}
