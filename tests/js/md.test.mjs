// Run with: node --test tests/js/md.test.mjs     (Node 18+, no dependencies)
import test from "node:test";
import assert from "node:assert/strict";
import { renderMarkdown as r, paragraphize as p } from "../../static/js/md.js";

test("italic with underscores (the engine's notes and disclaimers)", () => {
  assert.equal(r("_Also listed at: Pune._"), '<p class="fine-print">Also listed at: Pune.</p>');
  assert.equal(r("a _b_ c"), "<p>a <em>b</em> c</p>");
});
test("snake_case words and URLs with underscores are left alone", () => {
  assert.equal(r("see some_file_name here"), "<p>see some_file_name here</p>");
  assert.ok(!r("go https://x.in/a_b_c now").includes("<em>"));
});
test("bold + links open in a new tab, only http(s)", () => {
  assert.equal(r("**Apply:** [site](https://a.in)"), '<p><strong>Apply:</strong> <a href="https://a.in" target="_blank" rel="noopener noreferrer">site</a></p>');
  assert.ok(!r("[x](javascript:alert(1))").includes("href"));
});
test("bare URLs become links", () => {
  assert.ok(r("Apply online: https://espro.christuniversity.in/Application/").includes('href="https://espro.christuniversity.in/Application/"'));
});
test("headings, lists, quotes", () => {
  assert.equal(r("### Fees — MBA"), "<h3>Fees — MBA</h3>");
  assert.equal(r("#### Section"), "<h4>Section</h4>");
  assert.equal(r("1. one\n2. two"), "<ol><li>one</li><li>two</li></ol>");
  assert.equal(r("- a\n- b"), "<ul><li>a</li><li>b</li></ul>");
  assert.equal(r("> note"), "<blockquote>note</blockquote>");
});
test("tables are responsive; a one-line caption above becomes a heading, a sentence does not", () => {
  const t = "| A | B |\n|---|---|\n| 1 | 2 |";
  assert.ok(r(t).includes('class="table-responsive"') && r(t).includes('scope="col"'));
  assert.ok(r("MBA (Business Analytics)\n" + t).startsWith('<h5 class="table-title">MBA (Business Analytics)</h5>'));
  assert.ok(r("The fee is below.\n" + t).startsWith("<p>The fee"));
});
test("HTML is escaped", () => {
  assert.equal(r("<img src=x onerror=alert(1)> **b**"), "<p>&lt;img src=x onerror=alert(1)&gt; <strong>b</strong></p>");
});
test("paragraphize: one paragraph per scraped line, tables and lists stay together", () => {
  assert.equal(p("one\ntwo\n\nthree"), "one\n\ntwo\n\nthree");
  assert.equal(p("intro\n| A |\n|---|\n| 1 |\n- x\n- y"), "intro\n\n| A |\n|---|\n| 1 |\n\n- x\n- y");
});
test("odd input terminates", () => {
  assert.ok(typeof r("> \n>\n|\n#\n- \n1. ") === "string");
});

test("'~ subtitle' paragraphs render as a muted subtitle", () => {
  assert.equal(r("### Fees\n\n~ Central Campus · as published"), '<h3>Fees</h3><p class="subtitle">Central Campus · as published</p>');
});
