/**
 * Alert explanation and engineer feedback.
 *
 * Answers the questions the brief requires of every alert: what happened, why the system
 * is concerned, which historical wells support it, at what depth, which measurements
 * contributed, what happened historically and what was done about it.
 *
 * The evidence is read back from the assessment stored when the alert was raised, not
 * recomputed, so the engineer sees exactly what the system acted on.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";
import {
  ContributionBar,
  ErrorState,
  LevelBadge,
  Loading,
  Panel,
  Tag,
  Unavailable,
  formatDepth,
  formatNumber,
} from "../components/primitives";

const DECISIONS = ["accepted", "rejected", "investigating", "resolved"] as const;

export default function AlertExplanation() {
  const { alertId } = useParams();
  const id = Number(alertId);
  const queryClient = useQueryClient();

  const [decision, setDecision] = useState<string>("accepted");
  const [actionDescription, setActionDescription] = useState("");
  const [outcome, setOutcome] = useState("");
  const [engineerName, setEngineerName] = useState("");

  const explanation = useQuery({
    queryKey: ["alert-explanation", id],
    queryFn: () => api.alertExplanation(id),
    enabled: Number.isFinite(id),
  });

  const submit = useMutation({
    mutationFn: () =>
      api.recordAction({
        alert_id: id,
        decision,
        action_description: actionDescription || undefined,
        outcome: outcome || undefined,
        engineer_name: engineerName || undefined,
      }),
    onSuccess: () => {
      setActionDescription("");
      setOutcome("");
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });

  if (explanation.isLoading) return <Loading label="Loading alert evidence" />;
  if (explanation.isError)
    return <ErrorState error={explanation.error} onRetry={explanation.refetch} />;

  const data = explanation.data!;

  return (
    <div className="space-y-4">
      <Panel title="Why this alert was raised">
        <div className="flex flex-wrap items-center gap-3">
          <LevelBadge level={data.risk_level} />
          <span className="font-mono text-sm text-ink-primary">
            score {formatNumber(data.score, 4)}
          </span>
          <Tag>{data.well_name}</Tag>
          <Tag>{formatDepth(data.depth_m)}</Tag>
          <Tag>mode: {data.mode}</Tag>
        </div>
        <p className="mt-3 text-sm text-ink-secondary">{data.narrative}</p>
        <p className="mt-2 text-xs text-ink-muted">
          {data.probability === null
            ? "No probability is reported. This is a historical risk indicator combining unsupervised anomaly detection with analogue evidence — not a calibrated supervised prediction."
            : `Calibrated probability: ${formatNumber(data.probability, 4)}`}
        </p>
        {data.notes.length > 0 && (
          <ul className="mt-3 space-y-1 border-t border-surface-border pt-2 text-xs text-ink-muted">
            {data.notes.map((note, index) => (
              <li key={index}>• {note}</li>
            ))}
          </ul>
        )}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="What contributed" subtitle="Component scores and weights">
          {data.components.length === 0 ? (
            <Unavailable reason="No component breakdown was stored for this assessment." />
          ) : (
            <div className="space-y-3">
              {data.components.map((component) => (
                <div key={component.name}>
                  <div className="flex justify-between text-xs">
                    <span className="text-ink-secondary">{component.name}</span>
                    <span className="font-mono text-ink-primary">
                      {formatNumber(component.value, 3)}
                      <span className="ml-1 text-ink-muted">×{component.weight}</span>
                    </span>
                  </div>
                  <ContributionBar value={component.value} weight={component.weight} />
                  <p className="mt-0.5 text-[11px] text-ink-muted">{component.explanation}</p>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Supporting analogue wells">
          {data.analogue_wells.length === 0 ? (
            <Unavailable reason="No analogue wells supported this assessment." />
          ) : (
            <ul className="space-y-2 text-xs">
              {data.analogue_wells.map((well: any, index: number) => (
                <li
                  key={index}
                  className="flex flex-wrap items-center gap-2 rounded-card border border-surface-border bg-surface-overlay px-2.5 py-2"
                >
                  <span className="font-medium text-ink-primary">{well.well_name}</span>
                  <span className="font-mono text-accent-strong">
                    {formatNumber(well.score, 4)}
                  </span>
                  {well.dominant_formation && <Tag>{well.dominant_formation}</Tag>}
                  {well.distance_km !== null && well.distance_km !== undefined && (
                    <span className="text-ink-muted">
                      {formatNumber(well.distance_km, 1)} km
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel
        title="Historical evidence and mitigation history"
        subtitle="Retrieved records — nothing here is generated"
      >
        {data.historical_evidence.length === 0 ? (
          <Unavailable
            reason="No validated historical evidence was found for this interval."
            hint="NWIS does not generate engineering recommendations in the absence of evidence."
          />
        ) : (
          <ul className="divide-y divide-surface-border">
            {data.historical_evidence.map((evidence, index) => (
              <li key={`${evidence.event_id}-${index}`} className="py-3">
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

                {evidence.mitigations.length > 0 ? (
                  <ul className="mt-2 space-y-1">
                    {evidence.mitigations.map((mitigation: any, i: number) => (
                      <li
                        key={i}
                        className="rounded-card border border-surface-border bg-surface-overlay px-2.5 py-1.5 text-xs"
                      >
                        <span className="text-ink-primary">{mitigation.action_taken}</span>
                        {mitigation.outcome && (
                          <span className="text-ink-muted"> → {mitigation.outcome}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1.5 text-[11px] text-state-warn">
                    No validated historical mitigation found for this event.
                  </p>
                )}

                <p className="mt-1 text-[11px] text-ink-muted">
                  source: {evidence.source_dataset ?? "unknown"} · depth established via{" "}
                  {evidence.depth_source ?? "as recorded"}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        title="Record your decision"
        subtitle="Stored as institutional memory and made available for future retraining"
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-ink-muted">
            Decision
            <select
              value={decision}
              onChange={(event) => setDecision(event.target.value)}
              className="mt-1 w-full rounded-card border border-surface-border bg-surface-overlay px-2 py-1.5 text-sm text-ink-primary"
            >
              {DECISIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-ink-muted">
            Engineer
            <input
              value={engineerName}
              onChange={(event) => setEngineerName(event.target.value)}
              placeholder="name"
              className="mt-1 w-full rounded-card border border-surface-border bg-surface-overlay px-2 py-1.5 text-sm text-ink-primary"
            />
          </label>
          <label className="text-xs text-ink-muted sm:col-span-2">
            Action taken
            <textarea
              value={actionDescription}
              onChange={(event) => setActionDescription(event.target.value)}
              rows={2}
              className="mt-1 w-full rounded-card border border-surface-border bg-surface-overlay px-2 py-1.5 text-sm text-ink-primary"
            />
          </label>
          <label className="text-xs text-ink-muted sm:col-span-2">
            Outcome
            <textarea
              value={outcome}
              onChange={(event) => setOutcome(event.target.value)}
              rows={2}
              className="mt-1 w-full rounded-card border border-surface-border bg-surface-overlay px-2 py-1.5 text-sm text-ink-primary"
            />
          </label>
        </div>

        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            onClick={() => submit.mutate()}
            disabled={submit.isPending}
            className="rounded-pill bg-accent px-4 py-1.5 text-xs font-medium text-surface-base disabled:opacity-50"
          >
            {submit.isPending ? "Recording…" : "Record decision"}
          </button>
          {submit.isSuccess && (
            <span className="text-xs text-state-ok">
              Recorded. Stored for future model training; nothing retrains automatically.
            </span>
          )}
          {submit.isError && (
            <span className="text-xs text-state-bad">
              {(submit.error as Error).message}
            </span>
          )}
        </div>
      </Panel>
    </div>
  );
}
