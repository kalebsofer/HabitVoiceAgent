"use client";

import {
  LiveKitRoom,
  RoomAudioRenderer,
  useVoiceAssistant,
  BarVisualizer,
  DisconnectButton,
  useLocalParticipant,
} from "@livekit/components-react";
import "@livekit/components-styles";
import { useCallback, useEffect, useRef, useState } from "react";
import type { MediaDeviceFailure } from "livekit-client";
import { Track } from "livekit-client";

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
        height: "100vh",
        gap: "1rem",
        fontFamily: "sans-serif",
        background: "#0a0a0a",
        color: "#fafafa",
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
          style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "1rem" }}
        >
          <AgentView />
          <RoomMicVolumeMeter />
          <RoomAudioRenderer />
          <DisconnectButton>End Conversation</DisconnectButton>
        </LiveKitRoom>
      )}
    </main>
  );
}

function AgentView() {
  const { state, audioTrack } = useVoiceAssistant();

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "1rem" }}>
      <BarVisualizer
        state={state}
        barCount={5}
        trackRef={audioTrack}
        style={{ width: "200px", height: "100px" }}
      />
      <p style={{ textTransform: "capitalize", opacity: 0.6 }}>{state}</p>
    </div>
  );
}

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
