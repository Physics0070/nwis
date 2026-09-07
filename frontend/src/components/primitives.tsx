/**
 * Shared UI primitives.
 *
 * The state components matter as much as the data components. NWIS must never show a
 * zero, a dash or an empty chart where it actually means "we do not know" — an engineer
 * reading a 0 as a measurement is a safety problem. Every panel renders one of:
 * loading, error, explicitly-unavailable, or real data.
 */
import type { ReactNode } from "react";

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-card border border-surface-border bg-surface-raised shadow-card ${className}`}
    >
      {(title || actions) && (
        <header className="flex items-start justify-between gap-4 border-b border-surface-border px-4 py-3">
          <div>
            {title && (
              <h2 className="text-sm font-semibold tracking-wide text-ink-primary">
                {title}
              </h2>
            )}
            {subtitle && (
              <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p>
            )}
          </div>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-ink-muted">
      <span className="h-2 w-2 animate-pulse rounded-pill bg-accent" />
      {label}…
    </div>
  );
}

/** An error the user can act on. Shows the backend's own message, not a generic one. */
export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const message =
    error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div className="rounded-card border border-state-bad/40 bg-state-bad/10 px-3 py-3 text-sm">
      <p className="font-medium text-state-bad">Could not load this panel</p>
      <p className="mt-1 text-ink-secondary">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 rounded-pill border border-surface-border px-3 py-1 text-xs text-ink-secondary hover:bg-surface-hover"
        >
          Retry
        </button>
      )}
    </div>
  );
}

/**
 * "We genuinely do not have this." Distinct from an error, and never rendered as 0.
 */
export function Unavailable({
  reason,
  hint,
}: {
  reason: string;
  hint?: string;
}) {
  return (
    <div className="rounded-card border border-dashed border-surface-border px-3 py-4 text-sm">
      <p className="text-ink-secondary">{reason}</p>
      {hint && <p className="mt-1 text-xs text-ink-muted">{hint}</p>}
    </div>
  );
}

export function Metric({
  label,
  value,
  unit,
  hint,
  unavailableReason,
}: {
  label: string;
  value: number | string | null | undefined;
  unit?: string;
  hint?: string;
  unavailableReason?: string;
}) {
  const missing = value === null || value === undefined || value === "";
  return (
    <div className="rounded-card border border-surface-border bg-surface-overlay px-4 py-3">
      <p className="text-xs uppercase tracking-wider text-ink-muted">{label}</p>
      {missing ? (
        <p className="mt-1 text-sm text-ink-muted">
          {unavailableReason ?? "Not available"}
        </p>
      ) : (
        <p className="mt-1 font-mono text-metric text-ink-primary">
          {typeof value === "number" ? formatNumber(value) : value}
          {unit && (
            <span className="ml-1 text-sm font-sans text-ink-secondary">
              {unit}
            </span>
          )}
        </p>
      )}
      {hint && <p className="mt-1 text-xs text-ink-muted">{hint}</p>}
    </div>
  );
}

export function LevelBadge({ level }: { level: string }) {
  return (
    <span
      className="rounded-pill px-2 py-0.5 text-xs font-semibold tracking-wide"
      style={{
        color: levelColour(level),
        backgroundColor: `${levelColour(level)}1f`,
        border: `1px solid ${levelColour(level)}55`,
      }}
    >
      {level}
    </span>
  );
}

export function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-pill border border-surface-border bg-surface-overlay px-2 py-0.5 text-xs text-ink-secondary">
      {children}
    </span>
  );
}

/** Proportional bar used to show how much a component contributed to a score. */
export function ContributionBar({
  value,
  weight,
}: {
  value: number;
  weight: number;
}) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-pill bg-surface-overlay">
      <div
        className="h-full rounded-pill bg-accent"
        style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%`, opacity: 0.35 + weight }}
      />
    </div>
  );
}

// ------------------------------------------------------------------- formatting

export function levelColour(level: string): string {
  const map: Record<string, string> = {
    INFO: "#4b7fa8",
    LOW: "#3aa0d1",
    MEDIUM: "#d99b34",
    HIGH: "#e2703a",
    CRITICAL: "#d9455f",
  };
  return map[level] ?? "#63788f";
}

export function formatNumber(value: number, digits = 2): string {
  if (!Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 10000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function formatDepth(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${formatNumber(value, 1)} m`;
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toISOString().slice(0, 19).replace("T", " ");
}
