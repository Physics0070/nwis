/**
 * Drilling simulator.
 *
 * A replay of **real recorded telemetry**, presented as a live drilling session. Nothing
 * on this page is generated: the samples are rows the backend stored, the depth is the
 * recorded bit depth, and the intelligence panel is the existing risk and analogue
 * engines queried at that depth. The top bar says REPLAY MODE for exactly that reason.
 *
 * Layout is deliberately flat — top bar, borehole, a short telemetry strip, one
 * intelligence panel, controls. Secondary detail lives in the investigation drawer
 * rather than in more cards.
 *
 * Every threshold and window used for rendering comes from `/api/status`, not from a
 * constant in this file: the depth quantisation step, the offered replay speeds and the
 * risk engine's look-ahead window are all the backend's own configured values.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  type DrillingEvent,
  type HistoricalEvidence,
  type UiConfig,
  type Well,
} from "../lib/api";
import { useTelemetryStream } from "../hooks/useTelemetryStream";
import DrillTrack from "../components/DrillTrack";
import DepthRadar, { relationToBit } from "../components/DepthRadar";
import {
  ErrorState,
  LevelBadge,
  Loading,
  Panel,
  Tag,
  Unavailable,
  formatNumber,
  formatTimestamp,
} from "../components/primitives";

/** Which channels lead the telemetry strip. Labels and units, not data. */
const PRIMARY_CHANNELS: Array<{ key: string; label: string; unit: string }> = [
  { key: "standpipe_pressure_bar", label: "Standpipe", unit: "bar" },
  { key: "hookload_kn", label: "Hookload", unit: "kN" },
  { key: "flow_in_lpm", label: "Flow in", unit: "L/min" },
  { key: "surface_torque_knm", label: "Torque", unit: "kN·m" },
  { key: "bit_rpm", label: "Bit RPM", unit: "rpm" },
  { key: "rop_m_per_hr", label: "ROP", unit: "m/hr" },
];

/**
 * Snap the bit depth to the context grid, or keep the previous value.
 *
 * `stepM` is the backend's `ui.depth_context_step_m`. Exported so it can be tested:
 * re-querying risk and analogues on every 10-second sample leaves both panels
 * permanently loading, which is worse than slightly stale context.
 */
export function nextContextDepth(
  previous: number | null,
  bitDepth: number,
  stepM: number,
): number {
  if (previous != null && Math.abs(bitDepth - previous) < stepM) {
    return previous;
  }
  return Math.round(bitDepth / stepM) * stepM;
}

/**
 * Describe where a historical event sits relative to the bit, in DEPTH.
 *
 * `distance_from_bit_m` is `event.depth_start_m - current_bit_depth` — a vertical
 * offset, not a distance between wells. Labelling it "N m away" read as though the
 * analogue well were that far away geographically, which for a well 8 km off is simply
 * false. The evidence is relevant because it happened at a comparable *depth*.
 */
export function depthRelationToBit(offsetM: number): string {
  const rounded = Math.round(offsetM);
  if (rounded === 0) return "same depth";
  return rounded > 0 ? `${rounded} m deeper` : `${Math.abs(rounded)} m shallower`;
}

/** A value that is genuinely absent is stated, never rendered as a zero. */
function Value({
  value,
  unit,
  digits = 1,
}: {
  value: number | null | undefined;
  unit?: string;
  digits?: number;
}) {
  if (value == null) {
    return <span className="text-xs text-ink-muted">No data available</span>;
  }
  return (
    <span className="font-mono text-sm text-ink-primary">
      {formatNumber(value, digits)}
      {unit && <span className="ml-1 font-sans text-[11px] text-ink-secondary">{unit}</span>}
    </span>
  );
}

export default function Simulator() {
  const [wellId, setWellId] = useState<number | null>(null);
  const [speed, setSpeed] = useState<number | null>(null);
  const [investigating, setInvestigating] = useState<HistoricalEvidence | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<DrillingEvent | null>(null);
  const [actionNote, setActionNote] = useState("");
  const [engineer, setEngineer] = useState("");
  const [actionSaved, setActionSaved] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [seekDraft, setSeekDraft] = useState<number | null>(null);
  // Whether *this drawer* is what paused the replay, so closing it restores what the
  // engineer interrupted instead of resuming a replay they paused deliberately. State
  // rather than a ref because the drawer renders it.
  const [pausedByInvestigation, setPausedByInvestigation] = useState(false);

  // Thresholds and windows are the backend's, fetched once. Panels that need them wait
  // rather than falling back to a number invented here.
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const config: UiConfig | undefined = status.data?.config;

  // Only wells that actually have recorded telemetry can be replayed.
  const wells = useQuery({
    queryKey: ["sim-wells"],
    queryFn: () => api.listWells({ hasTelemetry: true, limit: 50 }),
  });

  const { latest, replayState, connection, lastError, setReplayState } =
    useTelemetryStream(wellId);

  const bitDepth = latest?.bit_depth_m ?? replayState?.current_depth_m ?? null;

  // Quantised so the intelligence panel does not refetch on every 10-second sample.
  const [contextDepth, setContextDepth] = useState<number | null>(null);
  useEffect(() => {
    if (bitDepth == null || config == null) return;
    setContextDepth((previous) =>
      nextContextDepth(previous, bitDepth, config.depth_context_step_m),
    );
  }, [bitDepth, config]);

  const enabled = wellId != null;
  // The live session persists what it evaluates. Without this the engine never records
  // an assessment and never raises an alert, so the alert inbox stays empty and an
  // engineer has nothing to attach a decision to. Cooldown and depth deduplication in
  // the risk engine are what stop this becoming alert spam.
  const risk = useQuery({
    queryKey: ["sim-risk", wellId, contextDepth],
    queryFn: () => api.risk(wellId as number, contextDepth, null, { persist: true }),
    enabled: enabled && contextDepth != null,
  });
  const analogues = useQuery({
    queryKey: ["sim-analogues", wellId, contextDepth],
    queryFn: () => api.analogues(wellId as number, contextDepth),
    enabled: enabled && contextDepth != null,
  });
  const formations = useQuery({
    queryKey: ["sim-formations", wellId],
    queryFn: () => api.formations(wellId as number),
    enabled,
  });
  const events = useQuery({
    queryKey: ["sim-events", wellId],
    queryFn: () => api.events(wellId as number),
    enabled,
  });

  const controls = useMemo(() => {
    if (wellId == null) return null;
    return {
      start: () => api.replayStart(wellId, speed ?? undefined).then(setReplayState),
      pause: () => api.replayPause(wellId).then(setReplayState),
      resume: () => api.replayResume(wellId).then(setReplayState),
      stop: () => api.replayStop(wellId).then(setReplayState),
      seek: (index: number) => api.replaySeek(wellId, index).then(setReplayState),
    };
  }, [wellId, speed, setReplayState]);

  const changeSpeed = useCallback(
    (next: number) => {
      setSpeed(next);
      if (wellId != null) api.replaySpeed(wellId, next).then(setReplayState).catch(() => undefined);
    },
    [wellId, setReplayState],
  );

  const status_ = replayState?.status ?? "stopped";
  const finished = status_ === "finished";
  const progress =
    replayState && replayState.total_samples > 0
      ? replayState.current_index / replayState.total_samples
      : 0;

  const selectedWell: Well | undefined = wells.data?.items.find((w) => w.id === wellId);
  const speeds = config?.replay_speeds ?? [];
  const activeSpeed = speed ?? replayState?.speed ?? null;

  /**
   * Open the investigation and hold the replay where it is.
   *
   * The evidence in the drawer was retrieved for one depth. Letting the bit keep moving
   * behind it would leave the engineer reading an explanation of a depth the well has
   * already passed.
   */
  const investigate = useCallback(
    async (item: HistoricalEvidence) => {
      setInvestigating(item);
      if (status_ === "running") {
        setPausedByInvestigation(true);
        await controls?.pause().catch(() => undefined);
      }
    },
    [controls, status_],
  );

  const closeInvestigation = useCallback(async () => {
    setInvestigating(null);
    if (pausedByInvestigation) {
      setPausedByInvestigation(false);
      await controls?.resume().catch(() => undefined);
    }
  }, [controls, pausedByInvestigation]);

  async function recordAction(decision: string) {
    if (wellId == null) return;
    setActionError(null);
    try {
      // Engineer actions attach to an alert. Use the well's most recent one; if the well
      // has none, say so rather than inventing an alert id.
      const alerts = await api.wellAlerts(wellId);
      if (!alerts.length) {
        setActionError(
          "No alert has been raised for this well yet, so there is nothing to attach a decision to.",
        );
        return;
      }
      const alert = alerts[0];
      await api.recordAction({
        alert_id: alert.id,
        decision,
        action_description: actionNote || undefined,
        engineer_name: engineer || undefined,
      });
      // Name the alert the decision was filed against. It is the well's most recent one,
      // which is not necessarily the depth on screen, and an engineer should be able to
      // see which record they just wrote to rather than assume.
      setActionSaved(
        `Recorded as "${decision}" against alert #${alert.id} (${alert.title}). ` +
          "Stored for future training; nothing retrains automatically.",
      );
      setActionNote("");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not record the decision.");
    }
  }

  return (
    <div className="space-y-3">
      {/* ---------------------------------------------------------------- top bar */}
      <div className="flex flex-wrap items-center gap-3 rounded-card border border-surface-border bg-surface-raised px-4 py-3">
        <span className="rounded-pill bg-accent-soft px-3 py-1 text-[11px] font-semibold tracking-wide text-accent-strong">
          REPLAY MODE
        </span>

        <select
          value={wellId ?? ""}
          onChange={(event) => {
            const next = event.target.value ? Number(event.target.value) : null;
            setWellId(next);
            setContextDepth(null);
            setInvestigating(null);
            setActionSaved(null);
            setSeekDraft(null);
            setPausedByInvestigation(false);
          }}
          aria-label="Select well"
          className="rounded-pill border border-surface-border bg-surface-overlay px-3 py-1.5 text-xs text-ink-primary focus:border-accent focus:outline-none"
        >
          <option value="">Select a well…</option>
          {wells.data?.items.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>

        {selectedWell && (
          <span className="text-[11px] text-ink-muted">
            {replayState?.source ?? "recorded telemetry"} · historical Volve telemetry, not
            a live rig feed
          </span>
        )}

        <div className="ml-auto flex items-center gap-3 text-[11px]">
          <span className="text-ink-muted">
            socket:{" "}
            <span
              style={{
                color:
                  connection === "open"
                    ? "#3fa87a"
                    : connection === "connecting"
                      ? "#d99b34"
                      : "#63788f",
              }}
            >
              {connection}
            </span>
          </span>
          <span className="rounded-pill bg-surface-overlay px-2 py-0.5 text-ink-secondary">
            {status_}
          </span>
        </div>
      </div>

      {lastError && connection !== "open" && (
        <div className="rounded-card border border-state-warn/40 bg-state-warn/10 px-3 py-2 text-xs text-ink-secondary">
          {lastError} — the stream retries automatically; nothing below is estimated while
          it is down.
        </div>
      )}

      {wells.isError && <ErrorState error={wells.error} onRetry={() => wells.refetch()} />}
      {status.isError && <ErrorState error={status.error} onRetry={() => status.refetch()} />}

      {wellId == null ? (
        <Panel>
          <Unavailable
            reason="No well selected."
            hint="Choose a well with recorded telemetry to begin the replay. Only wells that actually have stored samples are listed."
          />
        </Panel>
      ) : (
        <>
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            {/* ------------------------------------------------ depth visualisation */}
            <Panel
              title="Borehole"
              subtitle="Bit position, formations and recorded events at their own depths"
            >
              {formations.isPending || events.isPending ? (
                <Loading label="Loading well geometry" />
              ) : (
                <DrillTrack
                  bitDepthM={bitDepth}
                  totalDepthM={selectedWell?.total_depth_md_m ?? null}
                  formations={formations.data ?? []}
                  events={events.data ?? []}
                  onSelectEvent={setSelectedEvent}
                />
              )}
            </Panel>

            {/* ------------------------------------------------ intelligence panel */}
            <Panel
              title="NWIS intelligence"
              subtitle={
                contextDepth != null
                  ? `Evaluated at ${contextDepth} m`
                  : "Starts once the bit reports a depth"
              }
            >
              {contextDepth == null ? (
                <Unavailable reason="Waiting for the first telemetry sample." />
              ) : risk.isPending ? (
                <Loading label="Evaluating" />
              ) : risk.isError ? (
                <ErrorState error={risk.error} onRetry={() => risk.refetch()} />
              ) : !risk.data.evaluated ? (
                <Unavailable
                  reason="Risk could not be evaluated at this depth."
                  hint="No anomaly score, no historical evidence within the look-ahead window, and no measurements to apply rules to. That is an absence of signal, not a low risk."
                />
              ) : (
                <div className="space-y-3">
                  <div className="flex items-center gap-3">
                    <LevelBadge level={risk.data.risk_level} />
                    <span className="font-mono text-metric text-ink-primary">
                      {formatNumber(risk.data.score, 3)}
                    </span>
                    <span className="ml-auto text-[11px] text-ink-muted">
                      mode: {risk.data.mode}
                    </span>
                  </div>

                  {risk.data.probability == null && (
                    <p className="rounded-card border border-dashed border-surface-border px-3 py-2 text-[11px] text-ink-muted">
                      No calibrated probability: this is a historical risk indicator built
                      from an unsupervised anomaly score and analogue evidence, not a
                      supervised prediction.
                    </p>
                  )}

                  <div className="space-y-1">
                    {risk.data.components.map((c) => (
                      <div key={c.name} className="text-xs">
                        <div className="flex items-center justify-between">
                          <span className="text-ink-secondary">
                            {c.name.replace(/_/g, " ")}
                          </span>
                          {c.available ? (
                            <span className="font-mono text-ink-primary">
                              {formatNumber(c.value, 3)}
                            </span>
                          ) : (
                            <span className="text-[11px] text-ink-muted">
                              No data available
                            </span>
                          )}
                        </div>
                        <p className="text-[10px] text-ink-muted">{c.explanation}</p>
                      </div>
                    ))}
                  </div>

                  {/*
                    Which measurements pushed this sample away from normal. Stored by the
                    Isolation Forest at scoring time — the model reports abnormal
                    behaviour and the features behind it, and never names a failure mode.
                  */}
                  {risk.data.contributing_features.length > 0 && (
                    <div>
                      <p className="mb-1 text-[11px] uppercase tracking-wider text-ink-muted">
                        What made this sample unusual
                      </p>
                      <div className="flex flex-wrap gap-1">
                        {risk.data.contributing_features.slice(0, 5).map((f, i) => (
                          <Tag key={i}>
                            {String(f.feature ?? f.name ?? "feature")}
                            {typeof f.contribution === "number"
                              ? ` ${formatNumber(f.contribution as number, 2)}`
                              : ""}
                          </Tag>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* ---------------------------------- historical risk radar */}
                  <div>
                    <p className="mb-1 text-[11px] uppercase tracking-wider text-ink-muted">
                      Historical risk radar
                    </p>
                    <DepthRadar
                      bitDepthM={risk.data.depth_m}
                      lookaheadM={config?.risk_lookahead_m ?? null}
                      evidence={risk.data.historical_evidence}
                      onSelect={investigate}
                    />
                  </div>

                  {/* historical evidence → investigation */}
                  <div>
                    <p className="mb-1 text-[11px] uppercase tracking-wider text-ink-muted">
                      Historical evidence near this depth
                    </p>
                    {risk.data.historical_evidence.length === 0 ? (
                      <p className="text-xs text-ink-muted">No data available</p>
                    ) : (
                      <div className="space-y-1">
                        {risk.data.historical_evidence.slice(0, 3).map((e) => (
                          <button
                            key={e.event_id}
                            type="button"
                            onClick={() => investigate(e)}
                            className="flex w-full items-center gap-2 rounded-card border border-surface-border bg-surface-overlay px-3 py-2 text-left text-xs hover:bg-surface-hover"
                          >
                            <span className="text-ink-primary">{e.well_name}</span>
                            <span className="text-ink-muted">{e.event_type}</span>
                            {e.distance_from_bit_m != null && (
                              <Tag>{depthRelationToBit(e.distance_from_bit_m)}</Tag>
                            )}
                            <span className="ml-auto text-[11px] text-accent-strong">
                              Investigate
                            </span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  {/*
                    Which similarity dimensions actually contributed. This matters more
                    on the replay wells than anywhere else: the Volve wellbores carry
                    telemetry but no wireline logs and no stratigraphy, so geology and
                    formation genuinely cannot be computed against a FORCE candidate and
                    the ranking falls back on the dimensions that can. Saying so is the
                    difference between honest degradation and a score that looks more
                    contextual than it is.
                  */}
                  {analogues.data && analogues.data.matches.length > 0 && (
                    <div className="text-[11px] text-ink-muted">
                      <p>
                        Top analogue: {analogues.data.matches[0].well.name} (score{" "}
                        {formatNumber(analogues.data.matches[0].score, 3)})
                      </p>
                      <p>
                        scored on {analogues.data.matches[0].dimensions_used.join(", ")}
                        {analogues.data.matches[0].dimensions_unavailable.length > 0 && (
                          <>
                            {" · "}
                            <span className="text-state-warn">
                              not available:{" "}
                              {analogues.data.matches[0].dimensions_unavailable.join(", ")}
                            </span>
                          </>
                        )}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </Panel>
          </div>

          {/* -------------------------------------------------- telemetry strip */}
          <Panel title="Current telemetry" subtitle={formatTimestamp(latest?.recorded_at)}>
            {latest == null ? (
              <Unavailable reason="No sample received yet. Press start to begin the replay." />
            ) : (
              <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3 lg:grid-cols-6">
                {PRIMARY_CHANNELS.map((c) => (
                  <div key={c.key}>
                    <p className="text-[10px] uppercase tracking-wider text-ink-muted">
                      {c.label}
                    </p>
                    <Value value={latest.channels?.[c.key] ?? null} unit={c.unit} />
                  </div>
                ))}
              </div>
            )}
          </Panel>

          {/* -------------------------------------------------- controls */}
          <div className="rounded-card border border-surface-border bg-surface-raised px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              {(
                [
                  ["Start", () => controls?.start(), status_ === "running"],
                  ["Pause", () => controls?.pause(), status_ !== "running"],
                  ["Resume", () => controls?.resume(), status_ !== "paused"],
                  ["Stop", () => controls?.stop(), status_ === "stopped"],
                ] as Array<[string, () => void, boolean]>
              ).map(([label, onClick, disabled]) => (
                <button
                  key={label}
                  type="button"
                  onClick={onClick}
                  disabled={disabled}
                  className="rounded-pill border border-surface-border px-4 py-1.5 text-xs text-ink-secondary hover:bg-surface-hover disabled:opacity-35"
                >
                  {label}
                </button>
              ))}
              <button
                type="button"
                onClick={async () => {
                  await controls?.stop();
                  setContextDepth(null);
                  setInvestigating(null);
                  setActionSaved(null);
                  setSeekDraft(null);
                  setPausedByInvestigation(false);
                  await controls?.start();
                }}
                className="rounded-pill border border-surface-border px-4 py-1.5 text-xs text-ink-secondary hover:bg-surface-hover"
              >
                Reset
              </button>

              <div className="ml-auto flex items-center gap-2">
                <span className="text-[11px] text-ink-muted">speed</span>
                {speeds.length === 0 ? (
                  <span className="text-[11px] text-ink-muted">No data available</span>
                ) : (
                  speeds.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => changeSpeed(s)}
                      className={`rounded-pill px-2.5 py-1 text-[11px] ${
                        activeSpeed === s
                          ? "bg-accent-soft text-accent-strong"
                          : "text-ink-muted hover:bg-surface-hover"
                      }`}
                    >
                      {s}×
                    </button>
                  ))
                )}
              </div>
            </div>

            {replayState && replayState.total_samples > 0 && (
              <div className="mt-3">
                {/*
                  Scrub position. Some recordings sit at the surface for a long stretch
                  before the bit moves — F-4 does not leave 0 m until sample 14,952 of
                  59,806 — so a demo needs to be able to move to where the interesting
                  interval is rather than waiting the recording out.
                */}
                <input
                  type="range"
                  min={0}
                  max={replayState.total_samples - 1}
                  value={seekDraft ?? replayState.current_index}
                  aria-label="Replay position"
                  onChange={(event) => setSeekDraft(Number(event.target.value))}
                  onMouseUp={() => {
                    if (seekDraft != null) controls?.seek(seekDraft);
                    setSeekDraft(null);
                  }}
                  onTouchEnd={() => {
                    if (seekDraft != null) controls?.seek(seekDraft);
                    setSeekDraft(null);
                  }}
                  className="h-1 w-full cursor-pointer appearance-none rounded-pill bg-surface-overlay accent-accent"
                  style={{
                    background: `linear-gradient(to right, #2f9dd6 ${progress * 100}%, #1b2733 ${progress * 100}%)`,
                  }}
                />
                <p className="mt-1 font-mono text-[11px] text-ink-muted">
                  {(seekDraft ?? replayState.current_index).toLocaleString()} /{" "}
                  {replayState.total_samples.toLocaleString()} samples ·{" "}
                  {formatTimestamp(replayState.current_timestamp)}
                </p>
              </div>
            )}
          </div>

          {/* -------------------------------------------------- completion summary */}
          {finished && (
            <Panel title="Simulation complete">
              <div className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
                <div>
                  <p className="text-ink-muted">Samples replayed</p>
                  <p className="font-mono text-ink-primary">
                    {replayState?.total_samples.toLocaleString()}
                  </p>
                </div>
                <div>
                  <p className="text-ink-muted">Final bit depth</p>
                  <Value value={bitDepth} unit="m" />
                </div>
                <div>
                  <p className="text-ink-muted">Recorded events on this well</p>
                  <p className="font-mono text-ink-primary">{events.data?.length ?? 0}</p>
                </div>
              </div>
              <p className="mt-3 text-[11px] text-ink-muted">
                Every sample above was a stored measurement from {replayState?.source}. The
                replay reached the end of the recording; it did not generate any data.
              </p>
            </Panel>
          )}
        </>
      )}

      {/* ---------------------------------------------------- investigation drawer */}
      {investigating && (
        <InvestigationDrawer
          item={investigating}
          onClose={closeInvestigation}
          paused={pausedByInvestigation}
          engineer={engineer}
          setEngineer={setEngineer}
          actionNote={actionNote}
          setActionNote={setActionNote}
          recordAction={recordAction}
          actionSaved={actionSaved}
          actionError={actionError}
        />
      )}

      {/* ---------------------------------------------------- event detail drawer */}
      {selectedEvent && (
        <div className="fixed inset-y-0 right-0 z-50 w-full max-w-md overflow-y-auto border-l border-surface-border bg-surface-raised p-4 shadow-card">
          <div className="flex items-start justify-between gap-3">
            <h2 className="text-sm font-semibold text-ink-primary">Recorded event</h2>
            <button
              type="button"
              onClick={() => setSelectedEvent(null)}
              aria-label="Close event"
              className="rounded-pill border border-surface-border px-3 py-1 text-xs text-ink-secondary hover:bg-surface-hover"
            >
              Close
            </button>
          </div>
          <div className="mt-4 space-y-2 text-xs">
            <p className="text-ink-secondary">{selectedEvent.description}</p>
            <div className="flex flex-wrap gap-1">
              <Tag>{selectedEvent.event_type}</Tag>
              {selectedEvent.depth_start_m != null && (
                <Tag>{formatNumber(selectedEvent.depth_start_m, 0)} m</Tag>
              )}
              {selectedEvent.formation_name && <Tag>{selectedEvent.formation_name}</Tag>}
            </div>
            <p className="text-[10px] text-ink-muted">
              {selectedEvent.source_dataset ?? "No data available"}
              {selectedEvent.depth_source ? ` · depth via ${selectedEvent.depth_source}` : ""}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * The investigation drawer: the whole reasoning chain behind one piece of evidence.
 *
 * Analogue well → what happened → what was done → what came of it → where it is written
 * down. The report-passage section is the institutional memory reached from here rather
 * than from a separate page: it runs the same semantic search the Reports page runs,
 * seeded with this event's own words, and shows the passages with their page citations.
 */
function InvestigationDrawer({
  item,
  onClose,
  paused,
  engineer,
  setEngineer,
  actionNote,
  setActionNote,
  recordAction,
  actionSaved,
  actionError,
}: {
  item: HistoricalEvidence;
  onClose: () => void;
  paused: boolean;
  engineer: string;
  setEngineer: (value: string) => void;
  actionNote: string;
  setActionNote: (value: string) => void;
  recordAction: (decision: string) => void;
  actionSaved: string | null;
  actionError: string | null;
}) {
  // The query is the event's own recorded words, so a passage that comes back is
  // genuinely about this event rather than about a phrase composed here.
  const query = [item.formation_name, item.event_type, item.description]
    .filter(Boolean)
    .join(" ")
    .slice(0, 300);

  const [searchRequested, setSearchRequested] = useState(false);
  const passages = useQuery({
    queryKey: ["investigation-passages", item.event_id, query],
    queryFn: () => api.searchDocuments(query, { limit: 3 }),
    enabled: searchRequested && query.trim().length >= 2,
    retry: false,
  });

  return (
    <div className="fixed inset-y-0 right-0 z-50 w-full max-w-md overflow-y-auto border-l border-surface-border bg-surface-raised p-4 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink-primary">Investigation</h2>
          <p className="text-[11px] text-ink-muted">
            Evidence retrieved from the database, not generated
            {paused && " · replay paused"}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close investigation"
          className="rounded-pill border border-surface-border px-3 py-1 text-xs text-ink-secondary hover:bg-surface-hover"
        >
          Close
        </button>
      </div>

      <div className="mt-4 space-y-4 text-xs">
        <section>
          <p className="text-[10px] uppercase tracking-wider text-ink-muted">Analogue well</p>
          <p className="text-ink-primary">{item.well_name}</p>
          <p className="text-ink-muted">
            similarity {formatNumber(item.similarity_score, 4)}
            {item.distance_from_bit_m != null &&
              ` · ${relationToBit(item.distance_from_bit_m)}`}
          </p>
        </section>

        <section>
          <p className="text-[10px] uppercase tracking-wider text-ink-muted">
            What happened there
          </p>
          <p className="text-ink-secondary">{item.description}</p>
          <div className="mt-1 flex flex-wrap gap-1">
            <Tag>{item.event_type}</Tag>
            {item.formation_name && <Tag>{item.formation_name}</Tag>}
            {item.depth_start_m != null && (
              <Tag>{formatNumber(item.depth_start_m, 0)} m</Tag>
            )}
          </div>
        </section>

        <section>
          <p className="text-[10px] uppercase tracking-wider text-ink-muted">
            What was done about it
          </p>
          {item.mitigations.length === 0 ? (
            <p className="text-ink-muted">
              No validated historical mitigation found for this event. NWIS retrieves
              actions that were recorded; it does not generate one.
            </p>
          ) : (
            <ul className="space-y-2">
              {item.mitigations.map((m, i) => (
                <li key={i} className="rounded-card border border-surface-border bg-surface-overlay p-2">
                  <p className="text-ink-primary">{String(m.action_taken ?? "—")}</p>
                  {m.outcome ? (
                    <p className="mt-0.5 text-ink-muted">outcome: {String(m.outcome)}</p>
                  ) : (
                    <p className="mt-0.5 text-ink-muted">outcome: No data available</p>
                  )}
                  <p className="mt-0.5 text-[10px] text-ink-muted">
                    source: {String(m.source_reference ?? m.source_dataset ?? "No data available")}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section>
          <p className="text-[10px] uppercase tracking-wider text-ink-muted">Provenance</p>
          <p className="text-ink-muted">
            {item.source_dataset ?? "No data available"}
            {item.source_reference ? ` · ${item.source_reference}` : ""}
          </p>
          {item.depth_source && (
            <p className="text-ink-muted">depth established via {item.depth_source}</p>
          )}
        </section>

        {/* ------------------------------------------- institutional memory */}
        <section className="border-t border-surface-border pt-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[10px] uppercase tracking-wider text-ink-muted">
              Report passages
            </p>
            {!searchRequested && (
              <button
                type="button"
                onClick={() => setSearchRequested(true)}
                className="rounded-pill border border-surface-border px-3 py-1 text-[11px] text-accent-strong hover:bg-surface-hover"
              >
                Search reports
              </button>
            )}
          </div>
          {!searchRequested ? (
            <p className="mt-1 text-ink-muted">
              Searches the ingested scanned reports for passages about this event, and
              cites the page each came from.
            </p>
          ) : passages.isPending ? (
            <Loading label="Searching report passages" />
          ) : passages.isError ? (
            <ErrorState error={passages.error} onRetry={() => passages.refetch()} />
          ) : passages.data.results.length === 0 ? (
            <p className="mt-1 text-ink-muted">
              No passage in the ingested reports is close enough to this event.{" "}
              {passages.data.provenance.passages_indexed} of{" "}
              {passages.data.provenance.passages_total} stored passages are indexed, so
              this is what the corpus holds, not everything ever written.
            </p>
          ) : (
            <div className="mt-2 space-y-2">
              {passages.data.results.map((p) => (
                <article
                  key={p.chunk_id}
                  className="rounded-card border border-surface-border bg-surface-overlay p-2"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-ink-primary">{p.well_name ?? "Well not linked"}</span>
                    {p.page_number != null && <Tag>page {p.page_number}</Tag>}
                    <span className="ml-auto font-mono text-[10px] text-ink-muted">
                      {formatNumber(p.similarity, 3)}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[10px] text-ink-muted">{p.document_title}</p>
                  <p className="mt-1 line-clamp-6 whitespace-pre-wrap text-[11px] leading-relaxed text-ink-secondary">
                    {p.text}
                  </p>
                </article>
              ))}
              <p className="text-[10px] text-ink-muted">
                Searched {passages.data.provenance.passages_searched} of{" "}
                {passages.data.provenance.passages_total} stored passages.
              </p>
            </div>
          )}
        </section>

        {/* engineer feedback */}
        <section className="border-t border-surface-border pt-3">
          <p className="text-[10px] uppercase tracking-wider text-ink-muted">
            Record your decision
          </p>
          <input
            value={engineer}
            onChange={(e) => setEngineer(e.target.value)}
            placeholder="engineer name"
            aria-label="Engineer name"
            className="mt-2 w-full rounded-card border border-surface-border bg-surface-overlay px-3 py-1.5 text-xs text-ink-primary placeholder:text-ink-muted focus:border-accent focus:outline-none"
          />
          <textarea
            value={actionNote}
            onChange={(e) => setActionNote(e.target.value)}
            placeholder="action taken"
            aria-label="Action taken"
            rows={2}
            className="mt-2 w-full rounded-card border border-surface-border bg-surface-overlay px-3 py-1.5 text-xs text-ink-primary placeholder:text-ink-muted focus:border-accent focus:outline-none"
          />
          <div className="mt-2 flex flex-wrap gap-2">
            {["accepted", "rejected", "investigating"].map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => recordAction(d)}
                className="rounded-pill border border-surface-border px-3 py-1 text-[11px] text-ink-secondary hover:bg-surface-hover"
              >
                {d}
              </button>
            ))}
          </div>
          {actionSaved && <p className="mt-2 text-[11px] text-state-ok">{actionSaved}</p>}
          {actionError && <p className="mt-2 text-[11px] text-state-bad">{actionError}</p>}
        </section>
      </div>
    </div>
  );
}
