import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, AutoSubscribe, JobContext, WorkerOptions, cli, llm
from livekit.plugins import openai

import calendar_client

load_dotenv()

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("voice-agent")
logger.setLevel(logging.DEBUG)

MEMORY_FILE = Path(__file__).parent / "memory.json"

RECURRENCE_MAP = {
    "daily": ["RRULE:FREQ=DAILY"],
    "weekly": ["RRULE:FREQ=WEEKLY"],
    "weekdays": ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"],
    "monthly": ["RRULE:FREQ=MONTHLY"],
}


def load_memory() -> dict[str, str]:
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text())
    return {}


def save_memory(memory: dict[str, str]) -> None:
    MEMORY_FILE.write_text(json.dumps(memory, indent=2))


class VoiceAgent(Agent):
    _extra_context: str = ""

    def __init__(self):
        super().__init__(
            instructions=(
                "You are a warm, professional wellness assistant who helps people build healthy habits. "
                "You have access to Google Calendar and can schedule habits and actions for the user.\n\n"
                "Your conversation flow:\n"
                "1. Greet the user warmly. If you have memories about them, reference them naturally.\n"
                "2. Ask about their wellbeing, challenges, and goals.\n"
                "3. Suggest concrete lifestyle improvements (exercise, meditation, sleep, hydration, etc.).\n"
                "4. Offer to schedule these as calendar events.\n"
                "5. When scheduling, gather details conversationally — ask for one or two details at a time "
                "(event name, date/time, duration, whether it repeats), don't ask for everything at once.\n"
                "6. Confirm the details before creating the event.\n"
                "7. After creating, encourage the user and offer to schedule more.\n\n"
                "You also have a memory tool. Use it to remember important things the user tells you "
                "(their name, habits, goals, preferences) and recall them in future sessions. "
                "Proactively save things that seem important.\n\n"
                "Keep responses concise since this is a voice conversation. Be encouraging but not over-the-top. "
                "Always speak in English."
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
            events = calendar_client.list_upcoming_events(max_results=10)
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
    if memory:
        memory_summary = ", ".join(f"{k}: {v}" for k, v in memory.items())
        agent._extra_context = (
            f"\n\nYou remember these things about the user: {memory_summary}. "
            "Reference them naturally in your greeting."
        )

    logger.info("[STARTUP] Starting agent session...")
    await session.start(agent, room=ctx.room)
    logger.info("[STARTUP] Session started, generating initial reply...")
    await session.generate_reply()
    logger.info("[STARTUP] Initial reply generated")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
