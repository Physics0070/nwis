/**
 * Historical risk radar.
 *
 * Answers one question the evidence list alone could not: **is the bit approaching a
 * depth at which something went wrong in a comparable well?**
 *
 * Every element is positioned from data the risk engine already returned. The axis spans
 * the engine's own look-ahead window (`risk.lookahead_m`, served through `/api/status`),
 * so the picture cannot disagree with the query that produced the evidence. Each marker
 * sits at `distance_from_bit_m` — the signed depth offset between the historical event
 * and the current bit depth. Positive is deeper, which is where the bit is going.
 *
 * Nothing here is interpolated, smoothed or assumed. An event without a recorded depth is
 * not drawn, because a marker at an invented depth is worse than no marker.
 */
import type { HistoricalEvidence } from "../lib/api";

interface Props {
  bitDepthM: number | null;
  /** The risk engine's look-ahead window, in metres. Sets the axis extent. */
  lookaheadM: number | null;
  evidence: HistoricalEvidence[];
  onSelect?: (item: HistoricalEvidence) => void;
}

const AXIS_TOP = 18;
const AXIS_HEIGHT = 132;
const AXIS_X = 74;
const VIEW_WIDTH = 420;

/**
 * How many markers the axis will draw.
 *
 * A layout constant, not a threshold on the data. A single depth in the Volve recordings
 * can carry sixty-odd remarks; drawing them all turns the radar into a wall of identical
 * labels and hides the one thing it exists to show. The nearest records are drawn and the
 * remainder is counted, so nothing is dropped silently.
 */
const MAX_MARKERS = 7;

/** Plain-language relation between a historical event depth and the bit. */
export function relationToBit(offsetM: number): string {
  const rounded = Math.round(offsetM);
  if (rounded === 0) return "at the bit";
  return rounded > 0 ? `${rounded} m ahead` : `${Math.abs(rounded)} m behind`;
}

/**
 * The nearest event the bit has not yet reached.
 *
 * Exported for testing: "ahead" is the whole point of the radar, and an off-by-one on
 * the sign would turn a warning into a reassurance.
 */
export function nearestAhead(
  evidence: HistoricalEvidence[],
): HistoricalEvidence | null {
  const ahead = evidence.filter(
    (e) => e.distance_from_bit_m != null && e.distance_from_bit_m > 0,
  );
  if (!ahead.length) return null;
  return ahead.reduce((best, item) =>
    (item.distance_from_bit_m as number) < (best.distance_from_bit_m as number)
      ? item
      : best,
  );
}

export default function DepthRadar({
  bitDepthM,
  lookaheadM,
  evidence,
  onSelect,
}: Props) {
  if (bitDepthM == null || lookaheadM == null || lookaheadM <= 0) {
    return (
      <p className="text-xs text-ink-muted">
        No data available — the radar needs a reported bit depth and the engine's
        look-ahead window.
      </p>
    );
  }

  const placed = evidence.filter((e) => e.distance_from_bit_m != null);
  const warning = nearestAhead(placed);
  // Nearest first, so what is drawn is what matters most to the bit right now.
  const byProximity = [...placed].sort(
    (a, b) =>
      Math.abs(a.distance_from_bit_m as number) - Math.abs(b.distance_from_bit_m as number),
  );
  const drawn = byProximity.slice(0, MAX_MARKERS);
  const undrawn = byProximity.length - drawn.length;

  // Offset -lookahead .. +lookahead maps onto the axis, bit exactly at the centre.
  const y = (offsetM: number) =>
    AXIS_TOP + ((offsetM + lookaheadM) / (2 * lookaheadM)) * AXIS_HEIGHT;
  const bitY = y(0);

  return (
    <div className="space-y-2">
      {warning ? (
        <p className="rounded-card border border-state-warn/40 bg-state-warn/10 px-3 py-2 text-[11px] text-ink-secondary">
          <span className="font-semibold text-state-warn">
            Historical risk interval {relationToBit(warning.distance_from_bit_m as number)}
          </span>{" "}
          — {warning.event_type} recorded in {warning.well_name} at{" "}
          {Math.round(warning.depth_start_m as number)} m
          {warning.formation_name ? ` (${warning.formation_name})` : ""}.
        </p>
      ) : (
        <p className="text-[11px] text-ink-muted">
          No historical event is recorded ahead of the bit inside the {lookaheadM} m
          look-ahead window. That is an absence of matching records, not an all-clear.
        </p>
      )}

      <svg
        viewBox={`0 0 ${VIEW_WIDTH} ${AXIS_TOP + AXIS_HEIGHT + 20}`}
        className="w-full"
        role="img"
        aria-label="Depth radar: historical risk intervals relative to the current bit depth"
      >
        {/* look-ahead window */}
        <line
          x1={AXIS_X}
          y1={AXIS_TOP}
          x2={AXIS_X}
          y2={AXIS_TOP + AXIS_HEIGHT}
          stroke="#243141"
          strokeWidth="2"
        />
        <text x={AXIS_X - 8} y={AXIS_TOP + 4} textAnchor="end" className="fill-ink-muted" style={{ fontSize: 9 }}>
          {Math.round(bitDepthM - lookaheadM)} m
        </text>
        <text
          x={AXIS_X - 8}
          y={AXIS_TOP + AXIS_HEIGHT + 4}
          textAnchor="end"
          className="fill-ink-muted"
          style={{ fontSize: 9 }}
        >
          {Math.round(bitDepthM + lookaheadM)} m
        </text>

        {/* the bit */}
        <g style={{ transition: "transform 700ms linear" }}>
          <circle cx={AXIS_X} cy={bitY} r={5} fill="#4fb4e6" />
          <text
            x={AXIS_X - 8}
            y={bitY + 3}
            textAnchor="end"
            className="fill-ink-primary font-mono"
            style={{ fontSize: 10 }}
          >
            {bitDepthM.toFixed(0)} m
          </text>
        </g>

        {/* historical risk intervals at their real depth offsets */}
        {drawn.map((item, index) => {
          const offset = item.distance_from_bit_m as number;
          const ey = y(Math.max(-lookaheadM, Math.min(lookaheadM, offset)));
          const ahead = offset > 0;
          return (
            <g
              key={`${item.event_id}-${index}`}
              onClick={() => onSelect?.(item)}
              style={{ cursor: onSelect ? "pointer" : "default" }}
            >
              <title>{`${item.event_type} — ${item.well_name} @ ${item.depth_start_m} m (${relationToBit(offset)})`}</title>
              <line
                x1={AXIS_X}
                y1={ey}
                x2={AXIS_X + 18}
                y2={ey}
                stroke={ahead ? "#d99b34" : "#3d566d"}
                strokeWidth="1"
              />
              <circle cx={AXIS_X + 18} cy={ey} r={3.5} fill={ahead ? "#d99b34" : "#3d566d"} />
              <text
                x={AXIS_X + 26}
                y={ey + 3}
                className={ahead ? "fill-ink-primary" : "fill-ink-muted"}
                style={{ fontSize: 9 }}
              >
                {item.well_name} · {item.event_type} · {relationToBit(offset)}
              </text>
            </g>
          );
        })}
      </svg>

      {undrawn > 0 && (
        <p className="text-[10px] text-ink-muted">
          {drawn.length} of {byProximity.length} records shown, nearest to the bit first.
          The other {undrawn} lie inside the same window and are counted in the risk
          component.
        </p>
      )}
    </div>
  );
}
