/**
 * Report search.
 *
 * Searches scanned well reports by meaning rather than by keyword, and shows the
 * passages themselves with the document and page they came from. It deliberately does
 * not summarise or answer: an engineer acts on what the report says, so the page hands
 * them the original text and tells them exactly where to find it.
 *
 * The corpus is OCR'd from 1980s scans. Recognition errors are visible in the passages
 * below, which is honest — the confidence the OCR reported is recorded at ingestion, and
 * nothing here cleans the text up to look more authoritative than it is.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type PassageMatch } from "../lib/api";
import {
  ErrorState,
  Loading,
  Panel,
  Tag,
  Unavailable,
  formatNumber,
} from "../components/primitives";

/** Example queries that exercise the corpus as it actually is. */
const EXAMPLES = [
  "shale and claystone description",
  "cement plug set in the casing",
  "sandstone with good porosity",
  "core samples and fossils",
];

function Passage({ match }: { match: PassageMatch }) {
  return (
    <article className="rounded-card border border-surface-border bg-surface-overlay p-3">
      <div className="flex flex-wrap items-center gap-2">
        {match.well_name && match.well_id != null ? (
          <Link
            to={`/wells/${match.well_id}`}
            className="text-xs font-semibold text-accent-strong hover:underline"
          >
            {match.well_name}
          </Link>
        ) : (
          <span className="text-xs font-semibold text-ink-primary">
            Well not linked
          </span>
        )}
        {match.page_number != null && <Tag>page {match.page_number}</Tag>}
        <span className="ml-auto font-mono text-[11px] text-ink-muted">
          similarity {formatNumber(match.similarity, 3)}
        </span>
      </div>

      <p className="mt-1 text-[11px] text-ink-muted">{match.document_title}</p>

      {/* Preserve the OCR line breaks: the layout of a scanned table carries meaning. */}
      <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-ink-secondary">
        {match.text}
      </p>
    </article>
  );
}

export default function Reports() {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");

  const search = useQuery({
    queryKey: ["document-search", query],
    queryFn: () => api.searchDocuments(query),
    enabled: query.trim().length >= 2,
    // A 503 here is an answer, not a blip: it carries the reason the corpus cannot be
    // searched and the command that fixes it. Retrying it three times behind a backoff
    // left the page saying "Searching report passages…" for seven seconds and then
    // showing the same message anyway. Surface it at once; the error state has its own
    // retry button.
    retry: false,
  });

  const provenance = search.data?.provenance;

  const runSearch = () => {
    const next = draft.trim();
    if (next.length >= 2) setQuery(next);
  };

  return (
    <div className="space-y-4">
      <Panel
        title="Report search"
        subtitle="Finds passages in scanned well reports by meaning, and cites the page they came from."
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            runSearch();
          }}
          className="flex flex-wrap gap-2"
        >
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            // Enter inside the form should submit on its own. Handling the key here as
            // well costs nothing and makes the primary affordance independent of
            // implicit form submission, which does not fire in every environment.
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                runSearch();
              }
            }}
            placeholder="Describe what you are looking for, in plain language"
            aria-label="Search report passages"
            className="min-w-[260px] flex-1 rounded-pill border border-surface-border bg-surface-overlay px-4 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:border-accent focus:outline-none"
          />
          <button
            type="submit"
            disabled={draft.trim().length < 2}
            className="rounded-pill bg-accent-soft px-4 py-2 text-xs font-semibold text-accent-strong disabled:opacity-40"
          >
            Search
          </button>
        </form>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-ink-muted">Try:</span>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => {
                setDraft(example);
                setQuery(example);
              }}
              className="rounded-pill border border-surface-border px-3 py-1 text-[11px] text-ink-secondary hover:bg-surface-hover"
            >
              {example}
            </button>
          ))}
        </div>
      </Panel>

      {query.trim().length < 2 ? (
        <Panel>
          <Unavailable
            reason="No search yet."
            hint="Enter a query above. Results are passages from the ingested reports, never a generated answer."
          />
        </Panel>
      ) : search.isPending ? (
        <Panel>
          <Loading label="Searching report passages" />
        </Panel>
      ) : search.isError ? (
        <Panel>
          <ErrorState error={search.error} onRetry={() => search.refetch()} />
        </Panel>
      ) : (
        <Panel
          title={`${search.data.results.length} passage${
            search.data.results.length === 1 ? "" : "s"
          }`}
          subtitle={
            provenance
              ? `Searched ${provenance.passages_searched} of ${provenance.passages_total} stored passages · ${provenance.similarity_metric} ≥ ${provenance.min_similarity} · ${provenance.model}`
              : undefined
          }
        >
          {provenance?.note && (
            <p className="mb-3 rounded-card border border-dashed border-surface-border px-3 py-2 text-[11px] text-ink-muted">
              {provenance.note}
            </p>
          )}

          {search.data.results.length === 0 ? (
            <Unavailable
              reason="No passage in the ingested reports is close enough to this query."
              hint={
                provenance
                  ? `Nothing scored at or above the ${provenance.min_similarity} similarity threshold. The nearest passage is shown only when it clears it, so this is a real absence rather than a weak match presented as an answer.`
                  : undefined
              }
            />
          ) : (
            <div className="space-y-3">
              {search.data.results.map((match) => (
                <Passage key={match.chunk_id} match={match} />
              ))}
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}
