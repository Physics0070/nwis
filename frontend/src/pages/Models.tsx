/**
 * Model insights.
 *
 * Displays only metrics that a training run actually measured and wrote to the registry.
 * Where a model is untrained the page says so. Where a metric is unmeasurable it says
 * that too — no placeholder accuracy is ever shown.
 */
import { useQuery } from "@tanstack/react-query";
import { api, type ModelVersion } from "../lib/api";
import {
  ErrorState,
  Loading,
  Panel,
  Tag,
  Unavailable,
  formatNumber,
  formatTimestamp,
} from "../components/primitives";

const HEADLINE_KEYS = [
  ["accuracy", "Accuracy"],
  ["macro_f1", "Macro F1"],
  ["balanced_accuracy", "Balanced accuracy"],
  ["weighted_f1", "Weighted F1"],
  ["cohen_kappa", "Cohen κ"],
  ["force_penalty_score", "FORCE penalty (lower better)"],
] as const;

function ClassificationMetrics({ split, metrics }: { split: string; metrics: any }) {
  if (!metrics || typeof metrics !== "object") return null;
  const baseline = metrics.majority_class_baseline;

  return (
    <div className="rounded-card border border-surface-border bg-surface-overlay p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-ink-secondary">
          {split.replace(/_/g, " ")}
        </h4>
        {metrics.wells !== undefined && <Tag>{metrics.wells} unseen wells</Tag>}
        {metrics.samples !== undefined && (
          <Tag>{Number(metrics.samples).toLocaleString()} rows</Tag>
        )}
      </div>

      <div className="mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-2">
        {HEADLINE_KEYS.map(([key, label]) =>
          metrics[key] === undefined ? null : (
            <div key={key} className="flex justify-between text-xs">
              <span className="text-ink-muted">{label}</span>
              <span className="font-mono text-ink-primary">
                {formatNumber(metrics[key], 4)}
                {baseline && baseline[key] !== undefined && (
                  <span className="ml-2 text-ink-muted">
                    (baseline {formatNumber(baseline[key], 4)})
                  </span>
                )}
              </span>
            </div>
          ),
        )}
      </div>

      {Array.isArray(metrics.per_class) && metrics.per_class.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead className="text-ink-muted">
              <tr>
                <th className="py-1 text-left font-medium">Class</th>
                <th className="py-1 text-right font-medium">Support</th>
                <th className="py-1 text-right font-medium">Precision</th>
                <th className="py-1 text-right font-medium">Recall</th>
                <th className="py-1 text-right font-medium">F1</th>
              </tr>
            </thead>
            <tbody className="text-ink-secondary">
              {[...metrics.per_class]
                .sort((a: any, b: any) => b.support - a.support)
                .map((row: any) => (
                  <tr key={row.code} className="border-t border-surface-border">
                    <td className="py-1">{row.name}</td>
                    <td className="py-1 text-right font-mono">
                      {Number(row.support).toLocaleString()}
                    </td>
                    <td className="py-1 text-right font-mono">
                      {formatNumber(row.precision, 3)}
                    </td>
                    <td className="py-1 text-right font-mono">
                      {formatNumber(row.recall, 3)}
                    </td>
                    <td
                      className="py-1 text-right font-mono"
                      style={{ color: row.f1 === 0 ? "#d9455f" : undefined }}
                    >
                      {formatNumber(row.f1, 3)}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ModelCard({ model }: { model: ModelVersion }) {
  const metrics = model.metrics ?? {};
  const splits = Object.entries(metrics).filter(
    ([, value]) => value && typeof value === "object" && "accuracy" in (value as object),
  );
  const unsupervised = metrics.labels_available === false;

  return (
    <Panel
      title={`${model.name} · ${model.algorithm}`}
      subtitle={`${model.dataset ?? "dataset not recorded"} · trained ${formatTimestamp(
        model.trained_at,
      )}`}
    >
      <div className="mb-3 flex flex-wrap gap-2 text-xs">
        <Tag>{model.task}</Tag>
        <Tag>{model.feature_columns.length} features</Tag>
        {model.dataset_rows && <Tag>{model.dataset_rows.toLocaleString()} rows</Tag>}
        {model.training_seconds && <Tag>{formatNumber(model.training_seconds, 1)} s</Tag>}
        {(model.hyperparameters as any)?.device && (
          <Tag>device: {String((model.hyperparameters as any).device)}</Tag>
        )}
      </div>

      {unsupervised && (
        <div className="mb-3 rounded-card border border-state-warn/40 bg-state-warn/10 p-3 text-xs text-ink-secondary">
          {metrics.evaluation_note}
        </div>
      )}

      {splits.length > 0 ? (
        <div className="space-y-3">
          {splits.map(([split, value]) => (
            <ClassificationMetrics key={split} split={split} metrics={value} />
          ))}
        </div>
      ) : unsupervised ? (
        <div className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
          <div className="flex justify-between">
            <span className="text-ink-muted">training rows</span>
            <span className="font-mono text-ink-primary">
              {Number(metrics.training_rows ?? 0).toLocaleString()}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-ink-muted">achieved anomaly rate</span>
            <span className="font-mono text-ink-primary">
              {formatNumber(metrics.achieved_anomaly_rate ?? 0, 4)}
            </span>
          </div>
        </div>
      ) : (
        <Unavailable reason="No evaluation metrics were recorded for this model." />
      )}

      {model.limitations.length > 0 && (
        <div className="mt-3 border-t border-surface-border pt-3">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-ink-secondary">
            Known limitations
          </h4>
          <ul className="mt-1.5 space-y-1 text-[11px] text-ink-muted">
            {model.limitations.map((limitation, index) => (
              <li key={index}>• {limitation}</li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

export default function Models() {
  const models = useQuery({ queryKey: ["models"], queryFn: api.models });

  if (models.isLoading) return <Loading label="Loading model registry" />;
  if (models.isError) return <ErrorState error={models.error} onRetry={models.refetch} />;

  if (!models.data || models.data.length === 0) {
    return (
      <Unavailable
        reason="No models are registered."
        hint="Train a model with the ml/ pipelines; metrics appear here only after a real training run."
      />
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-muted">
        Every figure below was measured by a training run and written to the model
        registry. Nothing on this page is estimated or hand-entered.
      </p>
      {models.data.map((model) => (
        <ModelCard key={model.id} model={model} />
      ))}
    </div>
  );
}
