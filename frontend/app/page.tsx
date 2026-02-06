"use client";

import {
  LiveKitRoom,
  RoomAudioRenderer,
  useVoiceAssistant,
  BarVisualizer,
  DisconnectButton,
  useLocalParticipant,
  useDataChannel,
} from "@livekit/components-react";
import "@livekit/components-styles";
import { useCallback, useEffect, useRef, useState } from "react";
import type { MediaDeviceFailure } from "livekit-client";
import { Track } from "livekit-client";
import DraftCalendar from "./components/DraftCalendar";
import type { DraftSchedule } from "./components/DraftCalendar";

export default function Home() {
  const [connectionDetails, setConnectionDetails] = useState<{
    token: string;
    url: string;
  } | null>(null);

  const connect = useCallback(async () => {
    const res = await fetch("/api/token");
    const details = await res.json();
    setConnectionDetails(details);
  }, []);

  return (
    <main
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        gap: "1rem",
        fontFamily: "sans-serif",
        background: "#0a0a0a",
        color: "#fafafa",
        padding: "1rem",
      }}
    >
      {!connectionDetails ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "1.5rem" }}>
          <MicVolumeMeter />
          <button
            onClick={connect}
            style={{
              padding: "0.75rem 2rem",
              fontSize: "1.1rem",
              borderRadius: "8px",
              border: "1px solid #333",
              background: "#1a1a1a",
              color: "#fafafa",
              cursor: "pointer",
            }}
          >
            Start Conversation
          </button>
        </div>
      ) : (
        <LiveKitRoom
          token={connectionDetails.token}
          serverUrl={connectionDetails.url}
          connect={true}
          audio={true}
          onMediaDeviceFailure={onDeviceFailure}
          onDisconnected={() => setConnectionDetails(null)}
          style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "1rem", width: "100%" }}
        >
          <AgentViewWithSchedule />
          <RoomMicVolumeMeter />
          <RoomAudioRenderer />
          <DisconnectButton>End Conversation</DisconnectButton>
        </LiveKitRoom>
      )}
    </main>
  );
}

function AgentViewWithSchedule() {
  const { state, audioTrack } = useVoiceAssistant();
  const [draft, setDraft] = useState<DraftSchedule | null>(null);
  const [showCalendar, setShowCalendar] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const statusTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [confirming, setConfirming] = useState(false);

  // Listen for status updates from the agent
  const onStatusMessage = useCallback((msg: { payload: Uint8Array }) => {
    try {
      const text = new TextDecoder().decode(msg.payload);
      const data = JSON.parse(text);
      if (data?.type === "status" && typeof data.message === "string") {
        setStatusMsg(data.message);
        // Auto-clear after 6 seconds
        if (statusTimeout.current) clearTimeout(statusTimeout.current);
        statusTimeout.current = setTimeout(() => setStatusMsg(null), 6000);
      }
    } catch {
      // ignore
    }
  }, []);

  useDataChannel("agent_status", onStatusMessage);

  // Listen for draft_schedule messages from the agent
  const onMessage = useCallback(
    (msg: { payload: Uint8Array }) => {
      try {
        const text = new TextDecoder().decode(msg.payload);
        const data = JSON.parse(text) as DraftSchedule;
        // Only accept objects that look like a schedule (have items array)
        if (data && Array.isArray(data.items)) {
          setDraft(data);
          // Clear confirming state when we receive a confirmed schedule
          if (data.status === "confirmed") setConfirming(false);
          // Auto-show calendar when first draft arrives
          if (!showCalendar) setShowCalendar(true);
        }
      } catch {
        // Ignore non-JSON or action messages from the frontend
      }
    },
    [showCalendar]
  );

  const { send } = useDataChannel("draft_schedule", onMessage);

  const handleConfirm = useCallback(() => {
    setConfirming(true);
    const payload = new TextEncoder().encode(JSON.stringify({ action: "confirm" }));
    send(payload, { topic: "draft_schedule" });
  }, [send]);

  const isConfirmed = draft?.status === "confirmed";
  const isDraft = draft && !isConfirmed;

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "1rem", width: "100%" }}>
      {/* Voice visualizer — always visible */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "1rem" }}>
        <BarVisualizer
          state={state}
          barCount={5}
          trackRef={audioTrack}
          style={{ width: "200px", height: "100px" }}
        />
        <p style={{ textTransform: "capitalize", opacity: 0.6 }}>{state}</p>
      </div>

      {/* Status message from agent */}
      {statusMsg && (
        <div style={statusBannerStyle}>
          <span style={statusDotStyle} />
          {statusMsg}
        </div>
      )}

      {/* Calendar toggle button */}
      {draft && !isConfirmed && (
        <button
          onClick={() => setShowCalendar((v) => !v)}
          style={{
            padding: "0.5rem 1.2rem",
            fontSize: "0.9rem",
            borderRadius: "6px",
            border: "1px solid #333",
            background: showCalendar ? "#2a2a3a" : "#1a1a1a",
            color: "#fafafa",
            cursor: "pointer",
          }}
        >
          {showCalendar ? "Hide Calendar" : "Show Calendar"}
        </button>
      )}

      {/* Calendar view */}
      {draft && showCalendar && (
        <div style={{ width: "100%", maxWidth: 920 }}>
          <DraftCalendar schedule={draft} onConfirm={handleConfirm} />

          {/* Action buttons */}
          {isDraft && (
            <div
              style={{
                display: "flex",
                justifyContent: "center",
                gap: "1rem",
                marginTop: 16,
              }}
            >
              <button
                onClick={handleConfirm}
                disabled={confirming}
                style={{
                  ...confirmBtnStyle,
                  ...(confirming ? confirmBtnDisabledStyle : {}),
                }}
              >
                {confirming ? "Confirming..." : "Confirm Schedule"}
              </button>
              <button
                onClick={() => setShowCalendar(false)}
                style={correctBtnStyle}
              >
                Make Changes (Voice)
              </button>
            </div>
          )}

          {/* Confirmed state */}
          {isConfirmed && (
            <div style={{ display: "flex", justifyContent: "center", marginTop: 16 }}>
              <button
                onClick={() => {
                  setShowCalendar(false);
                  setDraft(null);
                }}
                style={doneBtnStyle}
              >
                Done
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// --- Shared components (unchanged) ---

function MicBars({ level }: { level: number }) {
  const barCount = 20;
  const filledBars = Math.round(level * barCount);
  return (
    <div style={{ display: "flex", gap: "2px", height: "24px", alignItems: "flex-end" }}>
      {Array.from({ length: barCount }, (_, i) => {
        const on = i < filledBars;
        const color = i < barCount * 0.6 ? "#4ade80" : i < barCount * 0.85 ? "#facc15" : "#f87171";
        return (
          <div
            key={i}
            style={{
              width: "6px",
              height: "100%",
              borderRadius: "2px",
              background: on ? color : "#333",
              transition: "background 0.05s",
            }}
          />
        );
      })}
    </div>
  );
}

function MicVolumeMeter() {
  const [level, setLevel] = useState(0);
  const [active, setActive] = useState(false);
  const [error, setError] = useState(false);
  const rafRef = useRef<number>(0);
  const cleanupRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch {
        setError(true);
        return;
      }
      if (cancelled) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }

      const ctx = new AudioContext();
      if (ctx.state === "suspended") await ctx.resume();
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      src.connect(analyser);
      const timeDomainData = new Uint8Array(analyser.fftSize);

      const poll = () => {
        analyser.getByteTimeDomainData(timeDomainData);
        let peak = 0;
        for (let i = 0; i < timeDomainData.length; i++) {
          const v = Math.abs(timeDomainData[i] - 128);
          if (v > peak) peak = v;
        }
        setLevel(peak / 128);
        rafRef.current = requestAnimationFrame(poll);
      };

      setActive(true);
      poll();

      cleanupRef.current = () => {
        cancelAnimationFrame(rafRef.current);
        stream.getTracks().forEach((t) => t.stop());
        ctx.close();
      };
    })();

    return () => {
      cancelled = true;
      cleanupRef.current?.();
    };
  }, []);

  if (error) {
    return <p style={{ color: "#f87171", fontSize: "0.85rem" }}>Mic access denied</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "0.4rem" }}>
      <p style={{ fontSize: "0.85rem", opacity: 0.6, margin: 0 }}>
        {active ? "" : "Requesting mic..."}
      </p>
      <MicBars level={level} />
    </div>
  );
}

function RoomMicVolumeMeter() {
  const { localParticipant } = useLocalParticipant();
  const [level, setLevel] = useState(0);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const pub = localParticipant.getTrackPublication(Track.Source.Microphone);
    const mediaTrack = pub?.track?.mediaStreamTrack;
    if (!mediaTrack) return;

    const stream = new MediaStream([mediaTrack]);
    const ctx = new AudioContext();
    ctx.resume();
    const src = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    src.connect(analyser);
    const timeDomainData = new Uint8Array(analyser.fftSize);

    const poll = () => {
      analyser.getByteTimeDomainData(timeDomainData);
      let peak = 0;
      for (let i = 0; i < timeDomainData.length; i++) {
        const v = Math.abs(timeDomainData[i] - 128);
        if (v > peak) peak = v;
      }
      setLevel(peak / 128);
      rafRef.current = requestAnimationFrame(poll);
    };
    poll();

    return () => {
      cancelAnimationFrame(rafRef.current);
      ctx.close();
    };
  }, [localParticipant]);

  return <MicBars level={level} />;
}

function onDeviceFailure(error?: MediaDeviceFailure) {
  console.error("Media device failure:", error);
  alert("Please allow microphone access to use the voice agent.");
}

// --- Button styles ---

const confirmBtnStyle: React.CSSProperties = {
  padding: "0.6rem 1.5rem",
  fontSize: "0.95rem",
  borderRadius: "8px",
  border: "1px solid #4ade80",
  background: "#1a3a1a",
  color: "#4ade80",
  cursor: "pointer",
  fontWeight: 600,
};

const confirmBtnDisabledStyle: React.CSSProperties = {
  opacity: 0.5,
  cursor: "not-allowed",
};

const correctBtnStyle: React.CSSProperties = {
  padding: "0.6rem 1.5rem",
  fontSize: "0.95rem",
  borderRadius: "8px",
  border: "1px solid #555",
  background: "#1a1a1a",
  color: "#fafafa",
  cursor: "pointer",
};

const doneBtnStyle: React.CSSProperties = {
  padding: "0.6rem 2rem",
  fontSize: "0.95rem",
  borderRadius: "8px",
  border: "1px solid #4ade80",
  background: "#1a3a1a",
  color: "#4ade80",
  cursor: "pointer",
  fontWeight: 600,
};

const statusBannerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "0.5rem",
  padding: "0.4rem 1rem",
  fontSize: "0.8rem",
  borderRadius: "6px",
  background: "#1a1a2a",
  border: "1px solid #333",
  color: "#aaa",
};

const statusDotStyle: React.CSSProperties = {
  width: 6,
  height: 6,
  borderRadius: "50%",
  background: "#4ade80",
  animation: "pulse 1.5s ease-in-out infinite",
};
