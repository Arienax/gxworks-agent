// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useEffect, useState } from "react";
import type { Session } from "../api/client";
import { api, setSession } from "../api/client";

/**
 * The loopback workbench opens its own operator session, so the page never asks
 * for or reads a credential; a failure here means the service is unreachable.
 */
export function useSession() {
  const [session, updateSession] = useState<Session | null>(null);
  const [connecting, setConnecting] = useState(true);
  useEffect(() => {
    let stopped = false;
    (async () => {
      try {
        const value = await api<Session>("/session");
        if (!stopped) {
          setSession(value);
          updateSession(value);
        }
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

  async function login() {
    const value = await api<Session>("/session", "POST", { token: "" });
    setSession(value);
    updateSession(value);
  }
  return { session, connecting, token: "", setToken: () => {}, login };
}
