/** Application shell: navigation and routes. */
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "./lib/api";
import Overview from "./pages/Overview";
import Wells from "./pages/Wells";
import ActiveWell from "./pages/ActiveWell";
import Alerts from "./pages/Alerts";
import AlertExplanation from "./pages/AlertExplanation";
import Models from "./pages/Models";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/wells", label: "Wells" },
  { to: "/alerts", label: "Alerts" },
  { to: "/models", label: "Models" },
];

export default function App() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });

  return (
    <div className="min-h-full">
      <header className="border-b border-surface-border bg-surface-raised">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-4 px-5 py-3">
          <div>
            <p className="text-sm font-semibold tracking-wide text-ink-primary">NWIS</p>
            <p className="text-[11px] text-ink-muted">Nearby Wells Intelligence System</p>
          </div>

          <nav className="flex gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-pill px-3 py-1.5 text-xs ${
                    isActive
                      ? "bg-accent-soft text-accent-strong"
                      : "text-ink-secondary hover:bg-surface-hover"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          {status.data && (
            <div className="ml-auto flex items-center gap-3 text-[11px] text-ink-muted">
              <span>{status.data.counts.wells} wells</span>
              <span
                className="rounded-pill px-2 py-0.5"
                style={{
                  color: status.data.database.fallback_active ? "#d99b34" : "#3fa87a",
                  background: status.data.database.fallback_active ? "#d99b3418" : "#3fa87a18",
                }}
              >
                {status.data.database.fallback_active
                  ? "local storage backend"
                  : "postgres stack"}
              </span>
            </div>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-5 py-5">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/wells" element={<Wells />} />
          <Route path="/wells/:wellId" element={<ActiveWell />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/alerts/:alertId" element={<AlertExplanation />} />
          <Route path="/models" element={<Models />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
