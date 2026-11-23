# CHRIST Admissions Copilot (unofficial)

A conversational admissions assistant **solely for CHRIST (Deemed to be University)**, built with FastAPI and OpenRouter.
It answers **only from CHRIST's own programme database** (the MySQL data provided by the university), quotes the stored text
exactly, and says so when something is not in the database. It is **not** an official CHRIST service and says so in the UI.

Built from `STUDENT ADMISSION AGENT.pdf` (the 16 requirement areas) and `SILC_Student_Applicant_Copilot_Master_Prompt.md`.

## How the data works (Applicant Co-pilot Build Blueprint)

```
read-only replica (agent_ro) ──► catalog builder ──► in-memory Catalog (immutable, swapped whole) ──► tools ──► skills ──► chat
          16 tables, fixed SQL     + config/*.yaml          BM25 + key indexes                 ▲
                                   + PDF text cache                                            └─ live ERP facts (NOT connected yet)
```

* The **catalog** (`app/catalog/`) is built at startup and on `POST /admin/catalog/refresh`; students keep the old one until the new one
  passes the checks (programmes down at most 20%, sections at most 30%, no fee/date/personal content). No per-request SQL.
* **Fees** are read on demand from the fee structure published for each programme in the university database (`course_related_links`, category F),
  shown exactly as stored with its academic year and a clear note when its stored validity date has passed (`FEES_SOURCE=none` switches this off).
  A programme with no stored fee says "Fee information currently unavailable" on its own row; fees are never loaded into the catalog and never invented.
* **Application dates, entrance-test dates, seats, hostel vacancy and application status are live ERP data.** The ERP client and the aggregation layer
  are a later phase: until then those tools (`app/live.py`) answer `available=false` with the admissions contact. The website's stored dates are never
  used, and the model never states one from memory.
* **Eligibility verdicts are computed in code** (`app/eligibility/`) from admissions-reviewed rules in `config/eligibility_rules.yaml`.
  Without a reviewed rule the assistant shows the published criteria and the admissions contact, no verdict.
* A **pre-router** (`app/agent/prerouter.py`) handles injection, refunds/complaints/"my application" (to a human) and missing-profile
  questions in code, before any model. **Skills** (`app/agent/skills.py`) give each turn a short instruction bundle and its own tool set.

| Answerable from the catalog | Not answerable (the assistant says so, never guesses) |
|---|---|
| 379 current programmes: level, campus(es), department, duration, term type | Application/test dates and seats (until the ERP is connected); fees only where a fee structure is stored |
| Eligibility, selection, entrance test, outcomes, structure, NRI / international, cancellation text, with academic year and expiry | Anyone's application status, offer or payment |
| Specialisations with roadmaps, term-by-term curriculum with credits, highlights, career paths | Admission chances or cutoffs, foreign-qualification equivalence |
| Shared Application / Admission / Selection blocks, campus contacts, facilities and reviewed topic pages (needs the grants in `docs/agent_ro_grants.sql`) | University profile, rankings |
| Brochure / structure PDF text with page numbers (after `python -m app.documents.build`) | |

### Operations

```bash
python -m eval.run_eval                 # golden + red-team checks (add --live to build from the database)
python -m app.eligibility.extract       # draft eligibility rules for admissions review (config/eligibility_drafts.yaml)
python -m app.catalog.candidates        # topic page candidates for review (config/topic_candidates.yaml)
python -m app.documents.build           # fetch + extract programme PDFs, then refresh the catalog
curl -X POST localhost:8000/admin/catalog/refresh -H "X-Admin-Token: $ADMIN_TOKEN" -d '{"force":true}'   # on EVERY instance
```
`GET /readyz` is 503 until the catalog is built; `GET /admin/catalog` returns the build report and the per-programme gap list for the web team.

## Run the app

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # then edit .env (see below)
uvicorn app.main:app --reload --port 8000
```
Open http://localhost:8000 (API docs at `/api/docs`). If the database is unreachable the app still starts; programme pages return
HTTP 503 and the assistant says the database cannot be reached instead of guessing.

### Database connection

`.env` (never committed):
```
CATALOGUE_SOURCE=mysql            # mysql | api | none   (default: mysql when AGENT_DB_USER is set)
APPLICANT_DB_HOST=127.0.0.1
APPLICANT_DB_PORT=3306
APPLICANT_DB_NAME=applicant
AGENT_DB_USER=agent_ro            # SELECT-only account on the public-content tables (see docs/ARCHITECTURE.md)
AGENT_DB_PASSWORD=...
```
Load the provided dump into a local MySQL first (the dump itself is gitignored). The catalogue (about 410 programme listings) is read once
and cached for 10 minutes.

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
renders the answer from the database text. Consequences: answers cannot contain model-invented facts and prompt injection can at
worst mis-route a question. A normal chat model with tool calling (e.g. `openai/gpt-4o-mini`) switches the app to `AGENT_MODE=tools`
(the model writes answers using the same tools). `LLM_PROVIDER=openai` is a config-only switch for the final target.

## One conversation, two agents

The whole product is a single natural-language chat (there are no separate pages to pick a programme, fees or dates from; the
JSON endpoints under `/api/programmes`, `/api/dates` etc. remain for other front-ends). The assistant starts the conversation
itself (`GET /api/welcome`) with *Hello! Welcome to CHRIST University. How can I help you? Which course would you like to enrol in at our college?*
and buttons for the university's biggest departments. There are **no accounts, no sign-in and no demo data**: the database holds public
programme content only, so the assistant never pretends to see an applicant's records. Two agents, each with its own prompt and tool set, share the chat:

| Agent | Job | Tools it may use |
|---|---|---|
| **Recommendation** | learns the applicant's interests, level and campus, suggests 3-5 programmes with reasons and career paths from the database, never states fees/dates/eligibility | `recommend_programmes`, `search_programmes`, `get_programme_details`, `get_programme_highlights`, `get_campus_details` |
| **Information** | fetches and reports facts (eligibility, fees, curriculum, selection, dates, how to apply, campus contact details and facilities); never recommends | every fetch tool, but not `recommend_programmes` |

Routing is keyword-based (`app/agent/router.py`) with the last agent sticky, so while the applicant answers the recommender's questions
it keeps the conversation, and a concrete fact question ("what are the fees?") hands over to the information agent. The tool sets are
enforced in code, not only in the prompt. In decider mode the same split exists as the `recommend_programmes` intent. Each agent can use its
own chat model: set `LLM_MODEL_RECOMMEND` and/or `LLM_MODEL_INFO` (both fall back to `LLM_MODEL`).

Recommendations are produced by matching the applicant's words (with a small synonym list such as *coding* to computer science) against
programme and department names in the database. They are suggestions, not admission advice.

## What it covers

Programmes (eligibility, fees by category, selection, curriculum, careers, placements), comparison of two programmes, the general
admission process, campuses, and a conservative preliminary percentage comparison that can never confirm eligibility.
If a programme name is ambiguous the assistant asks which one (same programme on several campuses is not ambiguous, and the other
campuses are disclosed).

The UI is the chat at `/`; every other path lands there.

## Architecture

| Concern | Where |
|---|---|
| Database access (read-only, parameterised) | `app/sources/mysql_source.py`; interface in `app/sources/base.py`; HTTP stub in `app/sources/api_source.py` |
| HTML-to-Markdown for stored text (real tables become tables) | `app/sources/text.py` |
| Programme catalogue + name resolver | `app/catalogue.py` |
| Preliminary eligibility hint | `app/eligibility_hint.py` |
| Tool registry (typed, source-referenced, per-agent tool sets) | `app/tools/registry.py` |
| Agent routing (info vs recommend) | `app/agent/router.py` |
| Opening message / API | `app/routers/api.py` (`/api/welcome`, `/api/chat`) |
| Decision engine (decider models) | `app/agent/decision_engine.py`, `app/llm/decisions.py` |
| Tool-calling agents (chat models) | `app/agent/orchestrator.py`, `app/agent/prompt.py` (one prompt per agent), `app/llm/` |
| UI | `static/`: Bootstrap 5.3 + Bootstrap Icons + Inter, vendored in `static/vendor/` (no CDN, no build step); `static/js/md.js` renders replies |

## Safety properties (covered by tests)

- Replies come from database text and tool results; gaps are reported, not filled.
- The model never writes SQL: it picks a tool and fills typed parameters. The DB account is SELECT-only on a whitelist of tables, the session is read-only, queries have a time limit and a row cap.
- CHRIST-only scope: unrelated questions are declined.
- Eligibility is stored as free text, so the app only flags a percentage below the lowest one mentioned and otherwise says "Official verification required". It never says someone is eligible.
- No personal data is read or kept: the DB account cannot see enquiry, payment or admin tables, and the assistant says plainly that it cannot see anyone's application status, offer or payment.
- The model's output is constrained to our own options (decisions mode) and rendered through an HTML-escaping markdown renderer.

## Tests

```bash
pytest                              # backend tests against an in-memory fake source (no network, no API key);
                                    # the live-database checks run only when the local MySQL and `agent_ro` exist
node --test tests/js/md.test.mjs    # the markdown renderer
```

## Known limits / what still needs CHRIST

- **No applicant data exists in the database** (it is the website CMS), so there is no sign-in and no "my applications". The assistant says so and points to the application status page and the Admissions Office contact stored in the database. Real application status would need an authorised CHRIST applicant API.
- **Campus facilities (hostel, library, transport...) need one grant.** The `campus_fecilities` table is clean public content but `agent_ro` cannot read it yet: run `docs/agent_ro_grants.sql` in MySQL Workbench as an administrator. Until then the assistant says hostel and facility details are not available to it. Campus addresses, phones, emails and websites already work (`campus_master`).
- **Application dates in the database are stale** (the newest close date is June 2020). The app shows them as recorded and says they may be out of date; it cannot know the current cycle.
- The `course_info` table (career paths, highlights) holds some double-encoded characters (`Â`, `â€™`); the app repairs those when it reads them. All other text is clean.
- Support tickets, payment verification and applying on a student's behalf are intentionally not implemented.

## Deploying

The data lives in MySQL, which Vercel (serverless) cannot reach on `localhost`. Either host the app next to the database, or expose the
catalogue through an HTTP API and implement `ApiSource` (`CATALOGUE_SOURCE=api`, `CATALOGUE_API_URL`, `CATALOGUE_API_TOKEN`); no tool, prompt or page changes.
Without a reachable source a deployment starts but answers "the database cannot be reached".

Environment variables (never read from git): `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL` (optionally `LLM_MODEL_RECOMMEND` / `LLM_MODEL_INFO`),
`CHAT_RATE_LIMIT_PER_MIN` (optional, default 20 per IP per instance), plus the database settings above.
`.vercelignore` keeps `tests/`, `.env`, SQL dumps and `.venv` out of the upload.
