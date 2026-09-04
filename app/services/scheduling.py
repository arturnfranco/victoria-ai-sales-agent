"""Scheduling providers for deterministic tests and Google Calendar."""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

import holidays

from app.schemas.booking import (
    AvailabilityPreference,
    BookingRequest,
    BookingResult,
    BookingSlot,
)


GOOGLE_CALENDAR_SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.events.freebusy",
)


class SchedulingError(RuntimeError):
    """Safe scheduling failure."""


class SlotUnavailableError(SchedulingError):
    """Raised when a selected slot is no longer free."""


class SchedulingService(Protocol):
    name: str

    def get_available_slots(
        self,
        *,
        after: datetime | None = None,
        limit: int = 3,
        preference: AvailabilityPreference | None = None,
    ) -> list[BookingSlot]: ...

    def book_meeting(self, request: BookingRequest) -> BookingResult: ...


class DeterministicSchedulingService:
    """In-memory scheduler with a stable, configurable clock."""

    name = "mock"

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        busy: list[tuple[datetime, datetime]] | None = None,
        blackout_dates: set[date] | None = None,
        timezone_name: str = "America/Recife",
    ) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._busy = list(busy or [])
        self._blackout_dates = set(blackout_dates or set())
        self._timezone = ZoneInfo(timezone_name)
        self._bookings: dict[str, BookingResult] = {}

    def get_available_slots(
        self,
        *,
        after: datetime | None = None,
        limit: int = 3,
        preference: AvailabilityPreference | None = None,
    ) -> list[BookingSlot]:
        earliest = self._now().astimezone(self._timezone) + timedelta(hours=24)
        if after is not None:
            earliest = max(earliest, after.astimezone(self._timezone))
        slots: list[BookingSlot] = []
        day = max(
            earliest.date(),
            preference.start_date if preference and preference.start_date else earliest.date(),
        )
        final_day = preference.end_date if preference else None
        holiday_years = range(day.year, (final_day or (day + timedelta(days=30))).year + 1)
        non_working = holidays.country_holidays("BR", subdiv="PE", years=holiday_years)
        excluded_dates = self._blackout_dates | (
            preference.excluded_dates if preference else set()
        )
        business_days = 0
        while len(slots) < limit and business_days < 10:
            if final_day and day > final_day:
                break
            if day.weekday() < 5 and day not in non_working and day not in excluded_dates:
                business_days += 1
                for hour in range(9, 18):
                    start = datetime.combine(day, time(hour), self._timezone)
                    end = start + timedelta(hours=1)
                    if preference and preference.earliest_time:
                        if start.timetz().replace(tzinfo=None) < preference.earliest_time:
                            continue
                    if preference and preference.latest_time:
                        if end.timetz().replace(tzinfo=None) > preference.latest_time:
                            continue
                    if start >= earliest and self._is_free(start, end):
                        slots.append(_slot(start, end))
                        if len(slots) == limit:
                            break
            day += timedelta(days=1)
        return slots

    def book_meeting(self, request: BookingRequest) -> BookingResult:
        existing = self._bookings.get(request.operation_id)
        if existing is not None:
            return existing
        if not self._is_free(request.slot.starts_at, request.slot.ends_at):
            raise SlotUnavailableError("selected slot is no longer available")
        result = BookingResult(
            provider_event_id=f"mock-{request.operation_id}",
            slot=request.slot,
            meeting_url=f"https://meet.example.test/{request.operation_id}",
        )
        self._busy.append((request.slot.starts_at, request.slot.ends_at))
        self._bookings[request.operation_id] = result
        return result

    def _is_free(self, start: datetime, end: datetime) -> bool:
        return all(end <= busy_start or start >= busy_end for busy_start, busy_end in self._busy)


class GoogleCalendarSchedulingService:
    """Google Calendar adapter using free/busy and idempotent event IDs."""

    name = "google"

    def __init__(
        self,
        *,
        calendar_id: str,
        client: Any,
        now: Callable[[], datetime] | None = None,
        timezone_name: str = "America/Recife",
        blackout_dates: set[date] | None = None,
    ) -> None:
        self._calendar_id = calendar_id
        self._client = client
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._timezone = ZoneInfo(timezone_name)
        self._blackout_dates = set(blackout_dates or set())

    def get_available_slots(
        self,
        *,
        after: datetime | None = None,
        limit: int = 3,
        preference: AvailabilityPreference | None = None,
    ) -> list[BookingSlot]:
        earliest = self._now().astimezone(self._timezone) + timedelta(hours=24)
        if after is not None:
            earliest = max(earliest, after.astimezone(self._timezone))
        if preference and preference.start_date:
            preferred_start = datetime.combine(
                preference.start_date, time.min, self._timezone
            )
            earliest = max(earliest, preferred_start)
        if preference and preference.end_date:
            horizon = datetime.combine(
                preference.end_date + timedelta(days=1), time.min, self._timezone
            )
        else:
            horizon = earliest + timedelta(days=16)
        try:
            response = self._client.freebusy().query(
                body={
                    "timeMin": earliest.isoformat(),
                    "timeMax": horizon.isoformat(),
                    "timeZone": self._timezone.key,
                    "items": [{"id": self._calendar_id}],
                }
            ).execute()
        except Exception as exc:
            raise SchedulingError("Google Calendar availability lookup failed") from exc
        calendar = response.get("calendars", {}).get(self._calendar_id, {})
        if calendar.get("errors"):
            raise SchedulingError("Google Calendar availability lookup failed")
        busy = [
            (datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["end"]))
            for item in calendar.get("busy", [])
        ]
        mock = DeterministicSchedulingService(
            now=self._now,
            busy=busy,
            blackout_dates=self._blackout_dates,
            timezone_name=self._timezone.key,
        )
        return mock.get_available_slots(
            after=after, limit=limit, preference=preference
        )

    def book_meeting(self, request: BookingRequest) -> BookingResult:
        event_id = _google_event_id(request.operation_id)
        try:
            existing = self._client.events().get(
                calendarId=self._calendar_id, eventId=event_id
            ).execute()
        except Exception as exc:
            if _http_status(exc) != 404:
                raise SchedulingError("Google Calendar event lookup failed") from exc
        else:
            return _booking_result(existing, request.slot)

        current = self.get_available_slots(after=request.slot.starts_at, limit=20)
        if not any(slot.starts_at == request.slot.starts_at for slot in current):
            raise SlotUnavailableError("selected slot is no longer available")
        body = {
            "id": event_id,
            "summary": f"VictorIA — conversa com {request.lead_name}",
            "start": {
                "dateTime": request.slot.starts_at.isoformat(),
                "timeZone": self._timezone.key,
            },
            "end": {
                "dateTime": request.slot.ends_at.isoformat(),
                "timeZone": self._timezone.key,
            },
            "conferenceData": {
                "createRequest": {
                    "requestId": request.operation_id,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        if request.lead_email:
            body["attendees"] = [{"email": request.lead_email}]
        try:
            event = self._client.events().insert(
                calendarId=self._calendar_id,
                body=body,
                conferenceDataVersion=1,
                sendUpdates="all" if request.lead_email else "none",
            ).execute()
        except Exception as exc:
            if _http_status(exc) != 409:
                raise SchedulingError("Google Calendar event creation failed") from exc
            event = self._client.events().get(
                calendarId=self._calendar_id, eventId=event_id
            ).execute()
        return _booking_result(event, request.slot)


def _booking_result(event: dict[str, Any], slot: BookingSlot) -> BookingResult:
    meeting_url = event.get("hangoutLink")
    if not meeting_url:
        for entry in event.get("conferenceData", {}).get("entryPoints", []):
            if entry.get("entryPointType") == "video":
                meeting_url = entry.get("uri")
                break
    if not meeting_url:
        raise SchedulingError("Google Calendar did not return a Meet link")
    return BookingResult(
        provider_event_id=event["id"],
        slot=slot,
        meeting_url=meeting_url,
    )


def _http_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status
    return getattr(getattr(exc, "resp", None), "status", None)


def build_scheduling_service() -> SchedulingService:
    provider = os.getenv("SCHEDULING_PROVIDER", "mock").casefold()
    if provider == "mock":
        return DeterministicSchedulingService()
    if provider != "google":
        raise ValueError("SCHEDULING_PROVIDER must be mock or google")
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID")
    if not calendar_id:
        raise ValueError("GOOGLE_CALENDAR_ID is required")
    credentials_info = _load_json_setting(
        json_name="GOOGLE_OAUTH_TOKEN_JSON",
        file_name="GOOGLE_OAUTH_TOKEN_FILE",
        description="Google OAuth token",
    )
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    try:
        credentials = Credentials.from_authorized_user_info(
            credentials_info, scopes=GOOGLE_CALENDAR_SCOPES
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "Google OAuth token is invalid; authorize the account again"
        ) from exc
    client = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    return GoogleCalendarSchedulingService(
        calendar_id=calendar_id,
        client=client,
        blackout_dates=_configured_blackout_dates(),
    )


def _configured_blackout_dates() -> set[date]:
    raw = os.getenv("SCHEDULING_BLACKOUT_DATES", "")
    try:
        return {date.fromisoformat(value.strip()) for value in raw.split(",") if value.strip()}
    except ValueError as exc:
        raise ValueError(
            "SCHEDULING_BLACKOUT_DATES must contain comma-separated YYYY-MM-DD dates"
        ) from exc


def _load_json_setting(
    *, json_name: str, file_name: str, description: str
) -> dict[str, Any]:
    raw_json = os.getenv(json_name)
    file_path = os.getenv(file_name)
    if not raw_json and not file_path:
        raise ValueError(f"{description} is required ({json_name} or {file_name})")
    try:
        if raw_json:
            value = json.loads(raw_json)
        else:
            with open(file_path or "", encoding="utf-8") as source:
                value = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} could not be loaded") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a JSON object")
    return value


def _slot(start: datetime, end: datetime) -> BookingSlot:
    return BookingSlot(id=start.isoformat(), starts_at=start, ends_at=end)


def _google_event_id(operation_id: str) -> str:
    raw = uuid.UUID(operation_id).bytes
    return base64.b32hexencode(raw).decode("ascii").rstrip("=").lower()
