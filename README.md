# CHRIST Admissions Copilot (unofficial)

A conversational admissions assistant **solely for CHRIST (Deemed to be University)**, built with FastAPI and OpenRouter.
It answers **only from CHRIST's own programme database** (the MySQL data provided by the university), quotes the stored text
exactly, and says so when something is not in the database. It is **not** an official CHRIST service and says so in the UI.

Built from `STUDENT ADMISSION AGENT.pdf` (the 16 requirement areas) and `SILC_Student_Applicant_Copilot_Master_Prompt.md`.

## How the data works

```
MySQL `applicant` database ──► MySqlSource (read-only `agent_ro`, predefined parameterised SQL) ──► Catalogue + tool registry ──► agent / pages
```

There is **no scraped website data** and no search index over web pages. Every programme fact, every page and every answer is read
from the database through `app/sources/` (`CatalogueSource`). When the DB is replaced by an HTTP API, only `app/sources/api_source.py`
changes. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the tables used and the rules the design enforces.

What the database holds, and therefore what the assistant can answer:

| Answerable from the database | Not in the database (the assistant says so, never guesses) |
|---|---|
| Programme list: name, level, campus, department, duration, term type, approved intake | Hostel and accommodation |
| Eligibility and fee text, selection process, entrance test, outcomes, structure, placements, merit scholarship (per programme) | General scholarships, university profile, rankings |
| Term-by-term curriculum with credits, programme highlights and career paths | Admissions phone numbers / email addresses, payment support |
| General admission process per programme group (how to apply, selection, results) | Class start dates / academic calendar, required-documents checklist |
| Campus names | Campus facilities, library, transport |
| Application open/close dates **as recorded on each programme** (the stored dates are mostly from 2017-2020, so they are shown with a "may be out of date" notice) | |

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

## What it covers

Programmes (eligibility, fees by category, selection, curriculum, careers, placements), comparison of two programmes, the general
admission process, campuses, and a conservative preliminary percentage comparison that can never confirm eligibility.
If a programme name is ambiguous the assistant asks which one (same programme on several campuses is not ambiguous, and the other
campuses are disclosed).

Pages: `/` chat · `/programmes` (+ detail) · `/eligibility` · `/admissions` · `/fees` · `/apply` · `/campuses` · `/support`.

## Architecture

| Concern | Where |
|---|---|
| Database access (read-only, parameterised) | `app/sources/mysql_source.py`; interface in `app/sources/base.py`; HTTP stub in `app/sources/api_source.py` |
| HTML-to-Markdown for stored text (real tables become tables) | `app/sources/text.py` |
| Programme catalogue + name resolver | `app/catalogue.py` |
| Preliminary eligibility hint | `app/eligibility_hint.py` |
| Tool registry (typed, source-referenced) | `app/tools/registry.py` |
| Decision engine (decider models) | `app/agent/decision_engine.py`, `app/llm/decisions.py` |
| Tool-calling agent (chat models) | `app/agent/orchestrator.py`, `app/agent/prompt.py`, `app/llm/` |
| Applicant API adapter (**mock**) | `app/applicants/adapter.py` |
| Session / auth | `app/auth.py` |
| UI | `static/`: Bootstrap 5.3 + Bootstrap Icons + Inter, vendored in `static/vendor/` (no CDN, no build step); `static/js/md.js` renders replies |

## Safety properties (covered by tests)

- Replies come from database text and tool results; gaps are reported, not filled.
- The model never writes SQL: it picks a tool and fills typed parameters. The DB account is SELECT-only on a whitelist of tables, the session is read-only, queries have a time limit and a row cap.
- CHRIST-only scope: unrelated questions are declined.
- Eligibility is stored as free text, so the app only flags a percentage below the lowest one mentioned and otherwise says "Official verification required". It never says someone is eligible.
- Applicant tools are not offered unless signed in; ownership is re-checked on every call; the client-supplied active application is trusted only after server verification; status/offer text is returned exactly as the API gives it and never reinterpreted as selection.
- The model's output is constrained to our own options (decisions mode) and rendered through an HTML-escaping markdown renderer.

## Tests

```bash
pytest                              # backend tests against an in-memory fake source (no network, no API key);
                                    # the live-database checks run only when the local MySQL and `agent_ro` exist
node --test tests/js/md.test.mjs    # the markdown renderer
```

## Known limits / what still needs CHRIST

- **No live applicant system is connected.** "My applications" uses a clearly labelled **mock** (`DEMO-APP-…`). Real application status needs an authorised CHRIST API and real sign-in; implement `ApplicantApi` in `app/applicants/adapter.py` and replace `demo-login`.
- **Application dates in the database are stale** (the newest close date is June 2020). The app shows them as recorded and says they may be out of date; it cannot know the current cycle.
- The `course_info` table (career paths, highlights) holds some double-encoded characters (`Â`, `â€™`); the app repairs those when it reads them. All other text is clean.
- Support tickets, payment verification and applying on a student's behalf are intentionally not implemented.

## Deploying

The data lives in MySQL, which Vercel (serverless) cannot reach on `localhost`. Either host the app next to the database, or expose the
catalogue through an HTTP API and implement `ApiSource` (`CATALOGUE_SOURCE=api`, `CATALOGUE_API_URL`, `CATALOGUE_API_TOKEN`); no tool, prompt or page changes.
Without a reachable source a deployment starts but answers "the database cannot be reached".

Environment variables (never read from git): `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `SESSION_SECRET` (required if
`INTEGRATION_MODE=production`), `CHAT_RATE_LIMIT_PER_MIN` (optional, default 20 per IP per instance), plus the database settings above.
`.vercelignore` keeps `tests/`, `.env`, SQL dumps and `.venv` out of the upload.
The "my applications" demo uses a **mock** applicant API; no live CHRIST system is connected.
