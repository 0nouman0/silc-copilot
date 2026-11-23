import { api } from "./api.js";
import { h, icon } from "./dom.js";
import { renderMarkdown } from "./md.js";

const KEY = "silc_chat_v3";

const ERR_COPY = {
  not_configured: "The AI assistant is not configured yet. An administrator needs to set LLM_API_KEY on the server.",
  rate_limited: "The assistant is receiving too many requests right now. Please wait a moment and try again.",
  timeout: "The assistant took too long to respond. Please try again.",
  network: "Could not reach the server. Check your connection and try again.",
  data_unavailable: "The CHRIST programme database cannot be reached right now. Please try again shortly.",
};
const RETRYABLE = new Set(["rate_limited", "timeout", "network", "unavailable", "bad_response", "error", "data_unavailable"]);
const LEVEL_TONE = { undergraduate: "primary", postgraduate: "success", doctoral: "warning" };

function load() {
  try { return JSON.parse(sessionStorage.getItem(KEY)) || {}; } catch { return {}; }
}
function save(s) {
  try { sessionStorage.setItem(KEY, JSON.stringify({ messages: s.messages.filter((m) => m.role !== "error"), lastAgent: s.lastAgent, state: s.state, conversationId: s.conversationId })); } catch { /* storage unavailable */ }
}
// one id per submitted message: the server answers a repeated submission (double click, retry) once
const newId = () => (globalThis.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
const cap = (x) => (x ? x.charAt(0).toUpperCase() + x.slice(1) : "");
const years = (n) => { const v = Number(n); return v ? `${v} year${v === 1 ? "" : "s"}` : null; };

export function mountChat(root) {
  const saved = load();
  // `state` is this chat's conversation state (degree, topic, campus, programme under discussion). The server keeps nothing: it comes back with every answer.
  const s = { messages: Array.isArray(saved.messages) ? saved.messages : [], lastAgent: saved.lastAgent || null, state: saved.state || null, conversationId: saved.conversationId || newId(), busy: false, pendingId: null, seq: 0 };

  const stage = h("div", { class: "stage", id: "stage" });
  const ta = h("textarea", { rows: "1", "aria-label": "Message", placeholder: "Tell me what you'd like to study, or ask about a programme, fees, eligibility, hostel...",
    maxlength: "4000", onkeydown: (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } },
    oninput: () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 160) + "px"; } });
  const sendBtn = h("button", { class: "send btn btn-primary rounded-circle", type: "button", "aria-label": "Send message", onclick: submit }, icon("arrow-up", "fs-5"));
  const clearBtn = h("button", { class: "linkbtn", type: "button", onclick: newChat }, icon("plus-lg", "me-1"), "New chat");
  const dock = h("div", { class: "dock" }, h("div", { class: "container-chat" },
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
    s.seq++;                                  // an answer still on its way belongs to the OLD chat and is dropped when it arrives
    s.busy = false; s.messages = []; s.lastAgent = null; s.state = null; s.pendingId = null; s.conversationId = newId();
    save(s); renderAll(); begin(); ta.focus();
  }

  // earlier answers only need their gist; the opening welcome is part of the context the agents see
  const history = () => s.messages.filter((m) => m.role === "user" || m.role === "assistant")
    .slice(-24).map((m) => ({ role: m.role, content: m.role === "assistant" ? m.content.slice(0, 2000) : m.content }));

  async function ask(text) {
    if (s.busy) return;                       // one request at a time: chips, cards, retry and Enter all go through here
    if (text != null) { s.pendingId = newId(); s.messages.push({ role: "user", content: text, id: s.pendingId }); }
    else if (!s.pendingId) s.pendingId = newId();
    s.messages = s.messages.filter((m) => m.role !== "error");
    s.busy = true; renderAll();
    const mine = ++s.seq;
    try {
      const r = await api("/chat", { method: "POST", body: { messages: history(), last_agent: s.lastAgent, state: s.state, client_msg_id: s.pendingId, conversation_id: s.conversationId } });
      if (mine !== s.seq) return;             // stale: the chat was reset while this request was in flight
      s.lastAgent = r.agent || s.lastAgent;
      s.state = r.state || s.state;
      s.pendingId = null;
      const lastUser = [...s.messages].reverse().find((m) => m.role === "user");
      if (lastUser && r.understanding) lastUser.normalized = r.understanding.corrected_message;   // the original text stays; this is how it was understood
      // the cards belong to THIS message only, and only cards the backend validated (they carry a programme id)
      s.messages.push({ role: "assistant", id: r.response_id, content: r.reply, sources: r.sources, followUps: r.follow_ups, cards: (r.cards || []).filter((c) => c && c.programme_id) });
    } catch (e) {
      if (mine !== s.seq) return;
      s.messages.push({ role: "error", code: e.code, content: ERR_COPY[e.code] || e.message });
    }
    s.busy = false; save(s); renderAll(); ta.focus();
  }

  // The conversation starts with the assistant welcoming the applicant and asking which course they want to enrol in.
  async function begin() {
    if (s.busy) return;
    s.busy = true; renderAll();
    try {
      const w = await api("/welcome");
      s.lastAgent = w.agent || "recommend";
      s.messages.push({ role: "assistant", content: w.reply, followUps: w.follow_ups, welcome: true });
    } catch (e) {
      s.messages.push({ role: "error", code: e.code, content: ERR_COPY[e.code] || e.message });
    }
    s.busy = false; save(s); renderAll();
  }

  // ------------------------------------------------------------ rendering
  const chip = (text) => h("button", { class: "chip btn btn-sm rounded-pill", type: "button", onclick: () => ask(text) }, text);

  function programmeCard(c) {
    const act = (label, ic, msg) => h("button", { class: "btn btn-sm btn-outline-primary d-inline-flex align-items-center gap-1", type: "button", onclick: () => ask(msg) }, icon(ic), label);
    const tone = LEVEL_TONE[c.level] || "secondary";
    return h("div", { class: "pcard card h-100" }, h("div", { class: "card-body d-flex flex-column gap-2" },
      h("div", { class: "d-flex flex-wrap gap-1" },
        c.number ? h("span", { class: "badge bg-primary fw-semibold", title: "Reply with this number" }, `#${c.number}`) : null,
        h("span", { class: `badge bg-${tone}-subtle text-${tone}-emphasis fw-medium` }, cap(c.level)),
        years(c.duration_years) ? h("span", { class: "badge bg-light text-secondary border fw-medium" }, icon("clock", "me-1"), years(c.duration_years)) : null),
      h("h3", { class: "pcard-title" }, c.name),
      h("div", { class: "small text-secondary" }, icon("geo-alt", "me-1"), c.campuses?.length ? c.campuses.join(" · ") : "Campus not listed"),
      c.department ? h("div", { class: "small text-secondary" }, icon("building", "me-1"), c.department) : null,
      c.matched_interests?.length ? h("div", { class: "small" }, h("span", { class: "text-secondary" }, "Matches your interest in "), h("strong", {}, c.matched_interests.slice(0, 4).join(", "))) : null,
      c.career_paths?.length ? h("ul", { class: "pcard-careers small text-secondary mb-0" }, c.career_paths.map((p) => h("li", {}, p))) : null,
      h("div", { class: "pcard-actions mt-auto pt-2 d-flex flex-wrap gap-2" },
        act("Eligibility", "patch-check", `What is the eligibility for ${c.name}?`),
        act("Fees", "cash-stack", `What are the fees for ${c.name}?`),
        act("Curriculum", "journal-text", `Show the curriculum of ${c.name}`),
        act("Details", "info-circle", `Tell me about ${c.name}`))));
  }

  function message(m, isLast) {
    if (m.role === "error") {
      return h("div", { class: "errcard alert alert-danger d-flex gap-3 align-items-start mb-0", role: "alert" }, icon("exclamation-triangle", "fs-5 mt-1"),
        h("div", {}, h("div", {}, m.content),
          RETRYABLE.has(m.code) ? h("button", { class: "btn btn-sm btn-danger mt-2", type: "button", onclick: () => (s.messages.some((x) => x.role === "user") ? ask(null) : (s.messages = [], begin())) }, icon("arrow-clockwise", "me-1"), "Try again") : null));
    }
    if (m.role === "user") {
      const fixed = m.normalized && m.normalized.trim().toLowerCase() !== m.content.trim().toLowerCase();
      return h("div", { class: "msg user" }, h("div", { class: "bubble" }, m.content), fixed ? h("div", { class: "small text-secondary mt-1", title: "How your message was understood" }, `Understood as: ${m.normalized}`) : null);
    }
    return h("div", { class: "msg bot" }, h("span", { class: "avatar", "aria-hidden": "true" }, icon("mortarboard-fill")),
      h("div", { class: "body" }, h("div", { class: "bubble prose" + (m.welcome ? " welcome" : ""), html: renderMarkdown(m.content) }),
        m.cards?.length ? h("div", { class: "pcards" }, m.cards.map(programmeCard)) : null,
        m.sources?.length ? h("div", { class: "sources" }, icon("database-check", "me-1"), "Source: CHRIST programme database") : null,
        isLast && m.followUps?.length && !s.busy ? h("div", { class: "followups" }, m.followUps.map(chip)) : null));
  }

  function renderStage() {
    const last = s.messages.map((m) => m.role).lastIndexOf("assistant");
    const kids = s.messages.map((m, i) => message(m, i === last));
    if (s.busy) kids.push(h("div", { class: "msg typing", role: "status", "aria-label": "Assistant is typing" }, h("span", { class: "avatar bg-secondary-subtle text-secondary", "aria-hidden": "true" }, icon("mortarboard-fill")),
      h("div", { class: "bubble bg-white border rounded-4 px-3 py-2" }, h("span", { class: "typing" }, h("i"), h("i"), h("i")))));
    stage.replaceChildren(h("div", { class: "container-chat" }, h("div", { class: "thread" + (s.messages.length <= 1 ? " intro" : ""), "aria-live": "polite" }, kids)));
    stage.scrollTop = stage.scrollHeight;
  }

  function renderAll() {
    renderStage();
    sendBtn.disabled = s.busy; ta.disabled = false;
  }

  // ------------------------------------------------------------ boot
  renderAll();
  if (!s.messages.length) begin();
  ta.focus();
}
