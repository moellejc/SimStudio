import { describe, expect, it } from "vitest";
import { deckNamesOk, templateSchema } from "../lib/validators";
import { formatPa, isActive, statusLabel } from "../lib/format";
import { readSessionToken, signSession } from "../lib/auth";

describe("deck names", () => {
  it("accepts a matching starter and engine pair", () => {
    expect(deckNamesOk("fork45_steel_0000.rad", "fork45_steel_0001.rad")).toBe("fork45_steel");
  });
  it("rejects a mismatched run name", () => {
    expect(deckNamesOk("A_0000.rad", "B_0001.rad")).toBeNull();
  });
});

describe("template schema", () => {
  it("defaults dry run on", () => {
    const parsed = templateSchema.parse({
      name: "Steel fork",
      templateId: "fork_load",
      material: "steel",
      endTimeMs: 20,
      loadKN: 0.4413,
      stopDisplacementMm: 40,
      pushVelocityMmMs: 0.25,
      rampMs: 100,
      threads: 2,
      friction: 0.3,
    });
    expect(parsed.dryRun).toBe(true);
  });
});

describe("formatting", () => {
  it("prints pascals in scientific notation", () => {
    expect(formatPa(3.52e6)).toBe("3.52e+6 Pa");
  });
  it("knows which states are still running", () => {
    expect(isActive("running_engine")).toBe(true);
    expect(isActive("succeeded")).toBe(false);
    expect(statusLabel("running_starter")).toBe("Starter");
  });
});

describe("session token", () => {
  it("round-trips a user", async () => {
    process.env.JWT_SECRET = "test-secret-test-secret-test-secret";
    const token = await signSession({ sub: "user-1", email: "a@b.co", name: "Ada" });
    const session = await readSessionToken(token);
    expect(session).toEqual({ sub: "user-1", email: "a@b.co", name: "Ada" });
  });
});
