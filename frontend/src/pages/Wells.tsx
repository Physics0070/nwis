/** Well explorer. Server-side search and pagination. */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import {
  ErrorState, Loading, Panel, Tag, Unavailable, formatDepth, formatNumber,
} from "../components/primitives";


export default function Wells() {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);

  // Page size is the backend's `ui.page_size`, so the pager arithmetic here and the
  // limit the API applies can never disagree.
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const pageSize = status.data?.config.page_size ?? null;

  const wells = useQuery({
    queryKey: ["wells", search, offset, pageSize],
    queryFn: () =>
      api.listWells({ search: search || undefined, limit: pageSize as number, offset }),
    enabled: pageSize != null,
  });

  return (
    <Panel
      title="Well knowledge base"
      subtitle={wells.data ? `${wells.data.total} wells stored` : undefined}
      actions={
        <input
          value={search}
          onChange={(event) => { setSearch(event.target.value); setOffset(0); }}
          placeholder="Search wells"
          className="rounded-card border border-surface-border bg-surface-overlay px-2.5 py-1 text-xs text-ink-primary"
        />
      }
    >
      {wells.isLoading && <Loading />}
      {wells.isError && <ErrorState error={wells.error} onRetry={wells.refetch} />}
      {wells.data && wells.data.items.length === 0 && (
        <Unavailable reason="No wells match this search." />
      )}
      {wells.data && wells.data.items.length > 0 && (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-ink-muted">
                <tr>
                  <th className="py-1.5 text-left font-medium">Well</th>
                  <th className="py-1.5 text-left font-medium">Field</th>
                  <th className="py-1.5 text-right font-medium">Latitude</th>
                  <th className="py-1.5 text-right font-medium">Longitude</th>
                  <th className="py-1.5 text-right font-medium">TD</th>
                  <th className="py-1.5 text-left font-medium">Data</th>
                </tr>
              </thead>
              <tbody className="text-ink-secondary">
                {wells.data.items.map((well) => (
                  <tr key={well.id} className="border-t border-surface-border">
                    <td className="py-1.5">
                      <Link to={`/wells/${well.id}`} className="text-ink-primary hover:text-accent-strong">
                        {well.name}
                      </Link>
                    </td>
                    <td className="py-1.5">{well.field_name ?? "—"}</td>
                    <td className="py-1.5 text-right font-mono">
                      {well.latitude === null ? "unmapped" : formatNumber(well.latitude, 4)}
                    </td>
                    <td className="py-1.5 text-right font-mono">
                      {well.longitude === null ? "unmapped" : formatNumber(well.longitude, 4)}
                    </td>
                    <td className="py-1.5 text-right font-mono">{formatDepth(well.total_depth_md_m)}</td>
                    <td className="py-1.5">
                      <span className="flex gap-1">
                        {well.has_logs && <Tag>logs</Tag>}
                        {well.has_telemetry && <Tag>telemetry</Tag>}
                        {well.has_trajectory && <Tag>traj</Tag>}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-3 flex items-center gap-2 text-xs">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - (pageSize ?? 0)))}
              className="rounded-pill border border-surface-border px-3 py-1 text-ink-secondary disabled:opacity-40"
            >
              Previous
            </button>
            <span className="text-ink-muted">
              {offset + 1}–{Math.min(offset + (pageSize ?? 0), wells.data.total)} of {wells.data.total}
            </span>
            <button
              type="button"
              disabled={offset + (pageSize ?? 0) >= wells.data.total}
              onClick={() => setOffset(offset + (pageSize ?? 0))}
              className="rounded-pill border border-surface-border px-3 py-1 text-ink-secondary disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </>
      )}
    </Panel>
  );
}
