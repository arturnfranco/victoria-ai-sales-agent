"""Authorize VictorIA to use a Google account's calendar."""

from __future__ import annotations

import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from app.services.scheduling import GOOGLE_CALENDAR_SCOPES, _load_json_setting


def main() -> None:
    token_file = os.getenv("GOOGLE_OAUTH_TOKEN_FILE")
    if not token_file:
        raise SystemExit("GOOGLE_OAUTH_TOKEN_FILE is required")

    client_config = _load_json_setting(
        json_name="GOOGLE_OAUTH_CLIENT_JSON",
        file_name="GOOGLE_OAUTH_CLIENT_FILE",
        description="Google OAuth client credentials",
    )
    flow = InstalledAppFlow.from_client_config(
        client_config, scopes=GOOGLE_CALENDAR_SCOPES
    )
    credentials = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        open_browser=True,
    )

    destination = Path(token_file).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(credentials.to_json(), encoding="utf-8")
    destination.chmod(0o600)
    print(f"Google Calendar authorization saved to {destination}")


if __name__ == "__main__":
    main()
