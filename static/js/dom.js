// Tiny DOM helpers. Text is always set via textContent/createTextNode (never innerHTML),
// so server/LLM-supplied strings cannot inject markup. Markdown goes through md.js only.
export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v; // only ever called with md.js output
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

/** Bootstrap Icons glyph, e.g. icon("compass") or icon("check-circle", "text-success me-1"). */
export const icon = (name, cls = "") => h("i", { class: `bi bi-${name}${cls ? " " + cls : ""}`, "aria-hidden": "true" });

export const na = (v, fallback = "Not available") =>
  v == null || v === "" ? h("span", { class: "na" }, fallback) : String(v);
