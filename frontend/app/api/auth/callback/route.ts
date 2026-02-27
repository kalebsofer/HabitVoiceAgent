import { NextRequest, NextResponse } from "next/server";
import { encrypt } from "../../../lib/crypto";

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const code = searchParams.get("code");
  const state = searchParams.get("state");
  const error = searchParams.get("error");

  if (error) {
    return NextResponse.redirect(new URL("/?auth_error=" + error, request.url));
  }

  if (!code || !state) {
    return NextResponse.redirect(new URL("/?auth_error=missing_params", request.url));
  }

  // Verify CSRF state
  const storedState = request.cookies.get("oauth_state")?.value;
  if (!storedState || storedState !== state) {
    return NextResponse.redirect(new URL("/?auth_error=invalid_state", request.url));
  }

  const clientId = process.env.GOOGLE_CLIENT_ID;
  const clientSecret = process.env.GOOGLE_CLIENT_SECRET;
  const redirectUri = process.env.GOOGLE_REDIRECT_URI || `${process.env.NEXTAUTH_URL || "http://localhost:3000"}/api/auth/callback`;

  if (!clientId || !clientSecret) {
    return NextResponse.redirect(new URL("/?auth_error=server_misconfigured", request.url));
  }

  // Exchange authorization code for tokens
  const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: clientId,
      client_secret: clientSecret,
      redirect_uri: redirectUri,
      grant_type: "authorization_code",
    }),
  });

  if (!tokenRes.ok) {
    const err = await tokenRes.text();
    console.error("Token exchange failed:", err);
    console.error("redirect_uri used:", redirectUri);
    console.error("client_id used:", clientId);
    return NextResponse.redirect(new URL("/?auth_error=token_exchange_failed", request.url));
  }

  const tokens = await tokenRes.json();

  // Extract stable Google user ID from id_token (JWT payload)
  let googleSub = "";
  if (tokens.id_token) {
    try {
      const payload = JSON.parse(
        Buffer.from(tokens.id_token.split(".")[1], "base64url").toString()
      );
      googleSub = payload.sub || "";
    } catch {
      console.error("Failed to decode id_token");
    }
  }

  // Encrypt tokens and store in httpOnly cookie
  const tokenPayload = JSON.stringify({
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token,
    token_type: tokens.token_type,
    expiry_date: Date.now() + tokens.expires_in * 1000,
    google_sub: googleSub,
  });

  const encrypted = encrypt(tokenPayload);

  const response = NextResponse.redirect(new URL("/", request.url));
  response.cookies.set("google_tokens", encrypted, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    maxAge: 60 * 60 * 24 * 30, // 30 days
    path: "/",
  });
  // Clear the CSRF state cookie
  response.cookies.delete("oauth_state");

  return response;
}
