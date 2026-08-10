import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearTokens, getAccessToken, isAuthenticated, login, logout, refresh, AuthError } from "./auth";

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

describe("auth", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("stores tokens on a successful login and reports authenticated", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          access_token: "at1",
          refresh_token: "rt1",
          token_type: "bearer",
          expires_in: 900,
          must_change_password: false,
        }),
      ),
    );

    expect(isAuthenticated()).toBe(false);
    const tokens = await login("pm@iridium.example", "correct horse battery staple");
    expect(tokens.accessToken).toBe("at1");
    expect(getAccessToken()).toBe("at1");
    expect(isAuthenticated()).toBe(true);

    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const body = JSON.parse(init.body as string);
    expect(body.product).toBe("neptune");
    expect(body.identifier).toBe("pm@iridium.example");
    expect(typeof body.device_id).toBe("string");
  });

  it("raises AuthError with the server detail on a failed login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: "invalid_credentials" }, { status: 401 }),
      ),
    );

    await expect(login("pm@iridium.example", "wrong")).rejects.toThrow(AuthError);
    expect(isAuthenticated()).toBe(false);
  });

  it("refresh() clears tokens and returns null when the refresh token is rejected", async () => {
    localStorage.setItem("neptune.janus.access_token", "stale");
    localStorage.setItem("neptune.janus.refresh_token", "expired");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 401 })));

    const result = await refresh();
    expect(result).toBeNull();
    expect(getAccessToken()).toBeNull();
  });

  it("refresh() with no stored refresh token is a no-op, no network call", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const result = await refresh();
    expect(result).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("logout() clears local tokens even if the network call fails", async () => {
    localStorage.setItem("neptune.janus.access_token", "at1");
    localStorage.setItem("neptune.janus.refresh_token", "rt1");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    await logout();
    expect(getAccessToken()).toBeNull();
  });

  it("clearTokens() removes both stored tokens", () => {
    localStorage.setItem("neptune.janus.access_token", "at1");
    localStorage.setItem("neptune.janus.refresh_token", "rt1");
    clearTokens();
    expect(getAccessToken()).toBeNull();
    expect(localStorage.getItem("neptune.janus.refresh_token")).toBeNull();
  });
});
