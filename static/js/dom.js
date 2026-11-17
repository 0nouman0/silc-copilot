// Tiny DOM helpers. Text is always set via textContent/createTextNode (never innerHTML),
// so server/LLM-supplied strings cannot inject markup. Markdown goes through md.js only.
export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v; // only ever called with md.js output or static icons
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

export const na = (v, fallback = "Not available") =>
  v == null || v === "" ? h("span", { class: "na" }, fallback) : String(v);

export const icon = (name) => {
  const p = {
    send: '<path d="M12 19V5M5 12l7-7 7 7"/>',
    compass: '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/>',
    check: '<path d="M20 6L9 17l-5-5"/>',
    wallet: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M16 12h5M3 10h18"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 015 .5c0 1.5-2.5 2-2.5 3.5M12 17h.01"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    logo: '<path d="M9 21l7-11 7 11" transform="translate(-4 -3) scale(.9)"/>',
  }[name];
  return h("span", { html: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>` });
};
