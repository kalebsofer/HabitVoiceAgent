# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Backend (agent/)
```bash
cd agent
uv run python main.py dev          # Start LiveKit agent in dev mode
uv run python test_calendar.py     # Manual test for Google Calendar API
uv sync                            # Install/update Python dependencies
uv add <package>                   # Add a new dependency
```

### Frontend (frontend/)
```bash
cd frontend
npm run dev                        # Start Next.js dev server
npx next build                     # Production build
npm start                          # Start production server
npm install                        # Install Node dependencies
```

Both services must run simultaneously — the frontend connects to the LiveKit agent via WebRTC.

### Environment Variables
- `agent/.env` — `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `OPENAI_API_KEY`
- `frontend/.env.local` — `NEXT_PUBLIC_LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- `agent/credentials.json` + `agent/token.json` — Google OAuth credentials (not committed)

## Architecture

**Voice-first habit scheduling assistant** — users speak to a LiveKit voice agent that plans habits and schedules them on Google Calendar.

### Backend: Python LiveKit Agent (`agent/main.py`)
- `VoiceAgent` class extends `livekit.agents.Agent` with OpenAI Realtime API (voice model)
- Tools are instance methods decorated with `@llm.function_tool(description=...)`
- Entry point: `entrypoint(ctx: JobContext)` at module level
- State persisted as JSON files: `memory.json`, `habit_plan.json`, `draft_schedule.json`
- Google Calendar integration in `agent/calendar_client.py` (OAuth2, supports multiple calendars)

### Frontend: Next.js App (`frontend/`)
- Single-page app at `app/page.tsx` — voice controls + calendar visualization
- `app/components/DraftCalendar.tsx` — custom week-view calendar grid (no library)
- `app/api/token/route.ts` — generates LiveKit access tokens for room connection
- Uses `@livekit/components-react` hooks: `useVoiceAssistant()`, `useDataChannel()`, `useLocalParticipant()`

### Communication: LiveKit Data Channels
Backend publishes via `room.local_participant.publish_data(payload, topic=...)`. Frontend listens with `useDataChannel(topic, onMessage)`.

| Topic | Direction | Payload |
|---|---|---|
| `agent_status` | Backend → Frontend | `{ type: "status", message: string }` |
| `draft_schedule` | Backend → Frontend | Full draft schedule JSON |
| `draft_schedule` | Frontend → Backend | `{ action: "confirm" \| "update_item", ... }` |

Backend receives frontend messages via `@ctx.room.on("data_received")`.

### Stage-Based Conversation Pipeline
The agent follows a structured flow: **GREETING → DISCOVERY → DETAILING → CONFIRMATION → SCHEDULING → REVIEW**

Key design: tool return values drive agent behavior, not the system prompt. Each tool returns stage-specific instructions telling the LLM what to do next. The `assess_user_input` tool must be called first when users describe goals/habits — it classifies input and returns the appropriate stage + guidance.

Stage transitions: `save_habit_plan` → SCHEDULING, `generate_draft_schedule` → REVIEW, `confirm_draft_schedule` → COMPLETE.

### Draft Scheduling Algorithm
1. Fetches events from all visible Google Calendars for the current month
2. Builds a busy-slot list from existing events
3. For each habit, attempts placement at preferred time
4. On conflict, shifts by 30-minute increments (up to 24 hours) to find a free slot
5. Pushes draft to frontend for visual review before creating real calendar events

## Key Implementation Details

- **Imports**: `from livekit.rtc import DataPacket` (not from `livekit.agents`)
- **Timezone handling**: All timestamps use user's IANA timezone; agent detects it at session start
- **Time parsing**: `_parse_preferred_time()` handles formats like "3pm", "8:30 AM", "morning", "lunchtime" — returns `(time_str, warning)`
- **Cadence validation**: `save_habit_plan` validates against `RECURRENCE_MAP` keys: daily, weekdays, weekly, 3x_per_week, monthly
- **Multi-calendar**: Events carry `_calendar_name` and `_calendar_color`; frontend renders per-calendar color accents (4px left border)
- **Python version**: 3.12+ required (see `.python-version`)
- **Package manager**: UV for Python, npm for frontend
