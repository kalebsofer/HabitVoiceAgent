import { AccessToken } from "livekit-server-sdk";
import { NextRequest, NextResponse } from "next/server";
import { decrypt } from "../../lib/crypto";

export async function GET(request: NextRequest) {
  const apiKey = process.env.LIVEKIT_API_KEY;
  const apiSecret = process.env.LIVEKIT_API_SECRET;
  const livekitUrl = process.env.NEXT_PUBLIC_LIVEKIT_URL;

  if (!apiKey || !apiSecret || !livekitUrl) {
    return NextResponse.json(
      { error: "Server misconfigured" },
      { status: 500 }
    );
  }

  // Read and decrypt Google tokens from cookie
  const cookie = request.cookies.get("google_tokens")?.value;
  if (!cookie) {
    return NextResponse.json(
      { error: "Not authenticated" },
      { status: 401 }
    );
  }

  let googleTokensJson: string;
  try {
    googleTokensJson = decrypt(cookie);
  } catch {
    return NextResponse.json(
      { error: "Session expired" },
      { status: 401 }
    );
  }

  const roomName = "voice-agent-room";
  const participantName = `user-${Math.random().toString(36).slice(2, 7)}`;

  const at = new AccessToken(apiKey, apiSecret, {
    identity: participantName,
  });

  at.addGrant({
    room: roomName,
    roomJoin: true,
    canPublish: true,
    canSubscribe: true,
  });

  // Embed Google tokens in participant metadata for the agent to read
  at.metadata = googleTokensJson;

  const token = await at.toJwt();

  return NextResponse.json({ token, url: livekitUrl });
}
