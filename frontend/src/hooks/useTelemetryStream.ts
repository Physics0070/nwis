/**
 * Live telemetry subscription.
 *
 * The server owns replay position and status; this hook only reflects what arrives.
 * It never generates, interpolates or extrapolates a sample — if the socket is quiet,
 * the UI shows the last real value and the connection state, not a moving line.
 *
 * A dropped socket is a transport failure, not a data condition, so it is retried with
 * exponential backoff. Replay state lives on the server and is re-sent on connect, so a
 * reconnect resumes the same session rather than restarting it. Buffered samples are
 * kept across a reconnect and cleared only when the well changes: they were real
 * measurements and did not stop being real because the connection blinked.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReplayState, TelemetrySample } from "../lib/api";
import { telemetrySocketUrl } from "../lib/api";

export type ConnectionStatus = "connecting" | "open" | "closed" | "error";

export interface StreamedSample extends TelemetrySample {
  receivedIndex: number;
}

const RETRY_BASE_MS = 500;
const RETRY_MAX_MS = 8000;

export function useTelemetryStream(wellId: number | null, bufferSize = 300) {
  const [samples, setSamples] = useState<StreamedSample[]>([]);
  const [replayState, setReplayState] = useState<ReplayState | null>(null);
  const [connection, setConnection] = useState<ConnectionStatus>("closed");
  const [lastError, setLastError] = useState<string | null>(null);
  // Bumping this re-runs the connect effect. It is the retry trigger.
  const [attempt, setAttempt] = useState(0);
  const failuresRef = useRef(0);
  const socketRef = useRef<WebSocket | null>(null);

  const reset = useCallback(() => {
    setSamples([]);
    setReplayState(null);
    setLastError(null);
  }, []);

  // Buffered samples belong to one well. Clearing them here — rather than inside the
  // connect effect — is what lets a reconnect keep the history it already received.
  useEffect(() => {
    reset();
    failuresRef.current = 0;
    setAttempt(0);
  }, [wellId, reset]);

  useEffect(() => {
    if (wellId === null) {
      setConnection("closed");
      return;
    }

    let disposed = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    const scheduleRetry = () => {
      if (disposed) return;
      const delay = Math.min(RETRY_BASE_MS * 2 ** failuresRef.current, RETRY_MAX_MS);
      failuresRef.current += 1;
      retryTimer = setTimeout(() => {
        if (!disposed) setAttempt((n) => n + 1);
      }, delay);
    };

    setConnection("connecting");
    const socket = new WebSocket(telemetrySocketUrl(wellId));
    socketRef.current = socket;

    socket.onopen = () => {
      failuresRef.current = 0;
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
    socket.onclose = () => {
      setConnection("closed");
      scheduleRetry();
    };

    return () => {
      // Detach before closing. A socket closed while still CONNECTING — which is what
      // StrictMode's double mount does, and what switching wells does — still fires
      // close (and sometimes error) afterwards. With its handlers attached it would
      // report "closed" or "connection failed" over the socket that replaced it, so the
      // UI would show a dead connection beside live streaming data, and would schedule
      // a retry for a socket nobody is waiting on.
      disposed = true;
      if (retryTimer !== undefined) clearTimeout(retryTimer);
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      socket.close();
      socketRef.current = null;
    };
  }, [wellId, bufferSize, attempt]);

  const latest = samples.length ? samples[samples.length - 1] : null;
  return { samples, latest, replayState, connection, lastError, setReplayState };
}
