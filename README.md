# VictorIA — AI Sales Agent for Financial Services

> A stateful, safety-aware AI sales agent that qualifies financial-services leads, routes them to the right service, and schedules a verified meeting with a human specialist.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-Structured_output-412991?logo=openai&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supabase-4169E1?logo=postgresql&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-Internal_console-FF4B4B?logo=streamlit&logoColor=white)
![Google Calendar](https://img.shields.io/badge/Google_Calendar-OAuth_booking-4285F4?logo=googlecalendar&logoColor=white)
![Tests](https://img.shields.io/badge/tests-106_passing-2EA44F)

## At a glance

| | |
|---|---|
| **Domain** | Financial planning and investment advisory |
| **Audience** | Medical doctors with complex financial needs |
| **Agent goal** | Discover, qualify, route, handle objections, and book |
| **Safety boundary** | No personalized financial or investment advice |
| **Current interface** | Streamlit Playground and internal console |
| **Integrations** | OpenAI, Supabase PostgreSQL, Google Calendar and Meet |
| **Current status** | Conversational core, persistence, and booking implemented |

## Problem

Financial-services teams spend significant time on repetitive early-stage conversations. Inbound leads may not know which service they need, whether they are ready for professional support, or whether a meeting is the appropriate next step.

For medical doctors, that challenge is amplified by limited time, fragmented income, investments spread across institutions, and long-term goals that often lack an integrated strategy.

A useful AI sales agent must therefore do more than chat. It must qualify consistently, respect advisory boundaries, preserve conversational state, and only claim a meeting after the scheduling provider confirms it.

## Solution

VictorIA conducts a concise consultative conversation and transforms it into auditable commercial state:

1. Understand the lead's need and primary pain.
2. Collect only the evidence required for qualification.
3. Route the lead to **Financial Planning**, **Investment Advisory**, both, or no current fit.
4. Detect and address commercial objections.
5. Offer booking only when deterministic eligibility rules allow it.
6. Query real availability and create a Google Calendar event with a Google Meet link.
7. Persist the full conversation so it can be reopened with its state intact.

The language model interprets the conversation and produces structured output. Deterministic application rules remain responsible for qualification scoring, service routing, state transitions, booking eligibility, guardrails, and scheduling confirmation.

## Architecture

VictorIA separates probabilistic language understanding from deterministic business control:

```text
Streamlit Playground                 WhatsApp (planned)
         │                                  │
         └──────────── Sales Service ───────┘
                          │
              ┌───────────┴───────────┐
              │                       │
       Sales Agent              Scheduling Service
   OpenAI structured output      Mock / Google Calendar
              │                       │
       Deterministic rules       Availability + Meet
              │
      SQLAlchemy repositories
              │
      Supabase PostgreSQL
```

A polished architecture diagram will be added during the portfolio-polish phase.

## Demo

The current Streamlit application includes:

- a Playground for real conversations with VictorIA;
- persisted conversation reopening;
- live qualification, routing, objection, and booking state;
- Leads and Conversations views;
- Google Calendar availability and confirmed Meet booking.

```bash
python -m streamlit run dashboard/streamlit_app.py
```

A short conversation GIF/video and dashboard screenshot are planned for the next portfolio-polish pass.

## AI Agent

The Sales Agent is designed around structured, inspectable decisions rather than free-form prompting alone.

| Capability | Implementation |
|---|---|
| Structured output | Strict schema validated with Pydantic |
| Conversation state | Explicit stages from opening through booking or closure |
| Qualification | Weighted evidence with deterministic scoring |
| Service routing | Rule-based mapping from validated lead signals |
| Objections | Explicit objection taxonomy and state-aware handling |
| Safety | Deterministic guardrails for advice, credentials, and scope |
| Reliability | Bounded retries, safe fallbacks, reference codes, and stack-trace logging |
| Booking | Provider-neutral workflow with separate selection and confirmation |

The agent deliberately does **not** construct portfolios, recommend securities, promise returns, access financial accounts, or replace a qualified human professional.

## Evaluation

Quality is treated as an engineering problem, not a subjective prompt-writing exercise.

Currently implemented:

- deterministic unit and integration tests;
- Streamlit interaction tests with `AppTest`;
- structured-output validation and retry behavior;
- booking idempotency and provider-failure tests;
- financial-advice and out-of-scope guardrails;
- **106 passing automated tests**.

Planned evaluation work includes a dedicated Conversation Evaluator, a fixed behavioral regression set, 30 synthetic medical-doctor personas, and a controlled Prompt V1 versus Prompt V2 experiment. Synthetic results will always be labeled separately from real customer outcomes.

## Analytics

The data model already persists the entities required for commercial analysis:

- leads and qualification scores;
- conversations, stages, and prompt versions;
- messages and delivery state;
- meeting status and datetime;
- future evaluator outputs.

The planned analytics layer will expose the qualification funnel, meeting booking rate, qualified-to-meeting rate, average turns per booking, objection resolution, evaluator scores, and guardrail violations. These dashboards are not presented as complete yet.

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| LLM | OpenAI API with structured output |
| Validation | Pydantic |
| Persistence | PostgreSQL on Supabase |
| ORM and migrations | SQLAlchemy and Alembic |
| Internal application | Streamlit |
| Scheduling | Google Calendar API with OAuth |
| Meeting link | Google Meet |
| Testing | pytest and Streamlit AppTest |

## How it was built

VictorIA is being developed in deliberate, testable phases:

- **Business specification:** service boundaries, ICP, qualification, routing, objections, and safety rules.
- **Synthetic dataset:** 30 fictional medical-doctor personas with expected fit and routing.
- **Sales core:** structured agent output plus deterministic commercial rules.
- **Persistence and Playground:** PostgreSQL state, conversation reopening, and internal inspection.
- **Booking:** real availability, confirmation, Calendar event, invitation, and Meet link.
- **Next:** WhatsApp delivery, automated evaluation, analytics, prompt experimentation, and portfolio polish.

This sequence keeps business decisions, AI behavior, application state, and external integrations independently testable.

## Run locally

### 1. Create the environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 2. Configure the environment

Copy the complete template and fill in your local values:

```bash
cp .env.example .env
```

| Variable | Required when | Purpose |
|---|---|---|
| `DATABASE_URL` | Always | PostgreSQL/Supabase connection string |
| `OPENAI_API_KEY` | Always | Server-side OpenAI credential |
| `OPENAI_MODEL` | Always | Model used for structured agent output |
| `LOG_LEVEL` | Optional | Application log level; defaults to `INFO` |
| `SCHEDULING_PROVIDER` | Optional | `mock` for local use or `google`; defaults to `mock` |
| `SCHEDULING_BLACKOUT_DATES` | Optional | Comma-separated closure dates in `YYYY-MM-DD` format |
| `GOOGLE_CALENDAR_ID` | Google provider | Calendar used for availability and events |
| `GOOGLE_OAUTH_TOKEN_FILE` | Google provider | Path to the authorized-user token file |
| `GOOGLE_OAUTH_TOKEN_JSON` | Google provider alternative | Token JSON supplied directly; takes precedence over the file |
| `GOOGLE_OAUTH_CLIENT_FILE` | OAuth authorization | Path to the downloaded desktop OAuth client file |
| `GOOGLE_OAUTH_CLIENT_JSON` | OAuth authorization alternative | Desktop OAuth client JSON supplied directly |

Use one token source and one client source; JSON values are alternatives to file paths, not additional requirements. Never commit `.env`, OAuth client credentials, or authorized-user tokens. See [`docs/booking.md`](docs/booking.md) for the complete Google OAuth flow.

### 3. Apply migrations

```bash
set -a
source .env
set +a
python -m alembic upgrade head
python -m alembic check
```

### 4. Run the application

```bash
python -m streamlit run dashboard/streamlit_app.py
```

### 5. Run the tests

```bash
python -m pytest -q
```

## Repository guide

```text
app/          Agent, schemas, deterministic rules, persistence, and scheduling
dashboard/    Streamlit Playground and internal console
prompts/      Versioned agent prompts
data/         Synthetic evaluation personas
tests/        Unit, integration, booking, persistence, and UI tests
docs/         Business, persistence, dataset, and Calendar documentation
migrations/   Reproducible database migrations
```

## Roadmap

- [x] Business rules and synthetic personas
- [x] Structured Sales Agent and deterministic guardrails
- [x] PostgreSQL persistence and Streamlit Playground
- [x] Google Calendar OAuth booking and Google Meet creation
- [ ] WhatsApp Cloud API channel
- [ ] Conversation Evaluator and analytics dashboard
- [ ] Fixed evaluation suite and prompt experiment
- [ ] Demo media and final architecture artwork

---

**VictorIA demonstrates how to build an AI-powered commercial workflow whose language is flexible, but whose business decisions, safety boundaries, persistence, and external actions remain testable and controlled.**
