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
python scrape.py                       # full crawl (resumable; ~1-2 h first time, minutes afterwards thanks to the cache)
python scrape.py status                # is it running? what has been saved so far?
python scrape.py --max-pages 300 --limit-programmes 10   # quick trial
```
`scrape.py` works from any directory and with any `python` (it switches to the project's `.venv` itself); `python -m scraper` also works
from the project root. A second scrape is refused while one is running, so two crawlers never hit the site at once. The scraper saves
**checkpoints** (programmes first, then pages every 500, then sections and PDFs) and the app reloads them automatically, so the chat
is usable minutes after starting.
Re-run it periodically so fees and dates stay current. The UI banner shows when the data was collected.

## Run the app

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # then edit .env (see below)
python scrape.py                  # collect CHRIST data (once, then periodically)
uvicorn app.main:app --reload --port 8000
```
Open http://localhost:8000 (API docs at `/api/docs`). Without `data/christ/` the app still starts and tells you to run `python scrape.py`.

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
| UI | `static/`: Bootstrap 5.3 + Bootstrap Icons + Inter, vendored in `static/vendor/` (no CDN, no build step). Content formatting: `app/christ/format.py` (server) and `static/js/md.js` (renderer) |

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

## Deploying to Vercel

Vercel builds from git and has no `data/` folder (it is gitignored), so deployments read a **compressed bundle** instead:

```bash
python scrape.py                # collect the data (once; see above)
python scrape.py bundle         # -> deploy_data/christ/*.gz  (~4 MB now; trimmed + compressed, PDFs limited to admission-relevant ones)
git add deploy_data && git commit -m "data: refresh CHRIST bundle" && git push
```
The app uses `data/christ/` when it exists (local dev) and otherwise `deploy_data/christ/`. Cold start loads and indexes the bundle in ~2–3 s.
`requirements.txt` holds only runtime dependencies (the scraper and tests use `requirements-dev.txt`); `.vercelignore` keeps `data/`, `tests/`, `scraper/` and `.venv` out of the upload.

Set these in **Vercel → Project → Settings → Environment Variables** (secrets are never read from git):

| Variable | Value |
|---|---|
| `LLM_PROVIDER` | `openrouter` |
| `LLM_API_KEY` | your OpenRouter key |
| `LLM_MODEL` | `perplexity/pplx-decider-flash-v1.3:free` |
| `SESSION_SECRET` | a long random string (required if `INTEGRATION_MODE=production`) |
| `CHAT_RATE_LIMIT_PER_MIN` | optional, default 20 per IP per instance |

Refresh the data by re-running the scrape, re-bundling, committing and pushing (Vercel redeploys on push).
The "my applications" demo uses a **mock** applicant API; no live CHRIST system is connected.

## Front-end notes

- **Components:** Bootstrap 5.3 (navbar, cards, pagination, accordion, modal, forms, badges) with the CHRIST blue/gold set through Bootstrap's own CSS variables in `static/css/app.css`; icons are Bootstrap Icons; the typeface is Inter. All three are vendored under `static/vendor/` (see `static/vendor/README.md` for versions and licences), so the site has no CDN dependency and Vercel needs no build step.
- **Answer formatting:** the server turns scraped text into tidy markdown (`prose()`: one paragraph per line, call-to-action labels like "Apply Now" removed, split dates re-joined; replies start with a `###` title and a muted `~` subtitle). `md.js` renders it safely (HTML is escaped, links are http(s) only, tables are responsive).
- **Tests:** `pytest` (Python) and `node --test tests/js/md.test.mjs` (the markdown renderer).
