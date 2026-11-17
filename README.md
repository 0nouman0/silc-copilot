# SILC Student Applicant Copilot

A conversational admissions assistant (FastAPI + OpenRouter) with a dashboard-style UI inspired by the supplied ERP screenshot.
Built from `STUDENT ADMISSION AGENT.pdf` (the 16 requirement areas) and `SILC_Student_Applicant_Copilot_Master_Prompt.md`.

> **Everything in `app/data/demo_data.py` and `app/knowledge/docs/` is fictional sample data**, labelled "demo" in the UI,
> in tool results and in source citations. It is not official university information.

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            # then edit .env — see below
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000. API docs: http://localhost:8000/api/docs.

### Configure OpenRouter (required for chat)

1. Create a key at https://openrouter.ai/keys and put it in **`.env`** (never committed; see `.gitignore`):
   ```
   LLM_PROVIDER=openrouter
   LLM_API_KEY=<your key>
   LLM_MODEL=perplexity/pplx-decider-flash-v1.3:free
   ```
2. That model is a **decisions model**: it answers typed questions (pick one / yes-probability / score) via
   `/api/alpha/decisions` and **cannot write text or call tools**. So the app runs in **`AGENT_MODE=decisions`**
   (auto-selected for `*decider*` models):
   the model only *classifies* (intent, programme, level, applicant category, a few applicant details) and our code
   runs the tools and writes the answer from templates. Consequences: answers can never contain model-invented facts,
   and prompt injection can at worst mis-route a question.
3. Any normal chat model (e.g. `openai/gpt-4o-mini`) switches the app to `AGENT_MODE=tools` (LLM writes answers using
   tools). Pick one that supports tool calling.

Measured on 54 real questions from the requirements PDF: **53/54** correct intent+programme.
Without a key the app still runs: pages work and the chat shows a clear "not configured" message.

### Switching to OpenAI (final target)

Config only — no code change: `LLM_PROVIDER=openai`, `LLM_API_KEY=<openai key>`, `LLM_MODEL=<model>`.
Both providers use the same OpenAI-style adapter (`app/llm/openai_compatible.py`). A vendor with a different wire format
needs one new class implementing `LLMProvider` plus one branch in `app/llm/__init__.py`.

> Use synthetic data with any provider until the institution approves it for applicant data.

## Try the personalised flow (demo mode)

Top-right **Sign in** → *Demo Applicant One* (3 sample applications) or *Demo Applicant Two* (1 application with an explicit offer).
The assistant announces the count, shows selectable application cards, remembers the selected one, and answers follow-ups
("status of this application?", "fees for this programme?"). Status text comes from the applicant API, never from the model.

## Architecture

```
Browser (static SPA)  ──►  FastAPI  ──►  Agent (decision engine | tool-calling) ──► OpenRouter decisions | chat provider
                              │                │
                              │                └─► Tool registry (small, typed, auth-aware tools)
                              │                        ├─ AdmissionsRepository  (programmes, fees, dates, …)
                              │                        ├─ KnowledgeStore        (RAG over approved documents)
                              │                        ├─ ApplicantApi          (account-specific, ownership enforced)
                              │                        └─ eligibility.assess()  (deterministic, not LLM)
                              └─ signed-cookie session (identity never comes from chat text)
```

| Concern | Where |
|---|---|
| Provider abstraction | `app/llm/` |
| Tool registry (15 focused tools) | `app/tools/registry.py` |
| Decision engine (decider models) | `app/agent/decision_engine.py`, `app/llm/decisions.py` |
| Tool-calling agent (chat models) | `app/agent/orchestrator.py`, `app/agent/prompt.py` |
| Eligibility logic (4 outcomes + disclaimer) | `app/eligibility.py` |
| Data interface + demo data | `app/data/` |
| Document ingestion / retrieval | `app/knowledge/store.py` |
| Applicant API adapter | `app/applicants/adapter.py` |
| Session / auth | `app/auth.py` |
| UI | `static/` (no build step) |

### Safety properties (all covered by tests)

- Answers come from tool results; missing data stays `null` and is reported as unavailable, never invented.
- Applicant tools are **not offered** to the model unless the user is signed in; ownership is re-checked on every call
  (another user's application reference returns the same "not found" as a non-existent one).
- The client-supplied "active application" is trusted only after the server verifies ownership.
- Status / offer status are returned exactly as the API gives them; the prompt forbids inferring selection.
- Eligibility is computed deterministically and always carries the "preliminary, not an admission decision" disclaimer.
- Production mode **never falls back** to demo data: it returns "data source unavailable" / "live status cannot be checked".
- Assistant output is rendered through an HTML-escaping markdown renderer; retrieved text is treated as reference data only.

## Test

```bash
pytest                      # 86 backend tests (eligibility, tools, decision engine, isolation, provider errors, API)
```

## What is complete vs. still needed

**Complete (working, demo data):** UI shell and 10 pages; chat with tool calling; hybrid tools + document retrieval; preliminary
eligibility; personalised application flow with cross-user isolation; provider switch; honest handling of missing data; error,
loading and empty states; responsive layout; keyboard/focus support.

**Uses demo data:** programmes, fees, dates, scholarships, hostel, documents, the 3 knowledge documents, and the applicant API.

**Needs the institution (not invented — see integration points):**

| Needed | Replace |
|---|---|
| Real programme/fee/date/scholarship/hostel/campus/contact data (DB or API; schema not yet supplied) | implement `AdmissionsRepository` in `app/data/repository.py`, return it from `get_repository()` |
| Applicant API (application list/status, auth) + real sign-in (SSO/OIDC) | implement `ApplicantApi` in `app/applicants/adapter.py`; replace `demo-login` in `app/routers/api.py` / `app/auth.py` |
| Approved brochures/policies for RAG | drop `.md` files (with front matter) in `app/knowledge/docs/`; for PDFs add an extractor, and for semantic search implement `KnowledgeStore` with embeddings (pgvector/Pinecone) |
| Support-ticket system, payment verification | not implemented on purpose — the assistant says so and routes to the Admissions Office |
| Approved LLM provider + data-processing terms | set `LLM_PROVIDER=openai` when approved |
| Admin knowledge-base UI, rate limiting beyond the in-memory limiter, persistent chat history | future phases |

Set `INTEGRATION_MODE=production` once real adapters exist; set `SESSION_SECRET` (required in production).
