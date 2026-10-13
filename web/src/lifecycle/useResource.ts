import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { usePanelVisible } from "./visibility";
import { sameValue } from "./requests";

/** Keep same-resource content mounted during revalidation; never show another scope. */
export function useResource<T>(path: string, revision?: unknown) {
  const visible = usePanelVisible();
  const loaded = useRef<{ path: string; revision: unknown } | null>(null);
  const [snapshot, setSnapshot] = useState<{ path: string; value: T } | null>(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(true);
  useEffect(() => {
    if (!visible || (loaded.current?.path === path && Object.is(loaded.current.revision, revision))) return;
    const controller = new AbortController();
    setError(""); setRefreshing(true);
    void api<T>(path, "GET", undefined, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) {
        loaded.current = { path, revision };
        setSnapshot(old => old?.path === path ? sameValue(old, { path, value }) : { path, value });
      }
    }).catch(error => {
      if (!controller.signal.aborted) setError(error instanceof Error ? error.message : String(error));
    }).finally(() => { if (!controller.signal.aborted) setRefreshing(false); });
    return () => controller.abort();
  }, [path, revision, visible]);
  const value = snapshot?.path === path ? snapshot.value : null;
  return { value, error, refreshing, loading: !value && refreshing };
}
