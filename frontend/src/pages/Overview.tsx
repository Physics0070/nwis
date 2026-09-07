/** Overview dashboard. Every figure is read from /api/status and /api/alerts. */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import {
  ErrorState,
  LevelBadge,
  Loading,
  Metric,
  Panel,
  Tag,
  Unavailable,
  formatDepth,
  formatTimestamp,
} from "../components/primitives";

export default function Overview() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: () => api.allAlerts(10) });
  const wells = useQuery({
    queryKey: ["wells", "telemetry"],
    queryFn: () => api.listWells({ hasTelemetry: true, limit: 10 }),
  });

  if (status.isLoading) return <Loading label="Loading knowledge base status" />;
  if (status.isError) return <ErrorState error={status.error} onRetry={status.refetch} />;

  const counts = status.data!.counts;
  const database = status.data!.database;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Wells in knowledge base" value={counts.wells} />
        <Metric
          label="Wells with position"
          value={counts.wells_with_position}
          hint={
            counts.wells_with_position === counts.wells
              ? "every well has a real surveyed position"
              : `${counts.wells - counts.wells_with_position} unmapped`
          }
        />
        <Metric label="Telemetry samples" value={counts.telemetry_samples} />
        <Metric label="Historical events" value={counts.drilling_events} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel
          title="Active wells"
          subtitle="Wells with telemetry available for replay"
          className="lg:col-span-2"
        >
          {wells.isLoading && <Loading />}
          {wells.isError && <ErrorState error={wells.error} onRetry={wells.refetch} />}
          {wells.data && wells.data.items.length === 0 && (
            <Unavailable reason="No wells with telemetry are loaded." />
          )}
          {wells.data && wells.data.items.length > 0 && (
            <ul className="divide-y divide-surface-border">
              {wells.data.items.map((well) => (
                <li key={well.id} className="flex items-center justify-between py-2.5">
                  <div>
                    <Link
                      to={`/wells/${well.id}`}
                      className="text-sm font-medium text-ink-primary hover:text-accent-strong"
                    >
                      {well.name}
                    </Link>
                    <p className="mt-0.5 text-xs text-ink-muted">
                      {well.field_name ?? "field not recorded"} ·{" "}
                      {well.operator ?? "operator not recorded"} · TD{" "}
                      {formatDepth(well.total_depth_md_m)}
                    </p>
                  </div>
                  <div className="flex gap-1.5">
                    {well.has_telemetry && <Tag>telemetry</Tag>}
                    {well.has_logs && <Tag>logs</Tag>}
                    {well.has_trajectory && <Tag>trajectory</Tag>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Knowledge base" subtitle="What is actually stored">
          <dl className="space-y-2 text-sm">
            {Object.entries(counts).map(([key, value]) => (
              <div key={key} className="flex justify-between gap-3">
                <dt className="text-ink-muted">{key.replace(/_/g, " ")}</dt>
                <dd className="font-mono text-ink-primary">{value.toLocaleString()}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-4 space-y-1 border-t border-surface-border pt-3 text-xs">
            <p className="text-ink-muted">
              spatial: <span className="text-ink-secondary">{database.spatial_query}</span>
            </p>
            <p className="text-ink-muted">
              vector: <span className="text-ink-secondary">{database.vector_search}</span>
            </p>
            <p className="text-ink-muted">
              time series:{" "}
              <span className="text-ink-secondary">{database.timeseries_storage}</span>
            </p>
          </div>
        </Panel>
      </div>

      <Panel title="Recent alerts" subtitle="Raised by the risk engine">
        {alerts.isLoading && <Loading />}
        {alerts.isError && <ErrorState error={alerts.error} onRetry={alerts.refetch} />}
        {alerts.data && alerts.data.length === 0 && (
          <Unavailable
            reason="No alerts have been raised."
            hint="Alerts appear once the risk engine evaluates a well above the configured minimum level."
          />
        )}
        {alerts.data && alerts.data.length > 0 && (
          <ul className="divide-y divide-surface-border">
            {alerts.data.map((alert) => (
              <li key={alert.id} className="py-2.5">
                <div className="flex items-center gap-2">
                  <LevelBadge level={alert.level} />
                  <Link
                    to={`/alerts/${alert.id}`}
                    className="text-sm text-ink-primary hover:text-accent-strong"
                  >
                    {alert.title}
                  </Link>
                  <span className="ml-auto text-xs text-ink-muted">
                    {formatTimestamp(alert.raised_at)}
                  </span>
                </div>
                <p className="mt-1 text-xs text-ink-secondary">{alert.summary}</p>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {status.data!.warnings.length > 0 && (
        <Panel title="System notes" subtitle="Stated plainly rather than hidden">
          <ul className="space-y-1.5 text-xs text-ink-secondary">
            {status.data!.warnings.map((warning, index) => (
              <li key={index} className="flex gap-2">
                <span className="text-state-warn">•</span>
                {warning}
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}
