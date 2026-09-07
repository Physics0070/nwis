/** Alert inbox. Levels and thresholds are decided by the backend. */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import {
  ErrorState, LevelBadge, Loading, Panel, Unavailable, formatDepth, formatTimestamp,
} from "../components/primitives";

export default function Alerts() {
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: () => api.allAlerts(100) });

  if (alerts.isLoading) return <Loading label="Loading alerts" />;
  if (alerts.isError) return <ErrorState error={alerts.error} onRetry={alerts.refetch} />;

  if (!alerts.data || alerts.data.length === 0) {
    return (
      <Unavailable
        reason="No alerts have been raised."
        hint="The risk engine raises an alert only above the configured minimum level, with cooldown and depth deduplication applied."
      />
    );
  }

  return (
    <Panel title="Risk alerts" subtitle={`${alerts.data.length} raised`}>
      <ul className="divide-y divide-surface-border">
        {alerts.data.map((alert) => (
          <li key={alert.id} className="py-3">
            <div className="flex flex-wrap items-center gap-2">
              <LevelBadge level={alert.level} />
              <Link to={`/alerts/${alert.id}`} className="text-sm text-ink-primary hover:text-accent-strong">
                {alert.title}
              </Link>
              <span className="text-xs text-ink-muted">{formatDepth(alert.depth_m)}</span>
              <span className="ml-auto text-xs text-ink-muted">{formatTimestamp(alert.raised_at)}</span>
            </div>
            <p className="mt-1 text-xs text-ink-secondary">{alert.summary}</p>
            <p className="mt-1 text-[11px] text-ink-muted">
              status: {alert.status} · seen {alert.occurrence_count}×
            </p>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
