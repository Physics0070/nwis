/**
 * Active well dashboard.
 *
 * Current depth, formation, live telemetry, offset map, ranked analogues and the risk
 * state all come from the backend. The current depth used for context is the replayed
 * bit depth, so the analogue and risk panels follow the bit as it moves.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { useTelemetryStream } from "../hooks/useTelemetryStream";
import TelemetryChart, { type ChannelSpec } from "../components/TelemetryChart";
import WellMap from "../components/WellMap";
import {
  ContributionBar,
  ErrorState,
  LevelBadge,
  Loading,
  Metric,
  Panel,
  Tag,
  Unavailable,
  formatDepth,
  formatNumber,
  formatTimestamp,
} from "../components/primitives";

// Channel display is configuration, not data: units and labels describe what the
// backend already normalised.
const CHANNELS: ChannelSpec[] = [
  { key: "standpipe_pressure_bar", label: "Standpipe pressure", unit: "bar", colour: "#2f9dd6" },
  { key: "hookload_kn", label: "Hookload", unit: "kN", colour: "#d99b34" },
  { key: "flow_in_lpm", label: "Flow in", unit: "L/min", colour: "#3fa87a" },
  { key: "surface_torque_knm", label: "Surface torque", unit: "kN·m", colour: "#e2703a" },
];

// How far the bit must move before geological context is re-queried.
const DEPTH_CONTEXT_STEP_M = 25;

export default function ActiveWell() {
  const { wellId } = useParams();
  const id = Number(wellId);
  const [speed, setSpeed] = useState<number>(600);

  const well = useQuery({ queryKey: ["well", id], queryFn: () => api.getWell(id) });
  const { samples, latest, replayState, connection, lastError, setReplayState } =
    useTelemetryStream(Number.isFinite(id) ? id : null);

  // Depth context follows the bit as the replay advances, but is quantised to a step.
  // Re-querying analogues and risk on every 10-second sample would refetch continuously
  // and leave those panels permanently in a loading state, which is worse than useless.
  // The bit has to move a meaningful distance before the geological context can change.
  const currentDepth = latest?.bit_depth_m ?? replayState?.current_depth_m ?? null;
  const [contextDepth, setContextDepth] = useState<number | null>(null);
  useEffect(() => {
    if (currentDepth === null) return;
    const quantised = Math.round(currentDepth / DEPTH_CONTEXT_STEP_M) * DEPTH_CONTEXT_STEP_M;
    setContextDepth((previous) => (previous === quantised ? previous : quantised));
  }, [currentDepth]);

  const nearby = useQuery({
    queryKey: ["nearby", id],
    queryFn: () => api.nearby(id, undefined, 40),
    enabled: Number.isFinite(id),
  });

  const analogues = useQuery({
    queryKey: ["analogues", id, contextDepth],
    queryFn: () => api.analogues(id, contextDepth),
    enabled: Number.isFinite(id),
    placeholderData: (previous) => previous,
  });

  const risk = useQuery({
    queryKey: ["risk", id, contextDepth],
    queryFn: () => api.risk(id, contextDepth),
    enabled: Number.isFinite(id),
    placeholderData: (previous) => previous,
  });

  const formations = useQuery({
    queryKey: ["formations", id],
    queryFn: () => api.formations(id),
    enabled: Number.isFinite(id),
  });

  const analogueNames = useMemo(
    () => new Set((analogues.data?.matches ?? []).map((m) => m.well.name)),
    [analogues.data],
  );

  const currentFormation = useMemo(() => {
    if (!formations.data || contextDepth === null) return null;
    return (
      formations.data.find(
        (f) => f.depth_top_m <= contextDepth && f.depth_base_m >= contextDepth,
      ) ?? null
    );
  }, [formations.data, contextDepth]);

  async function control(action: "start" | "pause" | "resume" | "stop") {
    const map = {
      start: () => api.replayStart(id, speed),
      pause: () => api.replayPause(id),
      resume: () => api.replayResume(id),
      stop: () => api.replayStop(id),
    };
    setReplayState(await map[action]());
  }

  if (well.isLoading) return <Loading label="Loading well" />;
  if (well.isError) return <ErrorState error={well.error} onRetry={well.refetch} />;
  const activeWell = well.data!;

  return (
    <div className="space-y-4">
      {/* header */}
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold text-ink-primary">{activeWell.name}</h1>
        {activeWell.field_name && <Tag>{activeWell.field_name}</Tag>}
        {activeWell.source_dataset && <Tag>{activeWell.source_dataset}</Tag>}
        {replayState && (
          <span className="ml-auto text-xs text-ink-muted">
            Telemetry source: <span className="text-ink-secondary">{replayState.source}</span>
          </span>
        )}
      </div>

      {/* KPIs, all backend-derived */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="Bit depth"
          value={currentDepth}
          unit="m"
          unavailableReason="Awaiting telemetry"
        />
        <Metric
          label="Formation at depth"
          value={currentFormation?.formation_name ?? currentFormation?.group_name ?? null}
          unavailableReason={
            formations.data && formations.data.length === 0
              ? "No stratigraphy recorded for this well"
              : "Not resolved at this depth"
          }
        />
        <Metric
          label="Risk indicator"
          value={risk.data ? risk.data.risk_level : null}
          hint={risk.data ? `score ${formatNumber(risk.data.score, 3)}` : undefined}
          unavailableReason="Not evaluated"
        />
        <Metric
          label="Replay position"
          value={
            replayState
              ? `${replayState.current_index.toLocaleString()} / ${replayState.total_samples.toLocaleString()}`
              : null
          }
          hint={replayState ? formatTimestamp(replayState.current_timestamp) : undefined}
          unavailableReason="Replay not started"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-5">
        {/* map */}
        <Panel
          title="Offset wells"
          subtitle="Positions from the database; proximity computed server-side"
          className="lg:col-span-3"
        >
          <div className="h-[340px]">
            {nearby.isLoading && <Loading label="Loading offset wells" />}
            {nearby.isError && <ErrorState error={nearby.error} onRetry={nearby.refetch} />}
            {nearby.data && (
              <WellMap
                activeWell={activeWell}
                nearby={nearby.data}
                analogueNames={analogueNames}
              />
            )}
          </div>
        </Panel>

        {/* replay controls + risk */}
        <div className="space-y-4 lg:col-span-2">
          <Panel
            title="Telemetry replay"
            subtitle={`connection: ${connection}`}
          >
            {lastError && <p className="mb-2 text-xs text-state-bad">{lastError}</p>}
            <div className="flex flex-wrap gap-2">
              {(["start", "pause", "resume", "stop"] as const).map((action) => (
                <button
                  key={action}
                  type="button"
                  onClick={() => control(action)}
                  className="rounded-pill border border-surface-border px-3 py-1 text-xs capitalize text-ink-secondary hover:bg-surface-hover"
                >
                  {action}
                </button>
              ))}
            </div>
            <label className="mt-3 block text-xs text-ink-muted">
              Replay speed: {speed}× real time
              <input
                type="range"
                min={1}
                max={3600}
                step={1}
                value={speed}
                onChange={(event) => setSpeed(Number(event.target.value))}
                onMouseUp={() => api.replaySpeed(id, speed).then(setReplayState)}
                className="mt-1 w-full accent-accent"
              />
            </label>
            {replayState && (
              <dl className="mt-3 space-y-1 border-t border-surface-border pt-2 text-xs">
                <div className="flex justify-between">
                  <dt className="text-ink-muted">status</dt>
                  <dd className="text-ink-secondary">{replayState.status}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-muted">simulated time</dt>
                  <dd className="font-mono text-ink-secondary">
                    {formatTimestamp(replayState.current_timestamp)}
                  </dd>
                </div>
              </dl>
            )}
          </Panel>

          <Panel title="Risk state" subtitle="Evaluated at the current bit depth">
            {risk.isLoading && <Loading />}
            {risk.isError && <ErrorState error={risk.error} onRetry={risk.refetch} />}
            {risk.data && (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <LevelBadge level={risk.data.risk_level} />
                  <span className="font-mono text-sm text-ink-primary">
                    {formatNumber(risk.data.score, 3)}
                  </span>
                  <span className="ml-auto text-[11px] text-ink-muted">
                    mode: {risk.data.mode}
                  </span>
                </div>

                {/* Absence of a probability is shown explicitly, never as 0. */}
                <p className="text-xs text-ink-muted">
                  {risk.data.probability === null
                    ? "No calibrated probability: this is a historical risk indicator, not a supervised prediction."
                    : `Calibrated probability ${formatNumber(risk.data.probability, 3)}`}
                </p>

                <div className="space-y-2">
                  {risk.data.components.map((component) => (
                    <div key={component.name}>
                      <div className="flex justify-between text-xs">
                        <span className="text-ink-secondary">{component.name}</span>
                        <span className="font-mono text-ink-primary">
                          {formatNumber(component.value, 3)}
                          <span className="ml-1 text-ink-muted">×{component.weight}</span>
                        </span>
                      </div>
                      <ContributionBar value={component.value} weight={component.weight} />
                      <p className="mt-0.5 text-[11px] text-ink-muted">
                        {component.explanation}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Panel>
        </div>
      </div>

      {/* telemetry chart */}
      <Panel
        title="Live telemetry"
        subtitle="Replayed samples with their original timestamps"
      >
        <TelemetryChart samples={samples} channels={CHANNELS} height={220} />
      </Panel>

      {/* analogues */}
      <Panel
        title="Contextual analogue wells"
        subtitle={
          analogues.data
            ? `Ranked at ${formatDepth(analogues.data.query_depth_m)} using weights ${Object.entries(
                analogues.data.weights,
              )
                .map(([k, v]) => `${k} ${v}`)
                .join(", ")}`
            : undefined
        }
      >
        {analogues.isLoading && <Loading label="Ranking analogues" />}
        {analogues.isError && (
          <ErrorState error={analogues.error} onRetry={analogues.refetch} />
        )}
        {analogues.data && analogues.data.matches.length === 0 && (
          <Unavailable reason="No analogue wells could be ranked for this depth." />
        )}
        {analogues.data && analogues.data.matches.length > 0 && (
          <ul className="space-y-3">
            {analogues.data.matches.map((match) => (
              <li
                key={match.well.id}
                className="rounded-card border border-surface-border bg-surface-overlay p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    to={`/wells/${match.well.id}`}
                    className="text-sm font-medium text-ink-primary hover:text-accent-strong"
                  >
                    {match.well.name}
                  </Link>
                  <span className="font-mono text-xs text-accent-strong">
                    {formatNumber(match.score, 4)}
                  </span>
                  {match.dominant_formation && <Tag>{match.dominant_formation}</Tag>}
                  {match.distance_km !== null && (
                    <span className="text-xs text-ink-muted">
                      {formatNumber(match.distance_km, 1)} km
                    </span>
                  )}
                </div>

                <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                  {match.components.map((component) => (
                    <div key={component.name} className="text-[11px]">
                      <div className="flex justify-between">
                        <span className="text-ink-secondary">{component.name}</span>
                        <span className="font-mono text-ink-primary">
                          {formatNumber(component.value, 3)}
                        </span>
                      </div>
                      <ContributionBar value={component.value} weight={component.weight} />
                    </div>
                  ))}
                </div>

                {match.dimensions_unavailable.length > 0 && (
                  <p className="mt-2 text-[11px] text-ink-muted">
                    Not comparable on: {match.dimensions_unavailable.join(", ")} — remaining
                    weights renormalised.
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {/* historical evidence */}
      <Panel
        title="Historical evidence near this depth"
        subtitle="Records retrieved from analogue wells"
      >
        {risk.data && risk.data.historical_evidence.length === 0 && (
          <Unavailable
            reason="No historical events found within the look-ahead window."
            hint="Nothing is inferred in the absence of evidence."
          />
        )}
        {risk.data && risk.data.historical_evidence.length > 0 && (
          <ul className="divide-y divide-surface-border">
            {risk.data.historical_evidence.slice(0, 8).map((evidence, index) => (
              <li key={`${evidence.event_id}-${index}`} className="py-2.5">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-medium text-ink-primary">{evidence.well_name}</span>
                  <Tag>similarity {formatNumber(evidence.similarity_score, 3)}</Tag>
                  <span className="text-ink-muted">
                    {formatDepth(evidence.depth_start_m)}
                    {evidence.distance_from_bit_m !== null &&
                      ` (${evidence.distance_from_bit_m > 0 ? "+" : ""}${formatNumber(
                        evidence.distance_from_bit_m,
                        0,
                      )} m from bit)`}
                  </span>
                </div>
                <p className="mt-1 text-sm text-ink-secondary">{evidence.description}</p>
                <p className="mt-0.5 text-[11px] text-ink-muted">
                  source: {evidence.source_dataset ?? "unknown"} · depth via{" "}
                  {evidence.depth_source ?? "as recorded"}
                  {evidence.mitigations.length === 0 &&
                    " · no validated historical mitigation recorded"}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
