import { useEffect, useState, type ReactNode } from "react";
import { AUTH_EXPIRED_EVENT } from "./api/client";
import { isAuthenticated } from "./api/auth";
import { LoginScreen } from "./components/LoginScreen";

/** Gates the whole app behind a JANUS session. Listens for AUTH_EXPIRED_EVENT (dispatched by
 * api/client.ts's getJSON when a request 401s even after a refresh attempt) so an expired
 * session drops back to the login screen without every tab needing its own auth-awareness. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(isAuthenticated());

  useEffect(() => {
    const onExpired = () => setAuthed(false);
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  if (!authed) {
    return <LoginScreen onSignedIn={() => setAuthed(true)} />;
  }
  return <>{children}</>;
}
