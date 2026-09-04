# Phase 3 persistence and Streamlit

VictorIA connects to Supabase PostgreSQL through SQLAlchemy. The application never
uses Supabase browser credentials or embeds database credentials in source code.

## Environment

Use Python 3.12 and install the project dependencies:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Create a Supabase project and copy its session-pooler connection string from the
project's **Connect** panel. Session mode uses port 5432 and works from IPv4-only
application hosts. Ensure the password is URL-encoded and SSL is required.

Set these server-side environment variables:

```text
DATABASE_URL=postgresql://postgres.PROJECT_REF:URL_ENCODED_PASSWORD@POOLER_HOST:5432/postgres?sslmode=require
OPENAI_API_KEY=...
OPENAI_MODEL=...
```

Do not commit real values.

## Schema setup

Apply every migration to the configured database:

```bash
python -m alembic upgrade head
```

Check that ORM metadata and the applied migration remain aligned:

```bash
python -m alembic check
```

## Internal console

Start Streamlit from the repository root:

```bash
python -m streamlit run dashboard/streamlit_app.py
```

The Playground creates a Streamlit lead and conversation, persists every complete
turn, and can reopen any saved conversation. The Leads and Conversations views
read from the same repositories. Stopping Streamlit does not remove conversational
state because the selected ID is the only chat state retained by the UI.

## Failure monitoring

Keep the Streamlit terminal visible during manual tests. Agent failures are
logged with a customer-visible reference, conversation ID, category, stable
consistency code when applicable, attempt, stage, exception type, and sanitized
stack trace. To locate a reported reference in captured output, search for its
identifier without the `REF-` prefix, for example:

```bash
rg '8f31abcd' streamlit.log
```

Set `LOG_LEVEL=INFO` unless more verbose library diagnostics are temporarily
needed. Logs must not contain lead messages, provider response bodies, API keys,
or OAuth tokens.
