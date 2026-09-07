/**
 * Depth-synchronised drilling visualisation.
 *
 * A vertical borehole track. The bit marker sits at the replayed bit depth; formation
 * bands and historical event markers are placed at their own recorded depths, so their
 * position relative to the bit is a real spatial relationship rather than decoration.
 *
 * Everything drawn here is passed in from the API. When a depth is unknown the element
 * is not drawn at all — a marker at an invented depth would be worse than no marker.
 */
import type { DrillingEvent, FormationInterval } from "../lib/api";

interface Props {
  bitDepthM: number | null;
  totalDepthM: number | null;
  formations: FormationInterval[];
  events: DrillingEvent[];
  onSelectEvent?: (event: DrillingEvent) => void;
}

const TRACK_TOP = 28;
const TRACK_HEIGHT = 300;
const TRACK_X = 92;
const TRACK_WIDTH = 26;

/** Formation bands cycle through muted strata tones; index only, no meaning encoded. */
const BAND_TONES = ["#22323f", "#1b2a35", "#263947", "#1e303c"];

export default function DrillTrack({
  bitDepthM,
  totalDepthM,
  formations,
  events,
  onSelectEvent,
}: Props) {
  // The scale needs a floor: at spud, bit depth and total depth are both 0 and every
  // element would collapse onto one pixel.
  const deepest = Math.max(
    totalDepthM ?? 0,
    bitDepthM ?? 0,
    ...formations.map((f) => f.depth_base_m ?? 0),
    ...events.map((e) => e.depth_end_m ?? e.depth_start_m ?? 0),
  );
  const scaleMax = deepest > 0 ? deepest * 1.05 : 1;
  const y = (depth: number) => TRACK_TOP + (depth / scaleMax) * TRACK_HEIGHT;

  const bitY = bitDepthM != null ? y(bitDepthM) : null;
  const placedEvents = events.filter((e) => e.depth_start_m != null);

  return (
    <svg
      viewBox={`0 0 420 ${TRACK_TOP + TRACK_HEIGHT + 34}`}
      className="w-full"
      role="img"
      aria-label="Borehole depth track showing bit position, formations and historical events"
    >
      {/* formation bands */}
      {formations.map((f, i) =>
        f.depth_top_m == null || f.depth_base_m == null ? null : (
          <g key={`${f.formation_name}-${f.depth_top_m}-${i}`}>
            <rect
              x={TRACK_X}
              y={y(f.depth_top_m)}
              width={TRACK_WIDTH}
              height={Math.max(1, y(f.depth_base_m) - y(f.depth_top_m))}
              fill={BAND_TONES[i % BAND_TONES.length]}
            />
            <text
              x={TRACK_X - 10}
              y={y(f.depth_top_m) + 4}
              textAnchor="end"
              className="fill-ink-muted"
              style={{ fontSize: 9 }}
            >
              {f.formation_name}
            </text>
          </g>
        ),
      )}

      {/* borehole outline */}
      <rect
        x={TRACK_X}
        y={TRACK_TOP}
        width={TRACK_WIDTH}
        height={TRACK_HEIGHT}
        fill="none"
        stroke="#243141"
        strokeWidth="1"
      />

      {/* drilled interval */}
      {bitY != null && (
        <rect
          x={TRACK_X}
          y={TRACK_TOP}
          width={TRACK_WIDTH}
          height={Math.max(0, bitY - TRACK_TOP)}
          fill="#12303f"
        />
      )}

      {/* drill string + bit */}
      {bitY != null && (
        <>
          <line
            x1={TRACK_X + TRACK_WIDTH / 2}
            y1={TRACK_TOP}
            x2={TRACK_X + TRACK_WIDTH / 2}
            y2={bitY}
            stroke="#4fb4e6"
            strokeWidth="2"
            style={{ transition: "y2 700ms linear" }}
          />
          <g style={{ transition: "transform 700ms linear" }} transform={`translate(0 ${bitY})`}>
            <rect
              x={TRACK_X + TRACK_WIDTH / 2 - 7}
              y={-5}
              width={14}
              height={10}
              rx={2}
              fill="#4fb4e6"
            />
            <text
              x={TRACK_X + TRACK_WIDTH + 14}
              y={4}
              className="fill-ink-primary font-mono"
              style={{ fontSize: 11 }}
            >
              {bitDepthM!.toFixed(1)} m
            </text>
          </g>
        </>
      )}

      {/* historical events at their recorded depths */}
      {placedEvents.map((e) => {
        const ey = y(e.depth_start_m as number);
        return (
          <g
            key={e.id}
            onClick={() => onSelectEvent?.(e)}
            style={{ cursor: onSelectEvent ? "pointer" : "default" }}
          >
            <title>{`${e.event_type} @ ${e.depth_start_m} m — ${e.description}`}</title>
            <circle cx={TRACK_X + TRACK_WIDTH + 78} cy={ey} r={3.5} fill="#d99b34" />
            <line
              x1={TRACK_X + TRACK_WIDTH}
              y1={ey}
              x2={TRACK_X + TRACK_WIDTH + 74}
              y2={ey}
              stroke="#243141"
              strokeWidth="1"
            />
          </g>
        );
      })}

      <text x={TRACK_X - 10} y={TRACK_TOP - 10} textAnchor="end" className="fill-ink-muted" style={{ fontSize: 9 }}>
        surface
      </text>
      {placedEvents.length > 0 && (
        <text
          x={TRACK_X + TRACK_WIDTH + 90}
          y={TRACK_TOP - 10}
          className="fill-ink-muted"
          style={{ fontSize: 9 }}
        >
          recorded events
        </text>
      )}
    </svg>
  );
}
