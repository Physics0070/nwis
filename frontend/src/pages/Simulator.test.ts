/**
 * The simulator's only non-trivial logic: when geological context is re-queried.
 *
 * Re-querying on every 10-second sample leaves the risk and analogue panels permanently
 * loading; never re-querying leaves them describing the wrong depth.
 */
import { describe, expect, it } from "vitest";
import { nextContextDepth } from "./Simulator";

describe("nextContextDepth", () => {
  it("snaps to the grid on the first sample", () => {
    expect(nextContextDepth(null, 0)).toBe(0);
    expect(nextContextDepth(null, 1237)).toBe(1225);
  });

  it("holds steady while the bit has not moved a full step", () => {
    expect(nextContextDepth(1225, 1230)).toBe(1225);
    expect(nextContextDepth(1225, 1249)).toBe(1225);
  });

  it("advances once the bit has moved a full step", () => {
    expect(nextContextDepth(1225, 1250)).toBe(1250);
    expect(nextContextDepth(1225, 1400)).toBe(1400);
  });

  it("follows the bit back up a hole as well as down", () => {
    expect(nextContextDepth(1225, 1200)).toBe(1200);
  });
});
