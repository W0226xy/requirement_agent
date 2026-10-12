import type { UserSession } from "./types";

export type SseEvent = { id?: string; event: string; data: Record<string, unknown> };

export async function readSse(
  path: string, session: UserSession, signal: AbortSignal,
  onEvent: (event: SseEvent) => void,
) {
  const response = await fetch(path, { signal, headers: {
    Accept: "text/event-stream", "X-Actor-ID": session.actorId, "X-Actor-Role": session.role,
  }});
  if (!response.ok || !response.body) throw new ApiError("实时连接失败", response.status);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let pending: Partial<SseEvent> = {};
  const flush = () => {
    if (pending.event && pending.data) onEvent(pending as SseEvent);
    pending = {};
  };
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let newline: number;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).replace(/\r$/, ""); buffer = buffer.slice(newline + 1);
      if (!line) { flush(); continue; }
      const separator = line.indexOf(":");
      const field = separator < 0 ? line : line.slice(0, separator);
      const valueText = separator < 0 ? "" : line.slice(separator + 1).replace(/^ /, "");
      if (field === "id") pending.id = valueText;
      if (field === "event") pending.event = valueText;
      if (field === "data") { try { pending.data = JSON.parse(valueText) as Record<string, unknown>; } catch { /* wait for valid event */ } }
    }
    if (done) break;
  }
}

type ApiErrorBody = {
  error?: { code?: string; message?: string };
  detail?: string | { msg?: string }[];
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
  session?: UserSession,
): Promise<T> {
  const headers = new Headers(options.headers);
  if (
    options.body &&
    !(options.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }
  if (session) {
    headers.set("X-Actor-ID", session.actorId);
    headers.set("X-Actor-Role", session.role);
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let body: ApiErrorBody = {};
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      // The status text remains the safe fallback for non-JSON proxy failures.
    }
    const validation = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg).filter(Boolean).join("; ")
      : body.detail;
    throw new ApiError(
      body.error?.message || validation || response.statusText || "请求失败",
      response.status,
      body.error?.code,
    );
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function queryString(values: Record<string, string | number | boolean | undefined>) {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  });
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}
