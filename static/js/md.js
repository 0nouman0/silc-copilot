// Small, SAFE markdown renderer for assistant replies and database text.
// Order matters: escape ALL HTML first, then add our own tags. Links are limited to http(s).
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const NEW_TAB = 'target="_blank" rel="noopener noreferrer"';

function inline(raw) {
  const stash = []; // protect code and links from the emphasis rules below
  const keep = (html) => `\u0000${stash.push(html) - 1}\u0000`;
  let s = esc(raw);
  s = s.replace(/`([^`]+)`/g, (_, c) => keep(`<code>${c}</code>`));
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) => keep(`<a href="${u}" ${NEW_TAB}>${t}</a>`));
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]*[^\s<).,;:!?])/g, (_, pre, u) => pre + keep(`<a href="${u}" ${NEW_TAB}>${u}</a>`));
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[\s(>])\*([^*\n]+)\*(?=[\s).,;:!?<]|$)/g, "$1<em>$2</em>");
  s = s.replace(/(^|[\s(>])_([^_\n]+)_(?=[\s).,;:!?<]|$)/g, "$1<em>$2</em>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => stash[+i]);
}

const isRow = (l) => /^\s*\|.*\|\s*$/.test(l);
const isSep = (l) => /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(l);
const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
const BULLET = /^\s*[-*•]\s+/;
const ORDERED = /^\s*\d+[.)]\s+/;
const startsBlock = (l) => /^(#{1,4}\s|>\s?)/.test(l) || BULLET.test(l) || ORDERED.test(l) || isRow(l) || /^\s*(-{3,}|\*{3,})\s*$/.test(l);

export function renderMarkdown(src) {
  const lines = String(src ?? "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) { out.push("<hr>"); i++; continue; }

    if (isRow(l) && i + 1 < lines.length && isSep(lines[i + 1])) {
      const head = cells(l); i += 2;
      const rows = [];
      while (i < lines.length && isRow(lines[i])) rows.push(cells(lines[i++]));
      out.push('<div class="table-responsive"><table class="table table-hover align-middle mb-0' + (head.length >= 5 ? " table-wide" : "") + '"><thead><tr>' +
        head.map((c) => `<th scope="col">${c}</th>`).join("") + "</tr></thead><tbody>" +
        rows.map((r) => "<tr>" + r.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") + "</tbody></table></div>");
      continue;
    }
    const hd = l.match(/^(#{1,4})\s+(.*)/);
    if (hd) { const tag = hd[1].length >= 4 ? "h4" : "h3"; out.push(`<${tag}>${inline(hd[2])}</${tag}>`); i++; continue; }

    if (/^>\s?/.test(l)) {
      const q = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) q.push(lines[i++].replace(/^>\s?/, ""));
      out.push(`<blockquote>${q.map(inline).join("<br>")}</blockquote>`);
      continue;
    }
    if (BULLET.test(l)) {
      const items = [];
      while (i < lines.length && BULLET.test(lines[i])) items.push(inline(lines[i++].replace(BULLET, "")));
      out.push("<ul>" + items.map((x) => `<li>${x}</li>`).join("") + "</ul>");
      continue;
    }
    if (ORDERED.test(l)) {
      const items = [];
      while (i < lines.length && ORDERED.test(lines[i])) items.push(inline(lines[i++].replace(ORDERED, "")));
      out.push("<ol>" + items.map((x) => `<li>${x}</li>`).join("") + "</ol>");
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !startsBlock(lines[i])) para.push(lines[i++]);
    if (!para.length) { para.push(lines[i++]); }  // defensive: never loop forever on odd input
    const nextIsTable = i < lines.length && isRow(lines[i]) && i + 1 < lines.length && isSep(lines[i + 1]);
    const text = para.join(" ").trim();
    if (/^~\s/.test(text)) {
      out.push(`<p class="subtitle">${inline(text.replace(/^~\s+/, ""))}</p>`);  // muted line directly under a heading
    } else if (nextIsTable && para.length === 1 && text.length <= 110 && !/[.:;!?]$/.test(text)) {
      out.push(`<h5 class="table-title">${inline(text)}</h5>`);  // a one-line caption directly above a table
    } else if (/^_[^_].*[^_]_$/.test(text) && !text.slice(1, -1).includes("_")) {
      out.push(`<p class="fine-print">${inline(text.slice(1, -1))}</p>`);  // a whole-paragraph italic note (disclaimers)
    } else {
      out.push(`<p>${para.map(inline).join("<br>")}</p>`);
    }
  }
  return out.join("");
}

/** Database text has one block per line. Give each line its own paragraph, keeping tables and lists together. */
export function paragraphize(text) {
  const out = [];
  let group = [], kind = null;
  const flush = () => { if (group.length) out.push(group.join("\n")); group = []; kind = null; };
  for (const raw of String(text || "").split("\n")) {
    const ln = raw.trim();
    if (!ln) { flush(); continue; }
    const k = isRow(ln) ? "table" : BULLET.test(ln) ? "bullet" : ORDERED.test(ln) ? "ordered" : /^#{1,4}\s/.test(ln) ? "heading" : "para";
    if (k !== kind || k === "heading" || k === "para") flush();
    kind = k;
    group.push(ln);
  }
  flush();
  return out.join("\n\n");
}
