import { describe, expect, it, vi, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LoginScreen } from "./LoginScreen";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("LoginScreen", () => {
  it("submits identifier/password and calls onSignedIn on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            access_token: "at1",
            refresh_token: "rt1",
            token_type: "bearer",
            expires_in: 900,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const onSignedIn = vi.fn();
    render(<LoginScreen onSignedIn={onSignedIn} />);

    fireEvent.change(screen.getByTestId("login-identifier"), { target: { value: "pm@iridium.example" } });
    fireEvent.change(screen.getByTestId("login-password"), { target: { value: "correct horse battery staple" } });
    fireEvent.click(screen.getByTestId("login-submit"));

    await waitFor(() => expect(onSignedIn).toHaveBeenCalledTimes(1));
  });

  it("shows the server's error message on a rejected login and does not call onSignedIn", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "invalid_credentials" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const onSignedIn = vi.fn();
    render(<LoginScreen onSignedIn={onSignedIn} />);

    fireEvent.change(screen.getByTestId("login-identifier"), { target: { value: "pm@iridium.example" } });
    fireEvent.change(screen.getByTestId("login-password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByTestId("login-submit"));

    await waitFor(() => expect(screen.getByTestId("login-error")).toHaveTextContent("invalid_credentials"));
    expect(onSignedIn).not.toHaveBeenCalled();
  });
});
