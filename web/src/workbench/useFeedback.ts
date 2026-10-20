import { useEffect, useRef, useState } from "react";
import type { ProjectWorkspace } from "./useProjectWorkspace";

export function useFeedback() {
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 4000);
    return () => clearTimeout(timer);
  }, [notice]);
  return { error, setError, notice, setNotice };
}
export function useGuardedAction(workspace: ProjectWorkspace, setError: (message: string) => void) {
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  async function guarded(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    const epoch = workspace.capture();
    setBusy(true);
    setError("");
    try { await action(); }
    catch (error) { if (workspace.isCurrent(epoch)) setError(String((error as Error).message)); }
    finally { busyRef.current = false; setBusy(false); }
  }
  return { busy, guarded };
}
