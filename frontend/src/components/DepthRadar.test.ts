/**
 * The radar's whole purpose is the word "ahead".
 *
 * `distance_from_bit_m` is signed: positive means the historical event sits deeper than
 * the bit, which is where the bit is going. A sign error would turn a warning about an
 * interval the well is about to enter into a note about one it already passed, so the
 * selection is tested directly.
 */
import { describe, expect, it } from "vitest";
import { nearestAhead, relationToBit } from "./DepthRadar";
import type { HistoricalEvidence } from "../lib/api";

function evidence(
  id: number,
  offset: number | null,
): HistoricalEvidence {
  return {
    well_name: `well-${id}`,
    similarity_score: 0.9,
    event_id: id,
    event_type: "loss",
    depth_start_m: 2000 + (offset ?? 0),
    depth_end_m: null,
    distance_from_bit_m: offset,
    description: "",
    formation_name: null,
    depth_source: null,
    source_dataset: null,
    source_reference: null,
    mitigations: [],
  };
}

describe("nearestAhead", () => {
  it("picks the closest event deeper than the bit", () => {
    const chosen = nearestAhead([evidence(1, 140), evidence(2, 30), evidence(3, 90)]);
    expect(chosen?.event_id).toBe(2);
  });

  it("ignores events the bit has already passed", () => {
    expect(nearestAhead([evidence(1, -10), evidence(2, -200)])).toBeNull();
  });

  it("ignores an event with no recorded depth offset", () => {
    expect(nearestAhead([evidence(1, null)])).toBeNull();
  });

  it("does not treat an event at the bit as ahead of it", () => {
    expect(nearestAhead([evidence(1, 0)])).toBeNull();
  });
});

describe("relationToBit", () => {
  it("distinguishes ahead, behind and at the bit", () => {
    expect(relationToBit(150)).toBe("150 m ahead");
    expect(relationToBit(-45)).toBe("45 m behind");
    expect(relationToBit(0)).toBe("at the bit");
  });
});
