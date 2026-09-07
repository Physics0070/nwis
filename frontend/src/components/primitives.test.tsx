/**
 * Tests for the state primitives.
 *
 * These guard the single most important frontend rule: a missing value must never render
 * as a number. An engineer reading "0 bar" when the truth is "we have no reading" is a
 * safety problem, not a cosmetic one.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  ErrorState,
  LevelBadge,
  Metric,
  Unavailable,
  formatDepth,
  formatNumber,
  formatTimestamp,
  levelColour,
} from "./primitives";

describe("Metric", () => {
  it("renders a real value with its unit", () => {
    render(<Metric label="Bit depth" value={1204.5} unit="m" />);
    expect(screen.getByText("1,204.5")).toBeDefined();
    expect(screen.getByText("m")).toBeDefined();
  });

  it("shows an explicit message instead of zero when the value is null", () => {
    render(<Metric label="Bit depth" value={null} unit="m" />);
    expect(screen.getByText("Not available")).toBeDefined();
    expect(screen.queryByText("0")).toBeNull();
  });

  it("shows an explicit message when the value is undefined", () => {
    render(<Metric label="Formation" value={undefined} />);
    expect(screen.getByText("Not available")).toBeDefined();
  });

  it("uses a caller-supplied reason so the cause of absence is specific", () => {
    render(
      <Metric label="Risk" value={null} unavailableReason="Not evaluated at this depth" />,
    );
    expect(screen.getByText("Not evaluated at this depth")).toBeDefined();
  });

  it("renders a genuine zero, which is different from missing", () => {
    render(<Metric label="Flow in" value={0} unit="L/min" />);
    expect(screen.getByText("0")).toBeDefined();
    expect(screen.queryByText("Not available")).toBeNull();
  });
});

describe("state components", () => {
  it("surfaces the backend message on error rather than a generic one", () => {
    render(<ErrorState error={new Error("Well 42 has no surveyed position")} />);
    expect(screen.getByText("Well 42 has no surveyed position")).toBeDefined();
  });

  it("distinguishes unavailable from error", () => {
    render(<Unavailable reason="No analogue wells could be ranked." hint="Nothing inferred." />);
    expect(screen.getByText("No analogue wells could be ranked.")).toBeDefined();
    expect(screen.getByText("Nothing inferred.")).toBeDefined();
  });
});

describe("LevelBadge", () => {
  it("renders the level vocabulary the backend sends", () => {
    for (const level of ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]) {
      const { unmount } = render(<LevelBadge level={level} />);
      expect(screen.getByText(level)).toBeDefined();
      unmount();
    }
  });

  it("gives each level a distinct colour", () => {
    const colours = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"].map(levelColour);
    expect(new Set(colours).size).toBe(5);
  });

  it("falls back to a neutral colour for an unknown level", () => {
    expect(levelColour("SOMETHING_NEW")).toBe("#63788f");
  });
});

describe("formatting", () => {
  it("formats depths and marks missing ones with a dash, not zero", () => {
    expect(formatDepth(1204.53)).toBe("1,204.5 m");
    expect(formatDepth(null)).toBe("—");
    expect(formatDepth(undefined)).toBe("—");
  });

  it("does not render NaN or Infinity as a number", () => {
    expect(formatNumber(Number.NaN)).toBe("—");
    expect(formatNumber(Number.POSITIVE_INFINITY)).toBe("—");
  });

  it("keeps large counts readable", () => {
    expect(formatNumber(86800)).toBe("86,800");
  });

  it("handles timestamps and missing timestamps", () => {
    expect(formatTimestamp("2016-09-30T10:04:00Z")).toBe("2016-09-30 10:04:00");
    expect(formatTimestamp(null)).toBe("—");
  });
});
