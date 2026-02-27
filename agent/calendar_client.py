import logging
import os
from datetime import datetime
from datetime import datetime as _dt
from datetime import timezone as _tz
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger("voice-agent")

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"
TOKEN_FILE = Path(__file__).parent / "token.json"


def get_calendar_service():
    """Legacy file-based auth — used as fallback when no per-user tokens are available."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.warning(f"Token refresh failed ({e}), re-authenticating...")
                creds = None
        if creds is None:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def get_calendar_service_from_tokens(tokens: dict):
    """Build a Calendar service from per-user OAuth tokens (passed via LiveKit metadata)."""
    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get("GOOGLE_CLIENT_ID"),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
        scopes=SCOPES,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        logger.info("Refreshed per-user Google access token")
    return build("calendar", "v3", credentials=creds)


def list_calendars(service=None) -> list[dict]:
    """Return all calendars the user has visible (selected) in Google Calendar.

    Each entry: { id, name, color, access_role, selected }
    access_role is one of: 'owner', 'writer', 'reader', 'freeBusyReader'
    """
    if service is None:
        service = get_calendar_service()
    result = service.calendarList().list().execute()
    calendars = []
    for entry in result.get("items", []):
        # Only include calendars the user has toggled visible
        if not entry.get("selected", False):
            continue
        calendars.append({
            "id": entry["id"],
            "name": entry.get("summaryOverride") or entry.get("summary", "(untitled)"),
            "color": entry.get("backgroundColor", "#4285f4"),
            "access_role": entry.get("accessRole", "reader"),
            "selected": True,
        })
    return calendars


def create_event(
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
    recurrence: list[str] | None = None,
    timezone: str = "America/New_York",
    calendar_id: str = "primary",
    service=None,
) -> dict:
    if service is None:
        service = get_calendar_service()
    event_body: dict = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_iso, "timeZone": timezone},
        "end": {"dateTime": end_iso, "timeZone": timezone},
    }
    if recurrence:
        event_body["recurrence"] = recurrence
    event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
    logger.info(f"Created event: {event.get('htmlLink')}")
    return event


def list_upcoming_events(
    max_results: int = 10,
    calendar_ids: list[str] | None = None,
    service=None,
) -> list[dict]:
    if service is None:
        service = get_calendar_service()
    now = _dt.now(_tz.utc).isoformat()

    if not calendar_ids:
        calendar_ids = ["primary"]

    all_events: list[dict] = []
    for cal_id in calendar_ids:
        try:
            result = (
                service.events()
                .list(
                    calendarId=cal_id,
                    timeMin=now,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            all_events.extend(result.get("items", []))
        except Exception as e:
            logger.warning(f"Failed to list events from calendar '{cal_id}': {e}")

    # Sort merged results by start time
    all_events.sort(key=lambda ev: ev["start"].get("dateTime", ev["start"].get("date", "")))
    return all_events[:max_results]


def list_month_events(
    year: int,
    month: int,
    timezone: str = "UTC",
    max_results: int = 250,
    calendars: list[dict] | None = None,
    service=None,
) -> list[dict]:
    """Return all events in a given month from all provided calendars.

    If calendars is provided, each entry should have { id, name, color }.
    Events are returned with extra fields: _calendar_id, _calendar_name, _calendar_color.
    """
    tz = ZoneInfo(timezone)
    time_min = datetime(year, month, 1, tzinfo=tz)

    # First day of next month
    if month == 12:
        time_max = datetime(year + 1, 1, 1, tzinfo=tz)
    else:
        time_max = datetime(year, month + 1, 1, tzinfo=tz)

    if service is None:
        service = get_calendar_service()

    # Default to primary if no calendars specified
    if not calendars:
        calendars = [{"id": "primary", "name": "Primary", "color": "#4285f4"}]

    all_events: list[dict] = []
    for cal in calendars:
        cal_id = cal["id"]
        try:
            result = (
                service.events()
                .list(
                    calendarId=cal_id,
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            for ev in result.get("items", []):
                ev["_calendar_id"] = cal_id
                ev["_calendar_name"] = cal["name"]
                ev["_calendar_color"] = cal["color"]
                all_events.append(ev)
        except Exception as e:
            logger.warning(f"Failed to list month events from calendar '{cal_id}': {e}")

    # Sort merged results by start time
    all_events.sort(key=lambda ev: ev["start"].get("dateTime", ev["start"].get("date", "")))
    return all_events
