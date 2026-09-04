# Phase 4 booking

VictorIA uses a provider-neutral scheduling service. The default provider is a
deterministic mock, so the complete flow can be tested without Google:

    SCHEDULING_PROVIDER=mock

Availability uses America/Recife, Monday through Friday from 09:00 to 18:00,
60-minute Calendar blocks, at least 24 hours' notice, and the next 10 business
days. The customer-facing conversation takes approximately 45 minutes; the
remaining 15 minutes are an advisor buffer.
Brazilian national and Pernambuco state holidays are excluded. Additional
company or municipal closures can be configured as comma-separated ISO dates:

    SCHEDULING_BLACKOUT_DATES=2026-06-24,2026-12-24

The agent shows three choices and requires both a slot selection and a separate
confirmation before booking. During this flow it resolves Portuguese weekday,
date, morning/afternoon, and "after" preferences against the scheduler;
availability questions do not rely on an LLM-generated answer.

## Manual mock test

1. Start Streamlit and qualify a lead until VictorIA offers availability.
2. Ask a question about the service. Verify it is answered without another CTA.
3. Reply "sim" to request availability.
4. Reply "1", "2", or "3".
5. Verify VictorIA repeats the exact time but does not book it yet.
6. Reply "confirmo".
7. Verify the stage is BOOKED, a meeting link is shown, and the Leads view
   contains the persisted meeting date.
8. Restart Streamlit and reopen the conversation; the booking must remain intact.

## Google Calendar setup

VictorIA uses OAuth as the advisor Google account. This lets Calendar create a
Google Meet owned by that user and send invitations to leads that have an email.

1. Create or select a Google Cloud project and enable the Google Calendar API.
2. In **Google Auth Platform**, configure an External consent app.
3. Add these scopes under **Data Access**:
   - `https://www.googleapis.com/auth/calendar.events`
   - `https://www.googleapis.com/auth/calendar.events.freebusy`
4. While the app is in Testing, add the advisor Google account under **Audience >
   Test users**.
5. Create an OAuth client with application type **Desktop app** and download the
   client JSON. Keep it outside the repository.
6. Configure the client and token paths, using absolute paths:

    SCHEDULING_PROVIDER=google
    GOOGLE_CALENDAR_ID=advisor-calendar-id
    GOOGLE_OAUTH_CLIENT_FILE=/absolute/path/to/client_secret.json
    GOOGLE_OAUTH_TOKEN_FILE=/absolute/path/to/google_oauth_token.json

7. Authorize the Google account that owns the configured calendar:

    python -m scripts.authorize_google_calendar

8. Sign in with the advisor account, accept the two Calendar permissions, and
   verify that the token file was created. Restart Streamlit after changing the
   environment.

`GOOGLE_OAUTH_CLIENT_JSON` may replace the client file for authorization.
`GOOGLE_OAUTH_TOKEN_JSON` may replace the token file at runtime and takes
precedence when both token forms are configured. Never commit client credentials
or authorized-user tokens.

An External app left in Testing issues a refresh token that expires after seven
days for Calendar scopes. Re-run the authorization command when it expires.
Before continuously deploying VictorIA, change the consent app publishing status
to **In production** and authorize it again.

The Google provider reads busy intervals, rechecks the selected time before
creation, creates a unique Google Meet, and uses a deterministic event ID for
safe retries. If the lead has an email, Google sends a Calendar invitation. If
not, booking still succeeds and the Meet link is returned in the conversation.

Apply the database migration before starting the updated application:

    python -m alembic upgrade head
    python -m alembic check
