import { beforeEach, describe, expect, it } from "vitest";
import { ALL_COLUMNS, DEFAULT_VISIBLE, loadBlotterColumns, saveBlotterColumns } from "./blotterColumns";
import type { ColumnKey } from "./blotterColumns";

describe("blotterColumns", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("defaults to all 8 columns in the original order when nothing is stored", () => {
    expect(loadBlotterColumns()).toEqual(DEFAULT_VISIBLE);
    expect(DEFAULT_VISIBLE).toEqual(ALL_COLUMNS.map((c) => c.key));
  });

  it("round-trips a saved column set", () => {
    const custom: ColumnKey[] = ["ticker", "action", "price"];
    saveBlotterColumns(custom);
    expect(loadBlotterColumns()).toEqual(custom);
  });

  it("falls back to defaults on malformed JSON", () => {
    window.localStorage.setItem("neptune.blotter.columns.v1", "{not json");
    expect(loadBlotterColumns()).toEqual(DEFAULT_VISIBLE);
  });

  it("falls back to defaults when the stored value isn't an array", () => {
    window.localStorage.setItem("neptune.blotter.columns.v1", JSON.stringify({ foo: "bar" }));
    expect(loadBlotterColumns()).toEqual(DEFAULT_VISIBLE);
  });

  it("filters out a key no longer in ALL_COLUMNS (simulating a future rename) instead of crashing", () => {
    window.localStorage.setItem(
      "neptune.blotter.columns.v1",
      JSON.stringify(["ticker", "some_removed_column", "price"]),
    );
    expect(loadBlotterColumns()).toEqual(["ticker", "price"]);
  });

  it("falls back to defaults when every stored key is unrecognized", () => {
    window.localStorage.setItem(
      "neptune.blotter.columns.v1",
      JSON.stringify(["totally_bogus"]),
    );
    expect(loadBlotterColumns()).toEqual(DEFAULT_VISIBLE);
  });
});
