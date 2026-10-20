import { useEffect, useState } from "react";
import type { Session } from "../api/client";
import { api, setSession } from "../api/client";

let bootstrapToken = new URLSearchParams(location.hash.slice(1)).get("token") || "";
if (bootstrapToken) history.replaceState(null, "", location.pathname + location.search);

export function useSession() {
  const [session, updateSession] = useState<Session | null>(null);
  const [connecting, setConnecting] = useState(true);
  const [token, setToken] = useState("");
  useEffect(() => {
    let stopped = false;
    const credential = bootstrapToken;
    bootstrapToken = "";
    (async () => {
      try {
        const value = credential
          ? await api<Session>("/session", "POST", { token: credential })
          : await api<Session>("/session");
        if (!stopped) {
          setSession(value);
          updateSession(value);
        }
      } catch {
        /* login form handles absent/expired session */
      } finally {
        if (!stopped) setConnecting(false);
      }
    })();
    return () => {
      stopped = true;
    };
  }, []);

  async function login() {
    const value = await api<Session>("/session", "POST", { token });
    setSession(value);
    updateSession(value);
    setToken("");
  }
  return { session, connecting, token, setToken, login };
}
