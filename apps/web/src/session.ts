import { createContext, useContext } from "react";

import type { UserSession } from "./types";

const STORAGE_KEY = "requirement-agent-session";

export const SessionContext = createContext<UserSession | null>(null);

export function loadSession(): UserSession | null {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (!stored) return null;
  try {
    return JSON.parse(stored) as UserSession;
  } catch {
    localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

export function saveSession(session: UserSession | null) {
  if (session) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
}

export function useSession(): UserSession {
  const session = useContext(SessionContext);
  if (!session) throw new Error("User session is unavailable");
  return session;
}
