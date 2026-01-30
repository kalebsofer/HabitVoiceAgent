"""Quick test for calendar integration — no voice/LiveKit needed."""

import calendar_client


def test_list():
    print("=== Listing upcoming events ===")
    events = calendar_client.list_upcoming_events(5)
    if not events:
        print("No upcoming events.")
    for ev in events:
        start = ev["start"].get("dateTime", ev["start"].get("date", ""))
        print(f"  - {ev['summary']} at {start}")


def test_create():
    print("\n=== Creating test event ===")
    event = calendar_client.create_event(
        summary="Test Habit: Morning Meditation",
        start_iso="2026-02-02T08:00:00",
        end_iso="2026-02-02T08:30:00",
        description="Created by HabitVoiceAgent test script",
        recurrence=["RRULE:FREQ=DAILY"],
    )
    print(f"  Created: {event['summary']}")
    print(f"  Link: {event.get('htmlLink', 'N/A')}")
    return event["id"]


def test_delete(event_id: str):
    print(f"\n=== Deleting test event {event_id} ===")
    service = calendar_client.get_calendar_service()
    service.events().delete(calendarId="primary", eventId=event_id).execute()
    print("  Deleted.")


if __name__ == "__main__":
    test_list()
    eid = test_create()
    test_list()
    resp = input("\nDelete test event? [y/N] ").strip().lower()
    if resp == "y":
        test_delete(eid)
