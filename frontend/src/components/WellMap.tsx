/**
 * Offset well map.
 *
 * Positions come from the database (FORCE X_LOC/Y_LOC and NPD factpages, both
 * transformed to WGS84 server-side). Nothing is placed at a computed or guessed
 * coordinate, and a well without a position is simply absent from the map — with a
 * count shown so its absence is visible rather than silent.
 */
import { useMemo } from "react";
import { CircleMarker, MapContainer, Popup, TileLayer, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import type { NearbyWell, Well } from "../lib/api";
import { formatDepth, formatNumber } from "./primitives";

const TILE_URL =
  import.meta.env.VITE_MAP_TILE_URL ??
  "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

interface Props {
  activeWell: Well;
  nearby: NearbyWell[];
  analogueNames?: Set<string>;
  onSelect?: (wellId: number) => void;
}

export default function WellMap({ activeWell, nearby, analogueNames, onSelect }: Props) {
  const mappable = useMemo(
    () => nearby.filter((n) => n.well.latitude !== null && n.well.longitude !== null),
    [nearby],
  );
  const unmapped = nearby.length - mappable.length;

  if (activeWell.latitude === null || activeWell.longitude === null) {
    return (
      <div className="flex h-full items-center justify-center rounded-card border border-dashed border-surface-border p-6 text-sm text-ink-secondary">
        {activeWell.name} has no surveyed surface position, so it cannot be mapped.
      </div>
    );
  }

  const centre: [number, number] = [activeWell.latitude, activeWell.longitude];

  // Frame the map to the wells actually being shown rather than a fixed zoom. These are
  // offshore wells with no land nearby, so a fixed zoom gives an empty view with no
  // sense of scale.
  const bounds: [number, number][] = [
    centre,
    ...mappable.map((n) => [n.well.latitude!, n.well.longitude!] as [number, number]),
  ];

  return (
    <div className="relative h-full w-full overflow-hidden rounded-card">
      <MapContainer
        bounds={bounds.length > 1 ? bounds : undefined}
        boundsOptions={{ padding: [36, 36] }}
        center={bounds.length > 1 ? undefined : centre}
        zoom={bounds.length > 1 ? undefined : 9}
        scrollWheelZoom
        className="h-full w-full"
        style={{ background: "#0b1015" }}
      >
        {/* Key-free OpenStreetMap tiles. The Carto dark basemap now requires an API
            key and renders "API KEY REQUIRED" watermarks without one. A CSS filter
            darkens the standard tiles to match the console theme instead. Override
            VITE_MAP_TILE_URL to use a commercial provider. */}
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url={TILE_URL}
          className="nwis-map-tiles"
        />

        {mappable.map(({ well, distance_km }) => {
          const isAnalogue = analogueNames?.has(well.name) ?? false;
          return (
            <CircleMarker
              key={well.id}
              center={[well.latitude!, well.longitude!]}
              radius={isAnalogue ? 7 : 5}
              pathOptions={{
                color: isAnalogue ? "#d99b34" : "#4b7fa8",
                fillColor: isAnalogue ? "#d99b34" : "#4b7fa8",
                fillOpacity: isAnalogue ? 0.75 : 0.45,
                weight: isAnalogue ? 2 : 1,
              }}
              eventHandlers={{ click: () => onSelect?.(well.id) }}
            >
              <Tooltip>{well.name}</Tooltip>
              <Popup>
                <div className="text-xs">
                  <p className="font-semibold">{well.name}</p>
                  <p>{formatNumber(distance_km, 2)} km from active well</p>
                  <p>TD {formatDepth(well.total_depth_md_m)}</p>
                  <p className="text-neutral-500">{well.source_dataset}</p>
                  {isAnalogue && <p className="font-medium">ranked analogue</p>}
                </div>
              </Popup>
            </CircleMarker>
          );
        })}

        <CircleMarker
          center={centre}
          radius={9}
          pathOptions={{
            color: "#2f9dd6",
            fillColor: "#2f9dd6",
            fillOpacity: 0.9,
            weight: 3,
          }}
        >
          <Tooltip permanent direction="top" offset={[0, -8]}>
            {activeWell.name}
          </Tooltip>
        </CircleMarker>
      </MapContainer>

      <div className="pointer-events-none absolute bottom-2 left-2 z-[1000] rounded-card bg-surface-base/85 px-2.5 py-1.5 text-[11px] text-ink-secondary">
        <span className="mr-3">
          <span className="mr-1 inline-block h-2 w-2 rounded-pill bg-accent align-middle" />
          active
        </span>
        <span className="mr-3">
          <span className="mr-1 inline-block h-2 w-2 rounded-pill bg-level-MEDIUM align-middle" />
          analogue
        </span>
        <span>
          <span className="mr-1 inline-block h-2 w-2 rounded-pill bg-level-INFO align-middle" />
          offset
        </span>
        {unmapped > 0 && (
          <span className="ml-3 text-state-warn">{unmapped} without position</span>
        )}
      </div>
    </div>
  );
}
