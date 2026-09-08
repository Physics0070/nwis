/**
 * The simulator's only non-trivial logic: when geological context is re-queried.
 *
 * Re-querying on every 10-second sample leaves the risk and analogue panels permanently
 * loading; never re-querying leaves them describing the wrong depth.
 */
import { describe, expect, it } from "vitest";
import { depthRelationToBit, nextContextDepth } from "./Simulator";

// The step is the backend's `ui.depth_context_step_m`, passed in rather than declared
// here, so the frontend cannot drift away from the value the API serves.
const STEP = 25;

describe("nextContextDepth", () => {
  it("snaps to the grid on the first sample", () => {
    expect(nextContextDepth(null, 0, STEP)).toBe(0);
    expect(nextContextDepth(null, 1237, STEP)).toBe(1225);
  });

  it("holds steady while the bit has not moved a full step", () => {
    expect(nextContextDepth(1225, 1230, STEP)).toBe(1225);
    expect(nextContextDepth(1225, 1249, STEP)).toBe(1225);
  });

  it("advances once the bit has moved a full step", () => {
    expect(nextContextDepth(1225, 1250, STEP)).toBe(1250);
    expect(nextContextDepth(1225, 1400, STEP)).toBe(1400);
  });

  it("follows the bit back up a hole as well as down", () => {
    expect(nextContextDepth(1225, 1200, STEP)).toBe(1200);
  });
});

describe("depthRelationToBit", () => {
  it("says same depth rather than '0 m away'", () => {
    // The bit and the event are both at 0 m. "0 m away" read as a distance between
    // wells, which is false for an analogue 8 km off.
    expect(depthRelationToBit(0)).toBe("same depth");
  });

  it("states direction, because above and below the bit are different situations", () => {
    expect(depthRelationToBit(120)).toBe("120 m deeper");
    expect(depthRelationToBit(-35)).toBe("35 m shallower");
  });
});

describe("the step comes from configuration", () => {
  it("uses the supplied step, not a constant of its own", () => {
    // A backend configured with a 50 m step must not be quantised on a 25 m grid.
    expect(nextContextDepth(null, 1237, 50)).toBe(1250);
    expect(nextContextDepth(1200, 1230, 50)).toBe(1200);
  });
});
