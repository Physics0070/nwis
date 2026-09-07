/**
 * Live telemetry subscription.
 *
 * The server owns replay position and status; this hook only reflects what arrives.
 * It never generates, interpolates or extrapolates a sample — if the socket is quiet,
 * the UI shows the last real value and the connection state, not a moving line.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReplayState, TelemetrySample } from "../lib/api";
import { telemetrySocketUrl } from "../lib/api";

export type ConnectionStatus = "connecting" | "open" | "closed" | "error";

export interface StreamedSample extends TelemetrySample {
  receivedIndex: number;
}

export function useTelemetryStream(wellId: number | null, bufferSize = 300) {
  const [samples, setSamples] = useState<StreamedSample[]>([]);
  const [replayState, setReplayState] = useState<ReplayState | null>(null);
  const [connection, setConnection] = useState<ConnectionStatus>("closed");
  const [lastError, setLastError] = useState<string | null>(null);
  const socketRef = useRef<WebSocket | null>(null);

  const reset = useCallback(() => {
    setSamples([]);
    setReplayState(null);
    setLastError(null);
  }, []);

  useEffect(() => {
    if (wellId === null) return;

    reset();
    setConnection("connecting");
    const socket = new WebSocket(telemetrySocketUrl(wellId));
    socketRef.current = socket;

    socket.onopen = () => {
      setConnection("open");
      // A previous attempt may have failed; a successful connection clears that state
      // so the UI never shows a stale failure next to a live connection.
      setLastError(null);
    };

    socket.onmessage = (raw) => {
      try {
        const message = JSON.parse(raw.data);
        if (message.event === "error") {
          setLastError(message.detail ?? "Stream error");
          return;
        }
        if (message.state) setReplayState(message.state as ReplayState);
        setLastError(null);
        if (message.sample) {
          const sample = message.sample as TelemetrySample;
          const index = message.state?.current_index ?? 0;
          setSamples((previous) => {
            // Ignore repeats so a reconnect does not duplicate points.
            if (previous.length && previous[previous.length - 1].receivedIndex === index) {
              return previous;
            }
            const next = [...previous, { ...sample, receivedIndex: index }];
            return next.length > bufferSize ? next.slice(next.length - bufferSize) : next;
          });
        }
      } catch {
        setLastError("Received a malformed message from the telemetry stream");
      }
    };

    socket.onerror = () => {
      setConnection("error");
      setLastError("The telemetry connection failed");
    };
    socket.onclose = () => setConnection("closed");

    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [wellId, bufferSize, reset]);

  const latest = samples.length ? samples[samples.length - 1] : null;
  return { samples, latest, replayState, connection, lastError, setReplayState };
}
