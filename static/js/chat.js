import { api } from "./api.js";
import { h, icon } from "./dom.js";
import { renderMarkdown } from "./md.js";

const KEY = "silc_chat_v1";
const ACTIONS = [
  { href: "/programmes", t: "Explore programmes", d: "Browse CHRIST's undergraduate, postgraduate and doctoral programmes.", i: "compass", c: "#4f46e5",
    p: "Show me the programmes available." },
  { href: "/eligibility", t: "Check my eligibility", d: "See how your qualifications compare with published criteria.", i: "check", c: "#0d9488",
    p: "Help me check my eligibility for a programme." },
  { href: "/fees", t: "Understand fees", d: "Explore tuition fees, other charges and payment information.", i: "wallet", c: "#d97706",
    p: "Show me the programme fee structure and other charges." },
  { href: "/apply", t: "Admission help", d: "Get guidance on applications, documents and deadlines.", i: "help", c: "#7c3aed",
    p: "Guide me through the application process." },
];
const PROMPTS = ["What are the application dates?", "What are the fees for the MBA?", "What is the eligibility for BBA?",
  "Which documents do I need?", "Are scholarships available?", "What are the hostel facilities?",
  "How can international students apply?", "How do I contact the Admissions Office?"];
const APP_PROMPTS = ["What is the status of this application?", "What are the fees for this programme?",
  "What documents are required?", "What should I do next?"];

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
  const sendBtn = h("button", { class: "send", type: "button", "aria-label": "Send message", onclick: submit }, icon("send"));
  const clearBtn = h("button", { class: "linkbtn", type: "button", onclick: newChat }, "New chat");
  const dock = h("div", { class: "dock" }, h("div", { class: "dockin" }, ctxbar,
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
    .slice(-24).map((m) => ({ role: m.role, content: m.content }));

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
  const chip = (text) => h("button", { class: "chip", type: "button", onclick: () => ask(text) }, text);

  function appsPanel() {
    if (s.appsError) return h("div", { class: "apps" }, h("div", { class: "errcard", role: "alert" }, s.appsError));
    if (!s.apps) return null;
    const n = s.apps.length;
    return h("div", { class: "apps" },
      h("h2", {}, n === 1 ? "I found 1 application associated with your account." : `I found ${n} applications associated with your account.`),
      h("p", { class: "lede" }, "Which application would you like to discuss? ",
        s.appsDemo ? h("span", { class: "tag demo" }, "Demo applicant records — no live CHRIST applicant system") : null),
      h("div", { class: "grid" }, s.apps.map((a) => h("button", {
        class: "appcard", type: "button", "aria-pressed": String(s.activeApp?.application_id === a.application_id), onclick: () => pickApp(a),
      }, h("b", {}, a.programme_name), h("small", {}, `${a.application_id}${a.campus ? " · " + a.campus : ""}`),
      h("div", {}, h("span", { class: "tag" + (/reject/i.test(a.status) ? " bad" : "") }, a.status),
        a.offer_status ? h("span", { class: "tag ok" }, a.offer_status) : null),
      a.last_updated ? h("small", {}, `Last updated ${a.last_updated}`) : null))),
      h("button", { class: "btn ghost", type: "button", onclick: () => { s.showApps = false; renderAll(); ta.focus(); } }, "Ask something else"));
  }

  function hero() {
    return h("div", { class: "hero" },
      h("h1", {}, "What would you like to know about ", h("em", {}, "admissions?")),
      h("p", {}, "Ask a question in your own words. I can help you explore programmes, understand eligibility, and navigate the admission process."),
      s.showApps ? appsPanel() : null,
      s.messages.length ? h("p", {}, h("button", { class: "btn", type: "button", onclick: () => { s.welcome = false; renderAll(); ta.focus(); } },
        `Continue your conversation (${s.messages.filter((m) => m.role === "user").length} question${s.messages.filter((m) => m.role === "user").length === 1 ? "" : "s"})`)) : null,
      h("div", { class: "actions" }, ACTIONS.map((a) => h("a", { class: "action", href: a.href, "data-link": "" },
        h("span", { class: "ic", style: `background:${a.c}` }, icon(a.i)), h("div", {}, h("b", {}, a.t), h("span", {}, a.d))))),
      h("div", { class: "chips" }, PROMPTS.map(chip)),
      signedIn() && s.apps && !s.showApps ? h("button", { class: "linkbtn", type: "button", onclick: () => { s.showApps = true; renderAll(); } }, "Show my applications") : null,
      !signedIn() ? h("p", { class: "lede" }, "Sign in (top right) to discuss your own applications.") : null);
  }

  function message(m, isLast) {
    if (m.role === "note") {
      return h("div", {}, h("div", { class: "note", role: "status" }, m.content),
        m.followUps ? h("div", { class: "followups", style: "justify-content:center" }, m.followUps.map(chip)) : null);
    }
    if (m.role === "error") {
      return h("div", { class: "errcard", role: "alert" }, m.content,
        RETRYABLE.has(m.code) ? h("div", {}, h("button", { class: "btn", type: "button", onclick: () => ask(null) }, "Try again")) : null);
    }
    if (m.role === "user") {
      return h("div", { class: "msg user" }, h("div", { class: "who", "aria-hidden": "true" }, "You"),
        h("div", { class: "body" }, h("div", { class: "bubble" }, m.content)));
    }
    return h("div", { class: "msg bot" }, h("div", { class: "who", "aria-hidden": "true" }, "AI"),
      h("div", { class: "body" }, h("div", { class: "bubble", html: renderMarkdown(m.content) }),
        m.sources?.length || m.demo ? h("div", { class: "sources" }, m.sources?.length ? "Sources:" : null,
          (m.sources || []).map((x) => (x.url ? h("a", { class: "src", href: x.url, target: "_blank", rel: "noopener noreferrer" }, x.title) : h("span", { class: "src" }, x.title))),
          m.demo ? h("span", { class: "tag demo" }, "Demo applicant data") : null) : null,
        isLast && m.followUps?.length && !s.busy ? h("div", { class: "followups" }, m.followUps.map(chip)) : null));
  }

  function renderStage() {
    if (!s.messages.length || s.welcome) { stage.replaceChildren(hero()); return; }
    const last = s.messages.map((m) => m.role).lastIndexOf("assistant");
    const kids = s.messages.map((m, i) => message(m, i === last));
    if (s.showApps) kids.push(appsPanel());
    if (s.busy) kids.push(h("div", { class: "msg bot", role: "status", "aria-label": "Assistant is typing" }, h("div", { class: "who" }, "AI"),
      h("div", { class: "body" }, h("div", { class: "bubble" }, h("span", { class: "typing" }, h("i"), h("i"), h("i"))))));
    stage.replaceChildren(h("div", { class: "thread", "aria-live": "polite" }, kids));
    stage.scrollTop = stage.scrollHeight;
  }

  function renderCtx() {
    ctxbar.replaceChildren();
    if (s.activeApp) {
      ctxbar.append(h("span", { class: "tag" }, `Discussing: ${s.activeApp.programme_name} · ${s.activeApp.application_id}`),
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
