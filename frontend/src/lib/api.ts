export const apiUrl = import.meta.env?.VITE_API_URL ?? (import.meta.env?.DEV ? "http://127.0.0.1:8000" : "");
export const anonymousTokenKey = "changelorg:anonymous-token:v1";
export const identityInvalidatedEvent = "changelorg:identity-invalidated";
let identityRequest = 0;
let access: { owner: string; token: string; authenticated: boolean } | null = null;
const reads = new Map<string, { expires: number; value: unknown }>();
let readGeneration = 0;
export function clearReadCache() { reads.clear(); readGeneration++; }

export function invalidateIdentity() {
  identityRequest++;
  access = null;
  clearReadCache();
}

export class ApiError extends Error {
  constructor(message: string, readonly status = 0) {
    super(message);
  }
}

function anonymousToken() {
  try {
    const stored = localStorage.getItem(anonymousTokenKey);
    if (stored !== null) {
      const parts = stored.split("-");
      const valid = parts.length === 5 && parts.every((part, index) =>
        part.length === [8, 4, 4, 4, 12][index] && [...part.toLowerCase()].every((char) => "0123456789abcdef".includes(char)),
      ) && parts[2][0] === "4" && "89ab".includes(parts[3][0].toLowerCase());
      if (!valid) throw new Error();
      return stored;
    }
    const token = crypto.randomUUID();
    localStorage.setItem(anonymousTokenKey, token);
    return token;
  } catch {
    throw new ApiError("Cannot access your browser identity. Allow localStorage and use HTTPS. Do not clear existing browser data.");
  }
}

export function errorMessage(cause: unknown) {
  return cause instanceof ApiError ? cause.message : "The request failed. Retry or reload to check the server state.";
}

export async function resetSession(): Promise<void> {
  let response: Response;
  try {
    // Session recovery needs no account access or browser token, even when either is invalid.
    response = await fetch(`${apiUrl}/session/reset`, {
      method: "POST", headers: { "X-Changelorg-Request": "1" }, credentials: "include",
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(90_000),
    });
  } catch {
    throw new ApiError("Cannot confirm the session reset. Check your connection, then retry account access or reset the session again.");
  }
  if (response.status !== 204) {
    throw new ApiError(`The server could not confirm the session reset (HTTP ${response.status}). Retry account access or reset the session again.`, response.status);
  }
}

export async function api<T>(path: string, options: { owner: string | null; method?: "GET" | "POST" | "PATCH" | "DELETE"; body?: unknown; signal?: AbortSignal }): Promise<T> {
  const method = options.method ?? "GET";
  if (!options.owner && (path !== "/me" || method !== "GET")) {
    throw new ApiError("Account access is not ready. Reload before you retry.");
  }
  const token = anonymousToken();
  const expected = access;
  if (options.owner && (!expected || expected.owner !== options.owner || expected.token !== token)) {
    if (expected && expected.token !== token) {
      invalidateIdentity();
      window.dispatchEvent(new Event(identityInvalidatedEvent));
    }
    throw new ApiError("Your account changed. Reload before you retry. No request was sent.", 412);
  }
  const headers: Record<string, string> = { "X-Anonymous-Token": token, Accept: "application/json" };
  const cacheable = method === "GET" && Boolean(options.owner) && ["/changes", "/profiles", "/sources", "/plugins", "/catalog"].includes(path.split("?")[0]);
  const cacheKey = `${options.owner}:${token}:${path}`;
  if (method !== "GET") clearReadCache();
  const generation = readGeneration;
  const cached = cacheable ? reads.get(cacheKey) : undefined;
  if (cached && cached.expires > Date.now() && !options.signal?.aborted) return structuredClone(cached.value) as T;
  // The server must compare this owner with the request identity before it accesses account data.
  if (options.owner) headers["X-Changelorg-Owner"] = options.owner;
  if (method !== "GET") headers["X-Changelorg-Request"] = "1";
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const timeout = AbortSignal.timeout(90_000);
  let response: Response;
  try {
    response = await fetch(`${apiUrl}${path}`, {
      method, headers, credentials: "include", signal: options.signal ? AbortSignal.any([options.signal, timeout]) : timeout,
      cache: "no-store",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    if (method !== "GET") clearReadCache();
    if (timeout.aborted) throw new ApiError("The request timed out. Reload to check the server state before you retry a save.");
    throw new ApiError("Cannot reach the server. Check your connection, then retry or reload to check the server state.");
  }
  if (method !== "GET") clearReadCache();
  if (options.owner && (access !== expected || localStorage.getItem(anonymousTokenKey) !== token)) {
    throw new ApiError("Your account changed. The response from the previous account was discarded.", 412);
  }
  if (!response.ok) {
    if (options.owner && (response.status === 401 || response.status === 412)) {
      invalidateIdentity();
      window.dispatchEvent(new Event(identityInvalidatedEvent));
    }
    const messages: Record<number, string> = {
      401: "Your identity is not accepted. Reload and sign in again. No browser identity fallback was used.",
      403: "This request is not allowed for your identity. Reload to check your account.",
      404: "This item no longer exists, or the server does not support this request. Reload to check.",
      409: "This request conflicts with existing data or your account changed. Reload the page and check your account and names.",
      412: "Your account changed. Reload the page before you retry. No changes were applied.",
      422: "The server rejected these values. Check the URL, profile, plugin, and other fields.",
      429: "Too many requests. Wait before you retry.",
      502: "The source did not return a usable response. Check its URL or try again later.",
    };
    // Response bodies can contain source credentials or proxy details. Never display them.
    throw new ApiError(messages[response.status] ?? `The server could not complete the request (HTTP ${response.status}). Reload before retrying a save.`, response.status);
  }
  if (response.status === 204) return undefined as T;
  let value: T;
  try {
    value = await response.json() as T;
  } catch {
    throw new ApiError("The server returned an invalid response. Reload to check the server state.");
  }
  if (options.owner && (access !== expected || localStorage.getItem(anonymousTokenKey) !== token)) {
    throw new ApiError("Your account changed. The response from the previous account was discarded.", 412);
  }
  if (cacheable && generation === readGeneration && !options.signal?.aborted) {
    if (reads.size >= 32) reads.delete(reads.keys().next().value!);
    reads.set(cacheKey, { expires: Date.now() + 15_000, value: structuredClone(value) });
  }
  return value;
}

export type Identity = { id: string; authenticated: boolean; login: string | null; auth_enabled: boolean; anonymous_has_data: boolean };
export type Profile = { name: string; source_count: number };
export type SourceCreate = { name: string; plugin: string; config: Record<string, unknown>; enabled: boolean };
export type Source = SourceCreate & { config: Record<string, unknown> & { profile: string }; id: number; owner_id: string; created_at: string; updated_at: string };
export type Plugin = { key: string; name: string; description: string; config_schema: { properties?: Record<string, { enum?: unknown[] }> } };
export type TimeWindow = { start: string; end: string };

export async function getIdentity() {
  const ticket = ++identityRequest;
  try {
    const token = anonymousToken();
    const value = await api<unknown>("/me", { owner: null });
    if (ticket !== identityRequest || localStorage.getItem(anonymousTokenKey) !== token) {
      throw new ApiError("Account access changed during the request. Retry account access.", 412);
    }
    if (!value || typeof value !== "object" || Array.isArray(value) ||
      !("id" in value) || typeof value.id !== "string" || !value.id ||
      !("authenticated" in value) || typeof value.authenticated !== "boolean" ||
      !("login" in value) || (value.login !== null && typeof value.login !== "string") ||
      !("auth_enabled" in value) || typeof value.auth_enabled !== "boolean" ||
      !("anonymous_has_data" in value) || typeof value.anonymous_has_data !== "boolean") {
      throw new ApiError("The server returned an invalid identity. Access is blocked until it returns a valid identity.");
    }
    if (access?.owner !== value.id || access.token !== token || access.authenticated !== value.authenticated) {
      clearReadCache();
      access = { owner: value.id, token, authenticated: value.authenticated };
    }
    return value as Identity;
  } catch (cause) {
    if (ticket === identityRequest) { access = null; clearReadCache(); }
    throw cause;
  }
}
