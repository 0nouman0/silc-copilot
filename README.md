# CHRIST Admissions Copilot (unofficial)

A conversational admissions assistant **solely for CHRIST (Deemed to be University)**, built with FastAPI and OpenRouter.
It answers from information scraped from CHRIST's public website, quotes the university's own text, and links every answer
to the page it came from. It is **not** an official CHRIST service and says so in the UI.

Built from `STUDENT ADMISSION AGENT.pdf` (the 16 requirement areas) and `SILC_Student_Applicant_Copilot_Master_Prompt.md`.

## How the data works

```
christuniversity.in ──► scraper/  ──► data/christ/{programmes.json, pages.jsonl, meta.json}  ──► app/christ/store.py (BM25 index + programme resolver)
```

`python -m scraper.run` collects, politely (about 4 requests/second, identifying User-Agent, robots.txt honoured when present,
every response cached on disk so re-runs are cheap):

| Source | What |
|---|---|
| Programme catalogue (`ajax_course.php`) | every programme: level, campus, application open/close dates, apply link, brochure |
| Per-programme popups + tabs | **Eligibility** and **Fees** (tables by category: Karnataka / other states / NRI / foreign) plus ~25 lazy-loaded sections per page (selection process, application process, international, outcomes, queries…) |
| Link crawl | every other public page on `christuniversity.in` and its subdomains (caps per host), and CHRIST-run blogs on other domains |
| PDFs | brochures, fee structures, policies: text extracted with pypdf |

Not scraped, on purpose: anything behind a login (ERP/ESPro, Moodle, the application portal), booking and webmail hosts, third-party
sites that forbid or block crawlers (e.g. NIRF returns 403; Wikipedia requires contact details in the User-Agent, which this project does not invent).
The scraped data is **not committed** (`data/` is gitignored): it is CHRIST's content, so each deployment collects its own copy.

```bash
python -m scraper.run                       # full crawl (resumable; ~1–2 h first time, minutes afterwards thanks to the cache)
python -m scraper.run --max-pages 300 --limit-programmes 10   # quick smoke run
```
Re-run it periodically so fees and dates stay current. The UI banner shows when the data was collected.

## Run the app

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # then edit .env (see below)
python -m scraper.run             # collect CHRIST data (once, then periodically)
uvicorn app.main:app --reload --port 8000
```
Open http://localhost:8000 (API docs at `/api/docs`). Without `data/christ/` the app still starts and tells you to run the scraper.

### OpenRouter

`.env` (never committed):
```
LLM_PROVIDER=openrouter
LLM_API_KEY=<your key>
LLM_MODEL=perplexity/pplx-decider-flash-v1.3:free
```
That model is a **decisions model**: it answers typed questions (pick-one / yes-probability / score) via `/api/alpha/decisions`
and cannot write text. The app therefore runs in `AGENT_MODE=decisions` (auto-selected for `*decider*` models):
the model only *classifies* the question (intent, level, applicant category); code resolves the programme, runs the tools and
renders the answer from the scraped content. Consequences: answers cannot contain model-invented facts and prompt injection can at
worst mis-route a question. A normal chat model with tool calling (e.g. `openai/gpt-4o-mini`) switches the app to `AGENT_MODE=tools`
(the model writes answers using the same tools). `LLM_PROVIDER=openai` is a config-only switch for the final target.

## What it covers

Programmes (350+ with campus/dates/overview/curriculum sections), eligibility (published text + a conservative preliminary
percentage comparison that can never confirm eligibility), application dates, fees by category, selection process, application
help, documents, scholarships, hostel, campus, placements, international/NRI admission, contact, and about-the-university.
If a programme name is ambiguous the assistant asks which one (same programme on several campuses is not ambiguous, and the other
campuses are disclosed). Anything the site did not publish is reported as unavailable, never guessed.

Pages: `/` chat · `/programmes` (+ detail) · `/eligibility` · `/admissions` · `/fees` · `/scholarships` · `/documents` · `/hostel` ·
`/campuses` · `/placements` · `/international` · `/support`.

## Architecture

| Concern | Where |
|---|---|
| Scraper | `scraper/` (`http.py` polite cached fetcher, `extract.py` HTML→markdown, `run.py` pipeline) |
| Corpus, BM25 index, programme resolver | `app/christ/store.py` |
| Preliminary eligibility hint | `app/christ/eligibility_hint.py` |
| Tool registry (typed, source-linked) | `app/tools/registry.py` |
| Decision engine (decider models) | `app/agent/decision_engine.py`, `app/llm/decisions.py` |
| Tool-calling agent (chat models) | `app/agent/orchestrator.py`, `app/agent/prompt.py`, `app/llm/` |
| Applicant API adapter (**mock**) | `app/applicants/adapter.py` |
| Session / auth | `app/auth.py` |
| UI | `static/` (no build step) |

## Safety properties (covered by tests)

- Replies come from scraped content and tool results; gaps are reported, not filled. Every answer links to its public source page.
- CHRIST-only scope: unrelated questions are declined.
- Eligibility is published as free text, so the app only flags a percentage below the lowest one mentioned and otherwise says "Official verification required". It never says someone is eligible.
- Applicant tools are not offered unless signed in; ownership is re-checked on every call; the client-supplied active application is trusted only after server verification; status/offer text is returned exactly as the API gives it and never reinterpreted as selection.
- The model's output is constrained to our own options (decisions mode) and rendered through an HTML-escaping markdown renderer.

## Tests

```bash
pytest        # 97 backend tests against a small fixture corpus (no network, no API key)
```

## Known limits / what still needs CHRIST

- **No live applicant system is connected.** "My applications" uses a clearly labelled **mock** (`DEMO-APP-…`). Real application status needs an authorised CHRIST API and real sign-in; implement `ApplicantApi` in `app/applicants/adapter.py` and replace `demo-login`.
- Data is only as current as the last scrape and only what CHRIST publishes publicly. Confirm important details with the Admissions Office.
- Support tickets, payment verification and applying on a student's behalf are intentionally not implemented.
- Respect CHRIST's website terms when scraping; keep the polite defaults and consider asking the university for an official data feed.
