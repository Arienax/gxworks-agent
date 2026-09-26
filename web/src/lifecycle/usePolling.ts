import { useEffect, useRef } from "react";
import { usePanelVisible } from "./visibility";
import { startPolling } from "./requests";
import type { Poller } from "./requests";

/** A poller's lifetime belongs to its resource scope, not to callback identity. */
export function usePolling<T>(scope: string | null, read: (signal: AbortSignal) => Promise<T>,
  apply: (value: T) => void, onError: (error: unknown) => void, interval: number) {
  const visible = usePanelVisible();
  const callbacks = useRef({ scope, read, apply, onError });
  callbacks.current = { scope, read, apply, onError };
  const poller = useRef<Poller | null>(null);
  useEffect(() => {
    if (scope === null || !visible) return;
    const instance = startPolling({
      read: signal => callbacks.current.read(signal),
      apply: value => { if (callbacks.current.scope === scope) callbacks.current.apply(value); },
      error: error => { if (callbacks.current.scope === scope) callbacks.current.onError(error); }, interval,
      paused: document.visibilityState === "hidden",
    });
    poller.current = instance;
    const visibility = () => document.visibilityState === "hidden" ? instance.pause() : instance.resume();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      instance.stop();
      document.removeEventListener("visibilitychange", visibility);
      if (poller.current === instance) poller.current = null;
    };
  }, [scope, interval, visible]);
  return poller;
}
