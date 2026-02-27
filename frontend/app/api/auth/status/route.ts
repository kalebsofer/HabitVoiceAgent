import { NextRequest, NextResponse } from "next/server";
import { decrypt } from "../../../lib/crypto";

export async function GET(request: NextRequest) {
  const cookie = request.cookies.get("google_tokens")?.value;
  if (!cookie) {
    return NextResponse.json({ authenticated: false });
  }

  try {
    const decrypted = decrypt(cookie);
    const tokens = JSON.parse(decrypted);
    // Check that we have at least an access token or refresh token
    if (!tokens.access_token && !tokens.refresh_token) {
      return NextResponse.json({ authenticated: false });
    }
    return NextResponse.json({ authenticated: true });
  } catch {
    // Cookie corrupted or key changed — clear it
    const response = NextResponse.json({ authenticated: false });
    response.cookies.delete("google_tokens");
    return response;
  }
}
