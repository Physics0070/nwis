/**
 * Telemetry time-series chart.
 *
 * Plots only points that arrived from the backend, with their real timestamps. There is
 * no synthetic series, no smoothing that invents values, and no fixed axis range chosen
 * to make a line look good — the domain follows the data.
 */
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { StreamedSample } from "../hooks/useTelemetryStream";
import { Unavailable, formatNumber } from "./primitives";

export interface ChannelSpec {
  key: string;
  label: string;
  unit: string;
  colour: string;
}

export default function TelemetryChart({
  samples,
  channels,
  height = 200,
}: {
  samples: StreamedSample[];
  channels: ChannelSpec[];
  height?: number;
}) {
  if (samples.length === 0) {
    return (
      <Unavailable
        reason="No telemetry has arrived yet."
        hint="Start the replay to stream stored samples from the database."
      />
    );
  }

  const data = samples.map((sample) => {
    const row: Record<string, number | string | null> = {
      time: sample.recorded_at ? sample.recorded_at.slice(11, 19) : "",
      index: sample.receivedIndex,
    };
    for (const channel of channels) {
      const value = sample.channels?.[channel.key];
      row[channel.key] = value === undefined ? null : value;
    }
    return row;
  });

  // Report channels that are absent from the stream instead of drawing a flat zero line.
  const missing = channels.filter((channel) =>
    data.every((row) => row[channel.key] === null || row[channel.key] === undefined),
  );
  const present = channels.filter((channel) => !missing.includes(channel));

  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="#243141" strokeDasharray="2 4" vertical={false} />
          <XAxis
            dataKey="time"
            tick={{ fill: "#63788f", fontSize: 10 }}
            stroke="#243141"
            minTickGap={40}
          />
          <YAxis
            tick={{ fill: "#63788f", fontSize: 10 }}
            stroke="#243141"
            width={52}
            domain={["auto", "auto"]}
          />
          <Tooltip
            contentStyle={{
              background: "#18232d",
              border: "1px solid #243141",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "#9bacc0" }}
            formatter={(value, name) => {
              const key = String(name);
              const channel = channels.find((c) => c.key === key);
              const numeric = typeof value === "number" ? formatNumber(value) : String(value);
              return [`${numeric} ${channel?.unit ?? ""}`, channel?.label ?? key];
            }}
          />
          {present.map((channel) => (
            <Line
              key={channel.key}
              type="monotone"
              dataKey={channel.key}
              stroke={channel.colour}
              strokeWidth={1.6}
              dot={false}
              isAnimationActive={false}
              connectNulls={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
        {present.map((channel) => (
          <span key={channel.key} className="flex items-center gap-1.5 text-ink-secondary">
            <span
              className="inline-block h-2 w-2 rounded-pill"
              style={{ background: channel.colour }}
            />
            {channel.label} ({channel.unit})
          </span>
        ))}
        {missing.map((channel) => (
          <span key={channel.key} className="text-ink-muted">
            {channel.label}: no readings in this window
          </span>
        ))}
      </div>
    </div>
  );
}
