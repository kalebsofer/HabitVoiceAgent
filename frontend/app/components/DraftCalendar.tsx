"use client";

import { useMemo, useState } from "react";

// --- Types ---

interface ScheduleItem {
  id: string;
  type: "existing" | "draft";
  summary: string;
  start: string;
  end: string;
  recurrence: string | null;
  habit_name: string | null;
  description?: string;
  calendar_name?: string;
  calendar_color?: string;
  all_day?: boolean;
}

export interface DraftSchedule {
  timezone: string;
  month: string; // "YYYY-MM"
  generated_at: string;
  status: "draft" | "confirmed";
  items: ScheduleItem[];
}

interface DraftCalendarProps {
  schedule: DraftSchedule;
  onConfirm: () => void;
}

// --- Constants ---

const HOUR_START = 6;
const HOUR_END = 22;
const HOURS = Array.from({ length: HOUR_END - HOUR_START }, (_, i) => HOUR_START + i);
const ROW_HEIGHT = 48; // px per hour slot
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// --- Helpers ---

function getMonday(date: Date): Date {
  const d = new Date(date);
  const day = d.getDay(); // 0=Sun
  const diff = day === 0 ? -6 : 1 - day;
  d.setDate(d.getDate() + diff);
  d.setHours(0, 0, 0, 0);
  return d;
}

function addDays(date: Date, days: number): Date {
  const d = new Date(date);
  d.setDate(d.getDate() + days);
  return d;
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

function formatHour(h: number): string {
  if (h === 0) return "12 AM";
  if (h < 12) return `${h} AM`;
  if (h === 12) return "12 PM";
  return `${h - 12} PM`;
}

function formatMonth(monthStr: string): string {
  const [year, month] = monthStr.split("-").map(Number);
  const d = new Date(year, month - 1, 1);
  return d.toLocaleDateString("en-US", { month: "long", year: "numeric" });
}

// --- Component ---

export default function DraftCalendar({ schedule, onConfirm }: DraftCalendarProps) {
  const today = new Date();
  const [weekStart, setWeekStart] = useState<Date>(() => getMonday(today));

  const weekDays = useMemo(
    () => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)),
    [weekStart]
  );

  // Filter items visible in current week
  const weekItems = useMemo(() => {
    const weekEnd = addDays(weekStart, 7);
    return schedule.items.filter((item) => {
      const start = new Date(item.start);
      return start >= weekStart && start < weekEnd;
    });
  }, [schedule.items, weekStart]);

  const prevWeek = () => setWeekStart((w) => addDays(w, -7));
  const nextWeek = () => setWeekStart((w) => addDays(w, 7));

  const isConfirmed = schedule.status === "confirmed";

  return (
    <div style={{ width: "100%", maxWidth: 900, margin: "0 auto" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 12,
        }}
      >
        <button onClick={prevWeek} style={navBtnStyle}>
          &larr;
        </button>
        <h3 style={{ margin: 0, fontSize: "1rem", opacity: 0.8 }}>
          {formatMonth(schedule.month)}
        </h3>
        <button onClick={nextWeek} style={navBtnStyle}>
          &rarr;
        </button>
      </div>

      {/* Grid */}
      <div style={{ display: "flex", overflow: "hidden", borderRadius: 8, border: "1px solid #333" }}>
        {/* Time gutter */}
        <div style={{ width: 56, flexShrink: 0, borderRight: "1px solid #333" }}>
          <div style={{ height: 32 }} /> {/* spacer for day header */}
          {HOURS.map((h) => (
            <div
              key={h}
              style={{
                height: ROW_HEIGHT,
                display: "flex",
                alignItems: "flex-start",
                justifyContent: "flex-end",
                paddingRight: 6,
                paddingTop: 2,
                fontSize: "0.65rem",
                opacity: 0.5,
                borderTop: "1px solid #222",
              }}
            >
              {formatHour(h)}
            </div>
          ))}
        </div>

        {/* Day columns */}
        {weekDays.map((day, di) => {
          const isToday = sameDay(day, today);
          const dayItemsAll = weekItems.filter((item) => {
            // All-day events use "YYYY-MM-DD" format without time
            if (item.all_day) {
              const itemDate = new Date(item.start + "T00:00:00");
              return sameDay(itemDate, day);
            }
            return sameDay(new Date(item.start), day);
          });
          const allDayItems = dayItemsAll.filter((item) => item.all_day);
          const dayItems = dayItemsAll.filter((item) => !item.all_day);

          return (
            <div
              key={di}
              style={{
                flex: 1,
                minWidth: 0,
                borderLeft: di > 0 ? "1px solid #222" : undefined,
                background: isToday ? "rgba(74, 222, 128, 0.04)" : undefined,
              }}
            >
              {/* Day header */}
              <div
                style={{
                  height: 32,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  borderBottom: "1px solid #333",
                  fontSize: "0.7rem",
                  opacity: isToday ? 1 : 0.6,
                  fontWeight: isToday ? 700 : 400,
                  color: isToday ? "#4ade80" : "#fafafa",
                }}
              >
                <span>{DAY_NAMES[di]}</span>
                <span style={{ fontSize: "0.6rem" }}>{day.getDate()}</span>
              </div>

              {/* All-day event banners */}
              {allDayItems.map((item) => (
                <div
                  key={item.id}
                  title={`${item.summary} (all day)${item.calendar_name ? `\n[${item.calendar_name}]` : ""}`}
                  style={{
                    padding: "1px 4px",
                    fontSize: "0.55rem",
                    background: "#2a2a3a",
                    borderLeft: `3px solid ${item.calendar_color || "#3a3a4a"}`,
                    color: "#aaa",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    borderBottom: "1px solid #222",
                  }}
                >
                  {item.summary}
                </div>
              ))}

              {/* Hour rows + events */}
              <div style={{ position: "relative" }}>
                {HOURS.map((h) => (
                  <div
                    key={h}
                    style={{
                      height: ROW_HEIGHT,
                      borderTop: "1px solid #222",
                    }}
                  />
                ))}

                {/* Event blocks */}
                {dayItems.map((item) => {
                  const start = new Date(item.start);
                  const end = new Date(item.end);
                  const startHour = start.getHours() + start.getMinutes() / 60;
                  const endHour = end.getHours() + end.getMinutes() / 60;
                  const top = (startHour - HOUR_START) * ROW_HEIGHT;
                  const height = Math.max((endHour - startHour) * ROW_HEIGHT, 18);

                  const isDraft = item.type === "draft";
                  const calColor = item.calendar_color || "#3a3a4a";
                  const calLabel = item.calendar_name ? `\n[${item.calendar_name}]` : "";

                  return (
                    <div
                      key={item.id}
                      title={`${item.summary}\n${start.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} - ${end.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}${item.recurrence ? `\n(${item.recurrence})` : ""}${calLabel}`}
                      style={{
                        position: "absolute",
                        top,
                        left: 2,
                        right: 2,
                        height,
                        borderRadius: 4,
                        padding: "2px 4px",
                        fontSize: "0.6rem",
                        lineHeight: "1.2",
                        overflow: "hidden",
                        cursor: "default",
                        background: isDraft ? "#1a3a1a" : "#2a2a3a",
                        border: isDraft ? "1px dashed #4ade80" : "1px solid #3a3a4a",
                        borderLeft: isDraft ? undefined : `4px solid ${calColor}`,
                        color: isDraft ? "#4ade80" : "#aaa",
                      }}
                    >
                      <div
                        style={{
                          fontWeight: 600,
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        {item.summary}
                      </div>
                      {height > 28 && (
                        <div style={{ opacity: 0.7, fontSize: "0.55rem" }}>
                          {start.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 16,
          marginTop: 10,
          fontSize: "0.7rem",
          opacity: 0.6,
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span
            style={{
              display: "inline-block",
              width: 12,
              height: 12,
              borderRadius: 2,
              background: "#1a3a1a",
              border: "1px dashed #4ade80",
            }}
          />
          Draft habit
        </span>
        {/* Dynamic calendar legend entries */}
        {(() => {
          const seen = new Map<string, string>();
          for (const item of schedule.items) {
            if (item.type === "existing" && item.calendar_name && !seen.has(item.calendar_name)) {
              seen.set(item.calendar_name, item.calendar_color || "#3a3a4a");
            }
          }
          // If no calendar metadata, show generic "Existing event" legend
          if (seen.size === 0) {
            return (
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span
                  style={{
                    display: "inline-block",
                    width: 12,
                    height: 12,
                    borderRadius: 2,
                    background: "#2a2a3a",
                    border: "1px solid #3a3a4a",
                  }}
                />
                Existing event
              </span>
            );
          }
          return Array.from(seen.entries()).map(([name, color]) => (
            <span key={name} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <span
                style={{
                  display: "inline-block",
                  width: 12,
                  height: 12,
                  borderRadius: 2,
                  background: "#2a2a3a",
                  borderLeft: `4px solid ${color}`,
                  border: `1px solid ${color}`,
                }}
              />
              {name}
            </span>
          ));
        })()}
      </div>

      {/* Confirmed overlay */}
      {isConfirmed && (
        <div
          style={{
            marginTop: 16,
            textAlign: "center",
            padding: "12px 16px",
            background: "rgba(74, 222, 128, 0.1)",
            border: "1px solid #4ade80",
            borderRadius: 8,
            color: "#4ade80",
            fontWeight: 600,
          }}
        >
          Schedule confirmed! Your habits are on your calendar.
        </div>
      )}
    </div>
  );
}

// --- Styles ---

const navBtnStyle: React.CSSProperties = {
  background: "none",
  border: "1px solid #333",
  borderRadius: 6,
  color: "#fafafa",
  padding: "4px 12px",
  cursor: "pointer",
  fontSize: "1rem",
};
