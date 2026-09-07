/**
 * The telemetry socket must not let a socket it already abandoned write state.
 *
 * React StrictMode mounts effects twice in development, so the first socket is closed
 * while still CONNECTING. That socket still fires `close` (and sometimes `error`)
 * afterwards. If its handlers are still attached, it reports "closed" or "connection
 * failed" over a socket that is open and streaming — the UI shows a dead connection
 * beside live data.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useTelemetryStream } from "./useTelemetryStream";

class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;
  url: string;

  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }

  close() {
    this.closed = true;
  }
}

beforeEach(() => {
  FakeSocket.instances = [];
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeSocket;
});

afterEach(() => {
  FakeSocket.instances = [];
});

describe("useTelemetryStream", () => {
  it("ignores a socket it has already abandoned", () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: number }) => useTelemetryStream(id),
      { initialProps: { id: 1 } },
    );

    const abandoned = FakeSocket.instances[0];
    expect(abandoned).toBeDefined();

    // Switching wells tears the first socket down and opens a second.
    rerender({ id: 2 });
    const live = FakeSocket.instances[FakeSocket.instances.length - 1];
    expect(live).not.toBe(abandoned);
    expect(abandoned.closed).toBe(true);

    act(() => live.onopen?.());
    expect(result.current.connection).toBe("open");

    // The abandoned socket finishes closing AFTER the live one opened.
    act(() => abandoned.onclose?.());
    expect(result.current.connection).toBe("open");

    // And a late failure on the abandoned socket must not surface as an error either.
    act(() => abandoned.onerror?.());
    expect(result.current.connection).toBe("open");
    expect(result.current.lastError).toBeNull();
  });

  it("still reports the live socket closing", () => {
    const { result } = renderHook(() => useTelemetryStream(1));
    const live = FakeSocket.instances[0];

    act(() => live.onopen?.());
    expect(result.current.connection).toBe("open");

    act(() => live.onclose?.());
    expect(result.current.connection).toBe("closed");
  });
});
