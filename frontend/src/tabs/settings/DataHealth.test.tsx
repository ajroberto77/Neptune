import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { DataHealth } from "./DataHealth";
import type { SecuritiesHealth } from "../../api/client";

const fetchSecuritiesHealth = vi.fn<() => Promise<SecuritiesHealth>>();

vi.mock("../../api/client", () => ({
  fetchSecuritiesHealth: () => fetchSecuritiesHealth(),
}));

const BASE: SecuritiesHealth = {
  benchmark: "SPY",
  benchmark_bars: 252,
  securities_projected: 540,
  names_with_30plus_bars: 539,
  names_with_computable_beta: 539,
  factor_panel: "MKT+SMB+HML+RMW+CMA+MOM",
  source: "db",
  reason: "ok",
};

describe("DataHealth", () => {
  it("shows the factor panel as healthy when loaded and fresh", async () => {
    fetchSecuritiesHealth.mockResolvedValueOnce({ ...BASE, factor_panel_stale: false });
    render(<DataHealth />);
    await waitFor(() => expect(screen.getByText("MKT+SMB+HML+RMW+CMA+MOM")).toBeInTheDocument());
    expect(screen.queryByText(/stale/)).not.toBeInTheDocument();
  });

  it("flags a loaded-but-stale panel distinctly from not-loaded, not as healthy", async () => {
    fetchSecuritiesHealth.mockResolvedValueOnce({ ...BASE, factor_panel_stale: true });
    render(<DataHealth />);
    // The panel string still shows the real, loaded value...
    await waitFor(() => expect(screen.getByText("MKT+SMB+HML+RMW+CMA+MOM")).toBeInTheDocument());
    // ...but a stale-specific hint appears, distinct from the "not loaded" hint.
    expect(screen.getByText(/panel loaded but stale/)).toBeInTheDocument();
    expect(screen.queryByText(/load Ken French to enable/)).not.toBeInTheDocument();
  });

  it("shows the not-loaded hint when the panel is genuinely unfilled (MKT-only)", async () => {
    fetchSecuritiesHealth.mockResolvedValueOnce({ ...BASE, factor_panel: "MKT-only", factor_panel_stale: false });
    render(<DataHealth />);
    await waitFor(() => expect(screen.getByText(/load Ken French to enable/)).toBeInTheDocument());
    expect(screen.queryByText(/panel loaded but stale/)).not.toBeInTheDocument();
  });
});
