import type { UserSession } from "./types";

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
