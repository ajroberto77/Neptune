import { useState, type FormEvent } from "react";
import { AuthError, login } from "../api/auth";

interface Props {
  onSignedIn: () => void;
}

/** JANUS login gate -- shown whenever no valid access token is on hand. Neptune's own backend
 * never issues tokens (design doc §7 Neptune section / §15.1: authorization lives where the
 * data plane lives), so this posts straight to the identity service via the /auth proxy
 * (vite.config.ts) and stores whatever it returns. */
export function LoginScreen({ onSignedIn }: Props) {
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!identifier || !password || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await login(identifier, password);
      onSignedIn();
    } catch (err) {
      setError(err instanceof AuthError ? err.message : "Could not reach the identity service.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex h-full items-center justify-center bg-ocean-bg">
      <form
        onSubmit={handleSubmit}
        className="w-80 rounded border border-ocean-border bg-ocean-panel p-6 shadow-lg"
      >
        <h1 className="mb-1 font-display text-lg text-slate-100">Neptune</h1>
        <p className="mb-5 text-xs text-ocean-muted">Sign in with your Iridium identity.</p>

        <label className="mb-3 block text-xs text-ocean-muted">
          Email or username
          <input
            className="np-input mt-1"
            data-testid="login-identifier"
            autoFocus
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            autoComplete="username"
          />
        </label>

        <label className="mb-4 block text-xs text-ocean-muted">
          Password
          <input
            className="np-input mt-1"
            data-testid="login-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>

        {error && (
          <p className="mb-3 text-xs text-status-breach" data-testid="login-error">
            {error}
          </p>
        )}

        <button
          type="submit"
          data-testid="login-submit"
          disabled={submitting}
          className="w-full rounded bg-ocean-accent py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
