// Authentication against JANUS (iridium-iam) -- see api/auth.py on the backend, and design doc
// §7's Neptune section / §15.5. Neptune's own FastAPI process verifies bearer tokens; it never
// issues them, so login is a direct call to the identity service, not to Neptune's own backend.
//
// Token storage is localStorage, not sessionStorage: unlike admin-web (§14.7.4's own highest-
// privilege-token reasoning), a desk tool that's re-authenticated on every tab close would be a
// real daily-use cost, and a Neptune session's absolute lifetime is still hard-capped server-side
// (AuthSession.absolute_expires_at) regardless of what the browser holds. Same tradeoff CATO's
// own client/node package makes for its refresh token.
const ACCESS_KEY = "neptune.janus.access_token";
const REFRESH_KEY = "neptune.janus.refresh_token";
const DEVICE_KEY = "neptune.janus.device_id";

const PRODUCT = "neptune";

// Deliberately NOT a `declare global` augmentation of Window.neptune here -- client.ts already
// owns that global declaration (NeptuneBridge, the API base bridge), and a second, differently-
// shaped `interface Window { neptune?: ... }` in this file would conflict with it (TypeScript
// requires every declaration merge of the same global property to agree on its type). A local
// cast at the one call site that needs the extra optional method avoids owning the global at all.
interface JanusBridge {
  getJanusIssuer?(): Promise<string>;
}

let _janusBase: string | null = null;

/** Resolves the JANUS base URL: relative ("") in a plain browser dev/prod build, where Vite's
 * proxy (dev) or same-origin hosting (prod, if ever served that way) forwards /auth and /me --
 * or JANUS's documented default loopback address inside the Electron shell, where there is no
 * proxy and a packaged build loads from a file:// origin. Neptune's Electron bridge does not
 * (yet) expose the configured issuer the way it exposes the Neptune API base
 * (window.neptune.getApiBaseUrl) -- wiring that through main.js/preload.cjs is per-product
 * Electron work, out of scope here; the loopback default matches iridium_iam's own
 * IRIDIUM_IAM_API_PORT default (design doc §2/§12) and is overridable later the same way the
 * Neptune API base is. */
async function janusBase(): Promise<string> {
  if (_janusBase !== null) return _janusBase;
  const bridge =
    typeof window !== "undefined" ? (window.neptune as JanusBridge | undefined) : undefined;
  let base: string;
  if (!bridge?.getJanusIssuer) {
    base = bridge ? "http://127.0.0.1:8700" : "";
  } else {
    try {
      base = (await bridge.getJanusIssuer()) || "http://127.0.0.1:8700";
    } catch {
      base = "http://127.0.0.1:8700";
    }
  }
  _janusBase = base;
  return base;
}

function deviceId(): string {
  let id = localStorage.getItem(DEVICE_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(DEVICE_KEY, id);
  }
  return id;
}

export interface AuthTokens {
  accessToken: string;
  refreshToken: string | null;
  mustChangePassword: boolean;
}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_KEY);
}

function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

function storeTokens(accessToken: string, refreshToken: string | null): void {
  localStorage.setItem(ACCESS_KEY, accessToken);
  if (refreshToken) localStorage.setItem(REFRESH_KEY, refreshToken);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export function isAuthenticated(): boolean {
  return getAccessToken() !== null;
}

interface LoginErrorBody {
  detail?: string;
}

export class AuthError extends Error {}

/** POST /auth/login -- identifier is an email OR a username (design doc §53/54's unification;
 * iridium_iam resolves whichever it is in one case-insensitive lookup). */
export async function login(identifier: string, password: string): Promise<AuthTokens> {
  const base = await janusBase();
  const res = await fetch(`${base}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      identifier,
      password,
      product: PRODUCT,
      device_id: deviceId(),
      device_label: typeof navigator !== "undefined" ? navigator.userAgent : undefined,
    }),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as LoginErrorBody;
    throw new AuthError(body.detail || `login failed (${res.status})`);
  }
  const data = await res.json();
  storeTokens(data.access_token, data.refresh_token ?? null);
  return {
    accessToken: data.access_token,
    refreshToken: data.refresh_token ?? null,
    mustChangePassword: Boolean(data.must_change_password),
  };
}

/** POST /auth/refresh. Returns the new access token on success, or null if the refresh token is
 * missing/expired/rejected -- callers should treat null as "sign in again", not throw. */
export async function refresh(): Promise<string | null> {
  const token = getRefreshToken();
  if (!token) return null;
  const base = await janusBase();
  const res = await fetch(`${base}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: token }),
  });
  if (!res.ok) {
    clearTokens();
    return null;
  }
  const data = await res.json();
  storeTokens(data.access_token, data.refresh_token ?? null);
  return data.access_token as string;
}

/** POST /auth/logout, best-effort, then always clears local state -- a failed logout call must
 * never leave the app believing it's still signed in. */
export async function logout(): Promise<void> {
  const token = getRefreshToken();
  clearTokens();
  if (!token) return;
  try {
    const base = await janusBase();
    await fetch(`${base}/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: token }),
    });
  } catch {
    // best-effort -- local tokens are already cleared above
  }
}
