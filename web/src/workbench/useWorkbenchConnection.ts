// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { useEffect, useState } from "react";
import type { WorkbenchConnection } from "../api/client";
import { api } from "../api/client";

export function useWorkbenchConnection() {
  const [connection, setConnection] = useState<WorkbenchConnection | null>(null);
  const [connecting, setConnecting] = useState(true);
  useEffect(() => {
    let stopped = false;
    (async () => {
      try {
        const value = await api<WorkbenchConnection>("/workspace");
        if (!stopped) setConnection(value);
      } catch {
        /* the shell reports the unreachable service */
      } finally {
        if (!stopped) setConnecting(false);
      }
    })();
    return () => {
      stopped = true;
    };
  }, []);

  return { connection, connecting, setConnection };
}
