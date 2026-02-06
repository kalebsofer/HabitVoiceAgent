import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, AutoSubscribe, JobContext, WorkerOptions, cli, llm
from livekit.plugins import openai
from livekit.rtc import DataPacket

import calendar_client

load_dotenv()

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("voice-agent")
logger.setLevel(logging.DEBUG)

MEMORY_FILE = Path(__file__).parent / "memory.json"
HABIT_PLAN_FILE = Path(__file__).parent / "habit_plan.json"
DRAFT_SCHEDULE_FILE = Path(__file__).parent / "draft_schedule.json"

RECURRENCE_MAP = {
    "daily": ["RRULE:FREQ=DAILY"],
    "weekly": ["RRULE:FREQ=WEEKLY"],
    "weekdays": ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"],
    "monthly": ["RRULE:FREQ=MONTHLY"],
    "3x_per_week": ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR"],
}

# Map vague time-of-day words to concrete 24h times
TIME_WORD_MAP = {
    "morning": "07:00",
    "early morning": "06:00",
    "late morning": "10:00",
    "afternoon": "13:00",
    "evening": "18:00",
    "night": "20:00",
    "before work": "07:00",
    "after work": "18:00",
    "lunchtime": "12:00",
    "lunch": "12:00",
}

# Days of the week for cadence mapping
WEEKDAY_INDICES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# ── Conversation stages ──────────────────────────────────────────────
STAGE_GREETING = "greeting"
STAGE_DISCOVERY = "discovery"
STAGE_DETAILING = "detailing"
STAGE_CONFIRMATION = "confirmation"
STAGE_SCHEDULING = "scheduling"
STAGE_REVIEW = "review"

STAGE_INSTRUCTIONS = {
    STAGE_DISCOVERY: (
        "## Your Task: Goal Discovery\n"
        "The user has a vague or abstract goal. Guide a focused discussion:\n"
        "- Ask what success looks like and why this matters to them "
        "(tie it to identity: 'I want to become the type of person who...')\n"
        "- Ask about their current routine, preferred times, and constraints\n"
        "- Learn what activities they enjoy or dislike\n"
        "- Suggest small, concrete habits using Atomic Habits principles "
        "(two-minute rule, habit stacking, reduce friction)\n"
        "- Ask 1-2 questions at a time — don't dump everything at once\n"
        "- When a concrete habit emerges, call assess_user_input again with the new details"
    ),
    STAGE_DETAILING: (
        "## Your Task: Fill In Habit Details\n"
        "The user has a habit idea but some details are missing.\n"
        "{missing_fields}\n"
        "Ask about the missing fields naturally in conversation. "
        "For physical or location-dependent activities (gym, swimming, cycling, cooking), "
        "ask if they need prep/wind-down time — e.g. 'Should I add time for getting "
        "changed and showering — maybe 15 minutes either side?' "
        "Do NOT ask about prep for meditation, journaling, reading, etc.\n"
        "Once all fields are clear, call assess_user_input again to advance."
    ),
    STAGE_CONFIRMATION: (
        "## Your Task: Confirm and Save\n"
        "You have all the habit details:\n{habit_summary}\n"
        "Summarize them back to the user in one concise sentence and ask for confirmation. "
        "On confirmation, call save_habit_plan with all the fields.\n"
        "After saving, ask if they want to add another habit or move to scheduling."
    ),
    STAGE_SCHEDULING: (
        "## Your Task: Schedule Habits\n"
        "The user's habits are saved. Tell them you'll check their calendar and build "
        "a schedule. Call fetch_monthly_calendar first, then generate_draft_schedule.\n"
        "After the draft is generated, give a concise voice summary of the proposed "
        "times and tell them the draft is now visible on their screen."
    ),
    STAGE_REVIEW: (
        "## Your Task: Review & Finalize\n"
        "The draft schedule is visible on the user's screen. Ask if they'd like any "
        "changes. Handle corrections with update_draft_item or remove_draft_item. "
        "When they're satisfied, call confirm_draft_schedule to create real Google "
        "Calendar events. After confirmation, encourage them warmly."
    ),
}


def load_memory() -> dict[str, str]:
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text())
    return {}


def save_memory(memory: dict[str, str]) -> None:
    MEMORY_FILE.write_text(json.dumps(memory, indent=2))


def get_local_timezone() -> str:
    """Detect the system's local IANA timezone name (e.g. 'America/New_York')."""
    try:
        from tzlocal import get_localzone
        tz_name = str(get_localzone())
        # Validate that ZoneInfo can handle the name (filters out Windows names
        # like 'GMT Standard Time' that aren't valid IANA keys).
        ZoneInfo(tz_name)
        return tz_name
    except Exception:
        pass

    # Fallback: use the UTC offset to pick a fixed-offset IANA name
    try:
        local_now = datetime.now(timezone.utc).astimezone()
        offset = local_now.utcoffset()
        if offset is not None:
            total_seconds = int(offset.total_seconds())
            sign = "+" if total_seconds >= 0 else "-"
            hours, remainder = divmod(abs(total_seconds), 3600)
            minutes = remainder // 60
            return f"Etc/GMT{'+' if total_seconds <= 0 else '-'}{hours}" if minutes == 0 else "UTC"
    except Exception:
        pass

    return "UTC"


def _parse_preferred_time(preferred_time: str) -> tuple[str, str | None]:
    """Convert a preferred_time string to 'HH:MM'.

    Handles: '07:00', '7:30', 'morning', '3pm', '3:30 PM', '15:00', '3 pm'.
    Returns (time_str, warning_or_none). Warning is set if we fell back to default.
    """
    cleaned = preferred_time.strip().lower()

    # 1. Named time-of-day words
    if cleaned in TIME_WORD_MAP:
        return TIME_WORD_MAP[cleaned], None

    # 2. 12-hour format with am/pm: "3pm", "3:30pm", "3:30 pm", "11 am"
    m12 = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$', cleaned)
    if m12:
        hour = int(m12.group(1))
        minute = int(m12.group(2) or 0)
        period = m12.group(3)
        if period == "pm" and hour != 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}", None

    # 3. 24-hour format: "07:00", "7:30", "15:00", "7"
    m24 = re.match(r'^(\d{1,2})(?::(\d{2}))?$', cleaned)
    if m24:
        hour = int(m24.group(1))
        minute = int(m24.group(2) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}", None

    # 4. Fallback — return default with a warning
    return "08:00", f"Could not parse time '{preferred_time}', defaulting to 08:00"


def _events_overlap(start1: datetime, end1: datetime, start2: datetime, end2: datetime) -> bool:
    """Check if two time ranges overlap."""
    return start1 < end2 and start2 < end1


async def _push_status(agent: "VoiceAgent", message: str) -> None:
    """Send a status update to the frontend for display."""
    room = agent._room
    if room:
        payload = json.dumps({"type": "status", "message": message}).encode("utf-8")
        await room.local_participant.publish_data(payload=payload, topic="agent_status")
        logger.info(f"[STATUS] {message}")


async def _push_draft_to_frontend(agent: "VoiceAgent", draft: dict) -> None:
    """Persist draft to disk and publish to frontend via data channel."""
    DRAFT_SCHEDULE_FILE.write_text(json.dumps(draft, indent=2))

    payload = json.dumps(draft).encode("utf-8")
    room = agent._room
    if room:
        await room.local_participant.publish_data(
            payload=payload,
            topic="draft_schedule",
        )
        logger.info("[DRAFT] Pushed draft schedule to frontend")
    else:
        logger.warning("[DRAFT] No room available to publish draft")


class VoiceAgent(Agent):
    _extra_context: str = ""
    _draft: dict | None = None
    _room: object | None = None
    _stage: str = STAGE_GREETING
    _calendars: list[dict] | None = None  # cached calendar list
    _target_calendar: str = "primary"     # calendar ID to create events on

    def __init__(self, user_tz: str = ""):
        self.user_tz = user_tz or get_local_timezone()
        now = datetime.now(ZoneInfo(self.user_tz))
        time_context = (
            f"## Current Context\n"
            f"The user's timezone is {self.user_tz}. "
            f"Right now it is {now.strftime('%A, %B %d, %Y at %I:%M %p')} in their timezone. "
            f"Use this when discussing scheduling — suggest times relative to today and "
            f"use the correct dates for 'tomorrow', 'next Monday', etc.\n\n"
        )

        super().__init__(
            instructions=(
                "You are the Habit Advisor — a sophisticated life coach and habit-formation expert. "
                "You help people build consistent, achievable habits using principles from "
                "James Clear's Atomic Habits (habit stacking, two-minute rule, environment design, "
                "identity-based habits).\n\n"

                + time_context +

                "## Conversation Pipeline\n"
                "You guide users through these stages:\n"
                "1. DISCOVERY — understand vague goals, suggest concrete habits\n"
                "2. DETAILING — fill in specifics (cadence, time, duration, cue)\n"
                "3. CONFIRMATION — confirm details, save each habit via save_habit_plan\n"
                "4. SCHEDULING — check calendar, generate conflict-free draft\n"
                "5. REVIEW — user adjusts draft on screen, then confirm to create real events\n\n"

                "## Critical Rule: Classify Before Responding\n"
                "When the user first describes their goals or habits, you MUST call "
                "assess_user_input BEFORE responding. This tool analyzes how much detail "
                "the user provided and returns the correct stage with instructions.\n\n"
                "A user who says 'I want to meditate daily at 8:30am for 20 minutes' has "
                "already given you name, cadence, time, and duration — do NOT ask exploratory "
                "questions. A user who says 'I want to be healthier' needs discovery.\n\n"
                "Always follow the instructions returned by assess_user_input and other tools. "
                "They contain your stage-specific guidance.\n\n"

                "## Saving Habits\n"
                "When saving a habit via save_habit_plan, include all fields:\n"
                "name, goal, cadence (daily/weekdays/weekly/3x_per_week/monthly), "
                "preferred_time ('07:00' or 'morning'), duration_minutes (int, include "
                "prep/wind-down if agreed), cue, two_minute_version.\n\n"

                "## Memory\n"
                "Use save_note to remember important things about the user "
                "(name, goals, preferences, constraints) and recall them in future sessions.\n\n"

                "## Style\n"
                "Keep responses concise — this is a voice conversation. Be warm and "
                "knowledgeable but not preachy. Speak like a trusted coach. Always speak in English."
            ),
        )

    # --- Memory tools ---

    @llm.function_tool(description="Save a note to memory with a key and value. Use this to remember important things about the user.")
    async def save_note(self, key: str, value: str) -> str:
        memory = load_memory()
        memory[key] = value
        save_memory(memory)
        logger.info(f"Saved memory: {key} = {value}")
        return f"Saved '{key}' to memory."

    @llm.function_tool(description="Recall a note from memory by key. Returns the value if found.")
    async def recall_note(self, key: str) -> str:
        memory = load_memory()
        if key in memory:
            return f"{key}: {memory[key]}"
        return f"No memory found for '{key}'."

    @llm.function_tool(description="List all saved memory keys and values.")
    async def list_memories(self) -> str:
        memory = load_memory()
        if not memory:
            return "No memories saved yet."
        return "\n".join(f"{k}: {v}" for k, v in memory.items())

    # --- Calendar management tools ---

    async def _get_calendars(self) -> list[dict]:
        """Get visible calendars, using session cache to avoid repeated API calls."""
        if self._calendars is None:
            self._calendars = calendar_client.list_calendars()
            logger.info(f"[CALENDARS] Fetched {len(self._calendars)} visible calendar(s)")
        return self._calendars

    @llm.function_tool(
        description=(
            "List all of the user's visible Google Calendars (work, personal, family, etc.). "
            "Returns each calendar's name, color, and whether the user can create events on it."
        )
    )
    async def list_calendars(self) -> str:
        try:
            calendars = await self._get_calendars()
            if not calendars:
                return "No calendars found."
            lines = []
            for cal in calendars:
                writable = cal["access_role"] in ("owner", "writer")
                role_label = "writable" if writable else "read-only"
                target = " (currently selected for new habits)" if cal["id"] == self._target_calendar else ""
                lines.append(
                    f"- {cal['name']} [{role_label}] (color: {cal['color']}){target}"
                )
            return f"Your calendars:\n" + "\n".join(lines)
        except Exception as e:
            logger.error(f"Failed to list calendars: {e}")
            return f"Sorry, I couldn't retrieve your calendars: {e}"

    @llm.function_tool(
        description=(
            "Set which calendar new habit events will be created on. "
            "Takes the calendar name (e.g. 'Work', 'Personal'). "
            "Only use this if the user explicitly asks to create habits on a specific calendar."
        )
    )
    async def set_target_calendar(self, calendar_name: str) -> str:
        try:
            calendars = await self._get_calendars()
            needle = calendar_name.strip().lower()
            match = None
            for cal in calendars:
                if needle in cal["name"].lower():
                    match = cal
                    break
            if not match:
                names = ", ".join(cal["name"] for cal in calendars)
                return f"No calendar matching '{calendar_name}'. Available calendars: {names}"
            if match["access_role"] not in ("owner", "writer"):
                return f"'{match['name']}' is read-only — you can't create events on it."
            self._target_calendar = match["id"]
            logger.info(f"[CALENDARS] Target calendar set to '{match['name']}' ({match['id']})")
            return f"New habits will now be created on '{match['name']}'."
        except Exception as e:
            logger.error(f"Failed to set target calendar: {e}")
            return f"Sorry, I couldn't set the target calendar: {e}"

    # --- Stage classification tool ---

    @llm.function_tool(
        description=(
            "IMPORTANT: Call this tool FIRST when the user describes their goals or habits. "
            "Analyzes how specific the user's input is and returns the correct conversation "
            "stage with instructions for what to do next.\n"
            "Extract these from the user's message:\n"
            "- user_message_summary: brief summary of what they said\n"
            "- has_habit_name: they named a specific activity (e.g. 'meditate', 'run', 'journal')\n"
            "- has_cadence: they specified frequency (e.g. 'daily', '3x/week', 'every morning')\n"
            "- has_preferred_time: they specified a time (e.g. '8:30am', 'morning', 'after work')\n"
            "- has_duration: they specified duration (e.g. '20 minutes', 'half hour')\n"
            "- has_goal: they mentioned an overarching goal (e.g. 'reduce stress', 'get fit')\n"
            "- wants_scheduling: they asked to schedule existing habits or said they're done adding habits"
        )
    )
    async def assess_user_input(
        self,
        user_message_summary: str,
        has_habit_name: bool = False,
        has_cadence: bool = False,
        has_preferred_time: bool = False,
        has_duration: bool = False,
        has_goal: bool = False,
        wants_scheduling: bool = False,
    ) -> str:
        # Check for existing saved habits
        existing_habits = []
        if HABIT_PLAN_FILE.exists():
            try:
                existing_habits = json.loads(HABIT_PLAN_FILE.read_text())
            except Exception:
                existing_habits = []

        existing_summary = ""
        if existing_habits:
            lines = [f"  - {h['name']} ({h['cadence']} at {h['preferred_time']})" for h in existing_habits]
            existing_summary = f"Previously saved habits:\n" + "\n".join(lines) + "\n"

        # Check for existing draft
        if self._draft and self._draft.get("status") == "draft":
            self._stage = STAGE_REVIEW
            logger.info(f"[STAGE] → {self._stage} (existing draft found)")
            return (
                f"Stage: REVIEW\n{existing_summary}"
                f"A draft schedule already exists and is visible to the user.\n\n"
                f"{STAGE_INSTRUCTIONS[STAGE_REVIEW]}"
            )

        # If user wants scheduling and habits exist
        if wants_scheduling and existing_habits:
            self._stage = STAGE_SCHEDULING
            logger.info(f"[STAGE] → {self._stage}")
            return (
                f"Stage: SCHEDULING\n{existing_summary}\n"
                f"{STAGE_INSTRUCTIONS[STAGE_SCHEDULING]}"
            )

        # All four key fields present → jump straight to confirmation
        if has_habit_name and has_cadence and has_preferred_time and has_duration:
            self._stage = STAGE_CONFIRMATION
            logger.info(f"[STAGE] → {self._stage} (all details provided)")
            habit_summary = (
                f"  Name: from user input\n"
                f"  Cadence: provided\n"
                f"  Time: provided\n"
                f"  Duration: provided\n"
                f"  Goal: {'provided' if has_goal else 'ask briefly or infer from context'}"
            )
            instructions = STAGE_INSTRUCTIONS[STAGE_CONFIRMATION].format(
                habit_summary=habit_summary
            )
            return (
                f"Stage: CONFIRMATION — the user gave you ALL the details.\n"
                f"{existing_summary}"
                f"Summarize what they said back to them and ask to confirm. "
                f"Then call save_habit_plan immediately.\n\n"
                f"{instructions}"
            )

        # Has a habit name or enough specifics → detailing
        if has_habit_name:
            self._stage = STAGE_DETAILING
            missing = []
            if not has_cadence:
                missing.append("cadence/frequency (daily, weekdays, 3x/week, etc.)")
            if not has_preferred_time:
                missing.append("preferred time of day")
            if not has_duration:
                missing.append("duration in minutes")
            if not has_goal:
                missing.append("overarching goal this habit serves")
            missing_str = "Still needed: " + ", ".join(missing) if missing else ""
            instructions = STAGE_INSTRUCTIONS[STAGE_DETAILING].format(
                missing_fields=missing_str
            )
            logger.info(f"[STAGE] → {self._stage} (missing: {missing})")
            return (
                f"Stage: DETAILING — the user named a habit but details are incomplete.\n"
                f"{existing_summary}{instructions}"
            )

        # Vague goal or general statement → discovery
        self._stage = STAGE_DISCOVERY
        logger.info(f"[STAGE] → {self._stage}")
        return (
            f"Stage: DISCOVERY — the user has a general goal, not a specific habit yet.\n"
            f"{existing_summary}"
            f"{STAGE_INSTRUCTIONS[STAGE_DISCOVERY]}"
        )

    # --- Habit plan tools ---

    @llm.function_tool(
        description=(
            "Save a habit to the habit plan. Call this once per habit after the user confirms it. "
            "Fields: name (str), goal (str), cadence (str: daily/weekdays/weekly/3x_per_week/monthly), "
            "preferred_time (str: e.g. '07:00' or 'morning'), duration_minutes (int), "
            "cue (str: what triggers the habit), two_minute_version (str: the minimal starter version)."
        )
    )
    async def save_habit_plan(
        self,
        name: str,
        goal: str,
        cadence: str,
        preferred_time: str,
        duration_minutes: int,
        cue: str = "",
        two_minute_version: str = "",
    ) -> str:
        try:
            # Validate cadence
            VALID_CADENCES = set(RECURRENCE_MAP.keys())
            normalized_cadence = cadence.lower().strip().replace(" ", "_")
            if normalized_cadence not in VALID_CADENCES:
                valid_list = ", ".join(sorted(VALID_CADENCES))
                return (
                    f"Invalid cadence '{cadence}'. "
                    f"Valid options are: {valid_list}. "
                    f"Please ask the user to clarify and call save_habit_plan again."
                )

            # Validate preferred_time parses correctly
            parsed_time, time_warn = _parse_preferred_time(preferred_time)
            if time_warn:
                logger.warning(f"[SAVE HABIT] {time_warn}")

            habits = []
            if HABIT_PLAN_FILE.exists():
                habits = json.loads(HABIT_PLAN_FILE.read_text())

            habit = {
                "name": name,
                "goal": goal,
                "cadence": normalized_cadence,
                "preferred_time": preferred_time,
                "duration_minutes": duration_minutes,
                "cue": cue,
                "two_minute_version": two_minute_version,
            }
            habits.append(habit)
            HABIT_PLAN_FILE.write_text(json.dumps(habits, indent=2))
            self._stage = STAGE_SCHEDULING
            logger.info(f"Saved habit plan: {name} — [STAGE] → {self._stage}")
            return (
                f"Habit '{name}' saved to plan ({cadence} at {preferred_time}, {duration_minutes} min).\n\n"
                f"--- NEXT STEP ---\n"
                f"Ask the user: would they like to add another habit, or are they ready "
                f"to schedule? If they want to schedule (or have no more habits), call "
                f"fetch_monthly_calendar followed by generate_draft_schedule.\n"
                f"{STAGE_INSTRUCTIONS[STAGE_SCHEDULING]}"
            )
        except Exception as e:
            logger.error(f"Failed to save habit plan: {e}")
            return f"Sorry, I couldn't save that habit: {e}"

    @llm.function_tool(description="List all habits currently in the habit plan.")
    async def list_habit_plan(self) -> str:
        try:
            if not HABIT_PLAN_FILE.exists():
                return "No habits in the plan yet."
            habits = json.loads(HABIT_PLAN_FILE.read_text())
            if not habits:
                return "No habits in the plan yet."
            lines = []
            for h in habits:
                lines.append(
                    f"- {h['name']}: {h['cadence']} at {h['preferred_time']}, "
                    f"{h['duration_minutes']} min (goal: {h['goal']})"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Failed to list habit plan: {e}")
            return f"Sorry, I couldn't retrieve the habit plan: {e}"

    # --- Calendar tools ---

    @llm.function_tool(
        description=(
            "Create a Google Calendar event. "
            "date should be like '2025-03-15'. start_time like '09:00'. "
            "duration_minutes is an integer. "
            "recurrence is optional: 'daily', 'weekly', 'weekdays', 'monthly', or empty for one-time."
        )
    )
    async def create_calendar_event(
        self,
        summary: str,
        date: str,
        start_time: str,
        duration_minutes: int = 30,
        description: str = "",
        recurrence: str = "",
    ) -> str:
        try:
            start_dt = datetime.fromisoformat(f"{date}T{start_time}:00")
            end_dt = start_dt + timedelta(minutes=duration_minutes)
            start_iso = start_dt.isoformat()
            end_iso = end_dt.isoformat()

            rrule = RECURRENCE_MAP.get(recurrence.lower().strip()) if recurrence else None

            event = calendar_client.create_event(
                summary=summary,
                start_iso=start_iso,
                end_iso=end_iso,
                description=description,
                recurrence=rrule,
                timezone=self.user_tz,
            )
            link = event.get("htmlLink", "")
            recurrence_text = f" ({recurrence})" if recurrence else ""
            return (
                f"Event created: {summary} on {date} at {start_time} "
                f"for {duration_minutes} minutes{recurrence_text}. Link: {link}"
            )
        except Exception as e:
            logger.error(f"Failed to create event: {e}")
            return f"Sorry, I couldn't create that event: {e}"

    @llm.function_tool(description="List upcoming Google Calendar events.")
    async def list_calendar_events(self) -> str:
        try:
            calendars = await self._get_calendars()
            cal_ids = [c["id"] for c in calendars]
            events = calendar_client.list_upcoming_events(
                max_results=10, calendar_ids=cal_ids,
            )
            if not events:
                return "No upcoming events found."
            lines = []
            for ev in events:
                start = ev["start"].get("dateTime", ev["start"].get("date", ""))
                lines.append(f"- {ev['summary']} at {start}")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Failed to list events: {e}")
            return f"Sorry, I couldn't retrieve events: {e}"

    # --- Draft scheduling tools ---

    @llm.function_tool(
        description=(
            "Fetch the user's Google Calendar events for the current month. "
            "Returns a text summary of busy times so you can reason about scheduling."
        )
    )
    async def fetch_monthly_calendar(self) -> str:
        try:
            await _push_status(self, "Fetching your calendars...")
            now = datetime.now(ZoneInfo(self.user_tz))
            calendars = await self._get_calendars()
            events = calendar_client.list_month_events(
                year=now.year, month=now.month, timezone=self.user_tz,
                calendars=calendars,
            )
            cal_count = len(calendars)
            await _push_status(self, f"Found {len(events)} event(s) across {cal_count} calendar(s)")
            if not events:
                return f"No events found for {now.strftime('%B %Y')}. The calendar is wide open!"

            lines = [f"Busy times for {now.strftime('%B %Y')} (across {cal_count} calendar(s)):"]
            for ev in events:
                start = ev["start"].get("dateTime", ev["start"].get("date", ""))
                end = ev["end"].get("dateTime", ev["end"].get("date", ""))
                summary = ev.get("summary", "(no title)")
                cal_name = ev.get("_calendar_name", "")
                cal_label = f" [{cal_name}]" if cal_name else ""
                lines.append(f"- {summary}{cal_label}: {start} to {end}")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Failed to fetch monthly calendar: {e}")
            return f"Sorry, I couldn't fetch the calendar: {e}"

    @llm.function_tool(
        description=(
            "Generate a conflict-free draft schedule by reading the habit plan and the user's "
            "calendar. Places each habit at its preferred time, avoiding conflicts. "
            "The draft is pushed to the frontend for visual review. Takes no parameters."
        )
    )
    async def generate_draft_schedule(self) -> str:
        try:
            # Load habit plan
            if not HABIT_PLAN_FILE.exists():
                return "No habit plan found. Please create habits first."
            habits = json.loads(HABIT_PLAN_FILE.read_text())
            if not habits:
                return "Habit plan is empty. Please add some habits first."

            await _push_status(self, f"Loaded {len(habits)} habit(s) from plan")

            now = datetime.now(ZoneInfo(self.user_tz))

            # Fetch existing calendar events for the month (all visible calendars)
            await _push_status(self, "Fetching your calendars...")
            calendars = await self._get_calendars()
            existing_events = calendar_client.list_month_events(
                year=now.year, month=now.month, timezone=self.user_tz,
                calendars=calendars,
            )

            # Build list of existing busy slots — normalize everything to user TZ
            user_tz = ZoneInfo(self.user_tz)
            busy_slots: list[tuple[datetime, datetime]] = []
            existing_items: list[dict] = []

            for ev in existing_events:
                start_str = ev["start"].get("dateTime", "")
                end_str = ev["end"].get("dateTime", "")
                is_all_day = not start_str or not end_str

                # All-day events: show on calendar but don't block habit placement
                if is_all_day:
                    all_day_date = ev["start"].get("date", "")
                    existing_items.append({
                        "id": ev.get("id", str(uuid.uuid4())),
                        "type": "existing",
                        "summary": ev.get("summary", "(no title)"),
                        "start": all_day_date,
                        "end": ev["end"].get("date", all_day_date),
                        "all_day": True,
                        "recurrence": None,
                        "habit_name": None,
                        "calendar_name": ev.get("_calendar_name", ""),
                        "calendar_color": ev.get("_calendar_color", ""),
                    })
                    continue

                # Timed events: add to busy slots for conflict detection
                start_dt = datetime.fromisoformat(start_str).astimezone(user_tz)
                end_dt = datetime.fromisoformat(end_str).astimezone(user_tz)
                busy_slots.append((start_dt, end_dt))
                existing_items.append({
                    "id": ev.get("id", str(uuid.uuid4())),
                    "type": "existing",
                    "summary": ev.get("summary", "(no title)"),
                    "start": start_str,
                    "end": end_str,
                    "all_day": False,
                    "recurrence": None,
                    "habit_name": None,
                    "calendar_name": ev.get("_calendar_name", ""),
                    "calendar_color": ev.get("_calendar_color", ""),
                })

            # Place each habit
            await _push_status(self, "Finding conflict-free time slots...")
            draft_items: list[dict] = []
            skipped_habits: list[str] = []
            draft_counter = 0
            tomorrow = (now + timedelta(days=1)).date()

            time_warnings: list[str] = []
            for habit in habits:
                preferred, time_warn = _parse_preferred_time(habit["preferred_time"])
                if time_warn:
                    time_warnings.append(f"{habit['name']}: {time_warn}")
                    logger.warning(f"[TIME PARSE] {time_warn}")
                h, m = map(int, preferred.split(":"))
                duration = habit.get("duration_minutes", 30)
                cadence = habit.get("cadence", "daily").lower().strip()

                # Determine the first date for this habit
                if cadence in ("daily", "weekdays"):
                    start_date = tomorrow
                    if cadence == "weekdays" and start_date.weekday() >= 5:
                        # Jump to next Monday
                        days_ahead = 7 - start_date.weekday()
                        start_date = start_date + timedelta(days=days_ahead)
                elif cadence == "weekly":
                    start_date = tomorrow
                elif cadence == "3x_per_week":
                    start_date = tomorrow
                    # Find next Mon, Wed, or Fri
                    target_days = {0, 2, 4}  # Mon, Wed, Fri
                    while start_date.weekday() not in target_days:
                        start_date += timedelta(days=1)
                elif cadence == "monthly":
                    start_date = tomorrow
                else:
                    # Validated cadences should never hit this, but handle gracefully
                    logger.warning(f"[CADENCE] Unknown cadence '{cadence}' for '{habit['name']}', using tomorrow")
                    start_date = tomorrow

                # Build the proposed start/end
                proposed_start = datetime(
                    start_date.year, start_date.month, start_date.day,
                    h, m, tzinfo=ZoneInfo(self.user_tz)
                )
                proposed_end = proposed_start + timedelta(minutes=duration)

                # Conflict resolution: shift by 30-min increments
                max_shifts = 48  # up to 24 hours of shifting
                found_slot = False
                for _ in range(max_shifts):
                    conflict = False
                    # Check against existing calendar events (already in user TZ)
                    for busy_start, busy_end in busy_slots:
                        if _events_overlap(proposed_start, proposed_end, busy_start, busy_end):
                            conflict = True
                            break
                    # Also check against already-placed draft items
                    if not conflict:
                        for placed in draft_items:
                            ps = datetime.fromisoformat(placed["start"]).astimezone(user_tz)
                            pe = datetime.fromisoformat(placed["end"]).astimezone(user_tz)
                            if _events_overlap(proposed_start, proposed_end, ps, pe):
                                conflict = True
                                break
                    if not conflict:
                        found_slot = True
                        break
                    proposed_start += timedelta(minutes=30)
                    proposed_end = proposed_start + timedelta(minutes=duration)

                if not found_slot:
                    # Do NOT place — report to agent so it can ask the user
                    skipped_habits.append(habit["name"])
                    logger.warning(
                        f"[CONFLICT] No conflict-free slot for '{habit['name']}' "
                        f"after {max_shifts} shifts — skipped"
                    )
                    continue

                draft_counter += 1
                draft_items.append({
                    "id": f"draft_{draft_counter:03d}",
                    "type": "draft",
                    "summary": habit["name"],
                    "start": proposed_start.isoformat(),
                    "end": proposed_end.isoformat(),
                    "recurrence": cadence,
                    "habit_name": habit["name"],
                    "description": (
                        f"Goal: {habit.get('goal', '')}\n"
                        f"Cue: {habit.get('cue', '')}\n"
                        f"2-min version: {habit.get('two_minute_version', '')}"
                    ),
                })

            # Build the full draft
            draft = {
                "timezone": self.user_tz,
                "month": now.strftime("%Y-%m"),
                "generated_at": now.isoformat(),
                "status": "draft",
                "items": existing_items + draft_items,
            }

            self._draft = draft
            await _push_status(self, f"Draft ready — {len(draft_items)} habit(s) scheduled")
            await _push_draft_to_frontend(self, draft)

            # Build voice summary
            summary_lines = ["Draft schedule created:"]
            for item in draft_items:
                dt = datetime.fromisoformat(item["start"])
                time_str = dt.strftime("%I:%M %p").lstrip("0")
                day_str = dt.strftime("%A, %B %d")
                rec = f" ({item['recurrence']})" if item.get("recurrence") else ""
                summary_lines.append(
                    f"- {item['summary']} at {time_str} starting {day_str}{rec}"
                )
            if skipped_habits:
                summary_lines.append(
                    f"\nCOULD NOT SCHEDULE ({len(skipped_habits)}):"
                )
                for name in skipped_habits:
                    summary_lines.append(f"  - {name} — no conflict-free slot found")
                summary_lines.append(
                    "Tell the user which habits couldn't be scheduled and ask if they'd "
                    "like to pick a specific time (even if it overlaps), change the duration, "
                    "or skip them."
                )
            if time_warnings:
                summary_lines.append("\nTime parse warnings:")
                summary_lines.extend(f"  - {w}" for w in time_warnings)

            summary_lines.append(
                f"\nTotal: {len(draft_items)} habit(s) scheduled, "
                f"{len(skipped_habits)} skipped, "
                f"{len(existing_items)} existing event(s) shown."
            )

            self._stage = STAGE_REVIEW
            logger.info(f"[STAGE] → {self._stage}")
            summary_lines.append(
                f"\n--- NEXT STEP ---\n"
                f"Give the user a concise voice summary of the proposed times above, "
                f"then tell them the draft is visible on their screen.\n"
                f"{STAGE_INSTRUCTIONS[STAGE_REVIEW]}"
            )
            return "\n".join(summary_lines)

        except Exception as e:
            logger.error(f"Failed to generate draft schedule: {e}", exc_info=True)
            return f"Sorry, I couldn't generate the draft schedule: {e}"

    def _find_draft_item(self, item_id: str) -> dict | None:
        """Find a draft item by exact ID or by name (case-insensitive substring)."""
        if not self._draft:
            return None
        needle = item_id.strip().lower()
        # Exact ID match first
        for item in self._draft["items"]:
            if item["id"] == item_id and item["type"] == "draft":
                return item
        # Fuzzy name match
        for item in self._draft["items"]:
            if item["type"] == "draft" and needle in item["summary"].lower():
                return item
        return None

    def _list_draft_names(self) -> str:
        """Return a short listing of current draft items for error messages."""
        if not self._draft:
            return ""
        names = [
            f"  - '{item['summary']}' (id: {item['id']})"
            for item in self._draft["items"] if item["type"] == "draft"
        ]
        return "Current draft items:\n" + "\n".join(names) if names else "No draft items."

    @llm.function_tool(
        description=(
            "Force-place a habit onto the draft schedule at a specific time, even if it "
            "overlaps with an existing event. ONLY use this when the user has explicitly "
            "said they're okay with the clash. Parameters: habit_name (str), date (YYYY-MM-DD), "
            "start_time (HH:MM), duration_minutes (int)."
        )
    )
    async def force_place_draft_item(
        self,
        habit_name: str,
        date: str,
        start_time: str,
        duration_minutes: int,
    ) -> str:
        try:
            if not self._draft:
                return "No draft schedule exists. Generate one first."

            # Load the habit from the plan to get metadata
            habits = []
            if HABIT_PLAN_FILE.exists():
                habits = json.loads(HABIT_PLAN_FILE.read_text())
            habit = next((h for h in habits if h["name"].lower() == habit_name.lower()), None)

            user_tz = ZoneInfo(self.user_tz)
            y, mo, d = map(int, date.split("-"))
            h, m = map(int, start_time.split(":"))
            start_dt = datetime(y, mo, d, h, m, tzinfo=user_tz)
            end_dt = start_dt + timedelta(minutes=duration_minutes)

            # Find next available draft ID
            existing_ids = [
                int(item["id"].split("_")[1])
                for item in self._draft["items"]
                if item["type"] == "draft" and item["id"].startswith("draft_")
            ]
            next_id = max(existing_ids, default=0) + 1

            self._draft["items"].append({
                "id": f"draft_{next_id:03d}",
                "type": "draft",
                "summary": habit_name,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "recurrence": habit.get("cadence", "") if habit else "",
                "habit_name": habit_name,
                "description": (
                    f"Goal: {habit.get('goal', '')}\n"
                    f"Cue: {habit.get('cue', '')}\n"
                    f"2-min version: {habit.get('two_minute_version', '')}"
                ) if habit else "",
            })

            await _push_draft_to_frontend(self, self._draft)

            time_str = start_dt.strftime("%I:%M %p").lstrip("0")
            date_str = start_dt.strftime("%A, %B %d")
            return (
                f"Force-placed '{habit_name}' at {time_str} on {date_str} "
                f"({duration_minutes} min). Note: this may overlap with existing events."
            )
        except Exception as e:
            logger.error(f"Failed to force-place draft item: {e}")
            return f"Sorry, I couldn't place that habit: {e}"

    @llm.function_tool(
        description=(
            "Update a single draft item in the schedule. item_id can be the exact id "
            "(e.g. 'draft_001') or the habit name (e.g. 'Morning Meditation'). "
            "Optional fields: new_date (YYYY-MM-DD), new_start_time (HH:MM), new_duration_minutes (int). "
            "The updated draft is pushed to the frontend."
        )
    )
    async def update_draft_item(
        self,
        item_id: str,
        new_date: str = "",
        new_start_time: str = "",
        new_duration_minutes: int = 0,
    ) -> str:
        try:
            if not self._draft:
                return "No draft schedule exists. Generate one first."

            target = self._find_draft_item(item_id)

            if not target:
                return f"No draft item matching '{item_id}'. {self._list_draft_names()}"

            # Parse current start/end — ensure timezone is preserved
            user_tz = ZoneInfo(self.user_tz)
            current_start = datetime.fromisoformat(target["start"])
            if current_start.tzinfo is None:
                current_start = current_start.replace(tzinfo=user_tz)
            else:
                current_start = current_start.astimezone(user_tz)

            current_end = datetime.fromisoformat(target["end"])
            if current_end.tzinfo is None:
                current_end = current_end.replace(tzinfo=user_tz)
            else:
                current_end = current_end.astimezone(user_tz)

            current_duration = round((current_end - current_start).total_seconds() / 60)

            # Apply changes — rebuild datetime in user TZ to preserve offset
            if new_date:
                y, mo, d = map(int, new_date.split("-"))
                current_start = current_start.replace(year=y, month=mo, day=d, tzinfo=user_tz)
            if new_start_time:
                h, m = map(int, new_start_time.split(":"))
                current_start = current_start.replace(hour=h, minute=m, second=0, microsecond=0, tzinfo=user_tz)
            duration = new_duration_minutes if new_duration_minutes > 0 else current_duration
            current_end = current_start + timedelta(minutes=duration)

            target["start"] = current_start.isoformat()
            target["end"] = current_end.isoformat()

            await _push_draft_to_frontend(self, self._draft)

            time_str = current_start.strftime("%I:%M %p").lstrip("0")
            date_str = current_start.strftime("%A, %B %d")
            return f"Updated '{target['summary']}' to {time_str} on {date_str} ({duration} min)."
        except Exception as e:
            logger.error(f"Failed to update draft item: {e}")
            return f"Sorry, I couldn't update that item: {e}"

    @llm.function_tool(
        description=(
            "Remove a draft item from the schedule. item_id can be the exact id "
            "(e.g. 'draft_001') or the habit name (e.g. 'Morning Run'). "
            "The updated draft is pushed to the frontend."
        )
    )
    async def remove_draft_item(self, item_id: str) -> str:
        try:
            if not self._draft:
                return "No draft schedule exists. Generate one first."

            target = self._find_draft_item(item_id)
            if not target:
                return f"No draft item matching '{item_id}'. {self._list_draft_names()}"

            self._draft["items"] = [
                item for item in self._draft["items"]
                if item["id"] != target["id"]
            ]

            await _push_draft_to_frontend(self, self._draft)
            return f"Removed '{target['summary']}' from the draft schedule."
        except Exception as e:
            logger.error(f"Failed to remove draft item: {e}")
            return f"Sorry, I couldn't remove that item: {e}"

    @llm.function_tool(
        description=(
            "Confirm the draft schedule and create real Google Calendar events for all draft items. "
            "Sets the draft status to 'confirmed' and pushes the final state to the frontend."
        )
    )
    async def confirm_draft_schedule(self) -> str:
        try:
            if not self._draft:
                return "No draft schedule exists. Generate one first."

            if self._draft["status"] == "confirmed":
                return "The schedule has already been confirmed."

            draft_count = sum(1 for i in self._draft["items"] if i["type"] == "draft")
            await _push_status(self, f"Creating {draft_count} calendar event(s)...")

            created_count = 0
            errors = []

            for item in self._draft["items"]:
                if item["type"] != "draft":
                    continue

                try:
                    await _push_status(self, f"Creating: {item['summary']}...")
                    cadence = item.get("recurrence", "")
                    rrule = RECURRENCE_MAP.get(cadence) if cadence else None

                    calendar_client.create_event(
                        summary=item["summary"],
                        start_iso=item["start"],
                        end_iso=item["end"],
                        description=item.get("description", ""),
                        recurrence=rrule,
                        timezone=self.user_tz,
                        calendar_id=self._target_calendar,
                    )
                    created_count += 1
                except Exception as e:
                    errors.append(f"{item['summary']}: {e}")
                    logger.error(f"Failed to create event for {item['summary']}: {e}")

            self._draft["status"] = "confirmed"
            await _push_status(self, "Schedule confirmed!")
            await _push_draft_to_frontend(self, self._draft)

            if errors:
                error_text = "; ".join(errors)
                return (
                    f"Created {created_count} event(s) on Google Calendar, "
                    f"but {len(errors)} failed: {error_text}"
                )

            return (
                f"All {created_count} habit(s) have been added to your Google Calendar! "
                "Your schedule is confirmed.\n\n"
                "--- COMPLETE ---\n"
                "Say something warm and encouraging. Their habits are now on their calendar "
                "and they're set to start building these habits!"
            )
        except Exception as e:
            logger.error(f"Failed to confirm draft schedule: {e}")
            return f"Sorry, I couldn't confirm the schedule: {e}"


async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    participant = await ctx.wait_for_participant()
    logger.info(f"Participant joined: {participant.identity}")

    model = openai.realtime.RealtimeModel(
        voice="shimmer",
        modalities=["audio", "text"],
    )

    session = AgentSession(llm=model)

    @session.on("user_input_transcribed")
    def on_user_transcript(ev):
        logger.info(f"[USER TRANSCRIPT] {ev}")

    @session.on("agent_speech_started")
    def on_agent_speech_started():
        logger.info("[AGENT] Speech started")

    @session.on("agent_speech_stopped")
    def on_agent_speech_stopped():
        logger.info("[AGENT] Speech stopped")

    @session.on("user_started_speaking")
    def on_user_started():
        logger.info("[USER] Started speaking")

    @session.on("user_stopped_speaking")
    def on_user_stopped():
        logger.info("[USER] Stopped speaking")

    @session.on("conversation_item_added")
    def on_item_added(ev):
        logger.info(f"[CONVERSATION] Item added: {ev}")

    @session.on("error")
    def on_error(ev):
        logger.error(f"[SESSION ERROR] {ev}")

    # Load existing memories so the agent can reference them
    memory = load_memory()
    agent = VoiceAgent()
    agent._room = ctx.room
    if memory:
        memory_summary = ", ".join(f"{k}: {v}" for k, v in memory.items())
        agent._extra_context = (
            f"\n\nYou remember these things about the user: {memory_summary}. "
            "Reference them naturally in your greeting."
        )

    # Data channel listener for frontend button clicks
    @ctx.room.on("data_received")
    def on_data_received(packet: DataPacket):
        if packet.topic != "draft_schedule":
            return
        try:
            msg = json.loads(packet.data.decode("utf-8"))
            action = msg.get("action")
            logger.info(f"[DATA CHANNEL] Received action: {action}")

            import asyncio
            loop = asyncio.get_event_loop()

            if action == "confirm":
                loop.create_task(_handle_confirm(agent, session))
            elif action == "update_item":
                loop.create_task(_handle_update_item(agent, session, msg))
        except Exception as e:
            logger.error(f"[DATA CHANNEL] Error processing message: {e}")

    logger.info("[STARTUP] Starting agent session...")
    await session.start(agent, room=ctx.room)
    logger.info("[STARTUP] Session started, generating initial reply...")
    await session.generate_reply()
    logger.info("[STARTUP] Initial reply generated")


async def _handle_confirm(agent: VoiceAgent, session: AgentSession):
    """Handle confirm button click from frontend."""
    result = await agent.confirm_draft_schedule()
    logger.info(f"[DATA CHANNEL] Confirm result: {result}")


async def _handle_update_item(agent: VoiceAgent, session: AgentSession, msg: dict):
    """Handle update_item action from frontend."""
    result = await agent.update_draft_item(
        item_id=msg.get("item_id", ""),
        new_date=msg.get("new_date", ""),
        new_start_time=msg.get("new_start_time", ""),
        new_duration_minutes=msg.get("new_duration_minutes", 0),
    )
    logger.info(f"[DATA CHANNEL] Update item result: {result}")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
