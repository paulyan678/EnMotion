import axiosLibrary, { type AxiosInstance } from "axios";
import { API_URL } from "@/lib/apiUrl";
import { getRuntimeLocalNonce, isServerModeEnabled } from "@/lib/serverMode";
import { getWorkspaceStorageScope } from "@/lib/workspaceStorage";

export const AUTH_REQUIRED_EVENT = "enmotion:auth-required";
export const WORKSPACE_RESPONSE_HEADER = "x-enmotion-workspace-id";
export const CSRF_COOKIE_NAME = "enmotion_csrf";
export const CSRF_HEADER_NAME = "X-CSRF-Token";
export const LOCAL_NONCE_HEADER_NAME = "X-EnMotion-Local-Nonce";
const SAFE_METHODS = new Set(["get", "head", "options"]);
let responseCsrfToken: string | null = null;

type ScopedRequestConfig = {
  _enmotionWorkspaceScope?: string | null;
};

export function setCsrfToken(token: string | null | undefined): void {
  responseCsrfToken = token?.trim() || null;
}

function cookieValue(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${encodeURIComponent(name)}=`;
  for (const part of document.cookie.split(";")) {
    const value = part.trim();
    if (value.startsWith(prefix)) {
      try {
        return decodeURIComponent(value.slice(prefix.length));
      } catch {
        return value.slice(prefix.length);
      }
    }
  }
  return null;
}

export function getCsrfToken(): string | null {
  return responseCsrfToken || cookieValue(CSRF_COOKIE_NAME);
}

function announceAuthenticationRequired(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
  }
}

function requestPath(url: string | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url, API_URL).pathname.replace(/\/+$/, "") || "/";
  } catch {
    return url.split("?", 1)[0].replace(/\/+$/, "") || "/";
  }
}

function isAuthProbe(url: string | undefined): boolean {
  const path = requestPath(url);
  return path === "/auth/session" || path === "/auth/me" || path === "/auth/login";
}

/** Public backend probes intentionally have no authenticated workspace header. */
function isPublicProbe(url: string | undefined): boolean {
  const path = requestPath(url);
  return path === "/health" || path === "/ready";
}

function isResponseScopeExempt(url: string | undefined): boolean {
  return isAuthProbe(url) || isPublicProbe(url);
}

export function createApiClient(): AxiosInstance {
  const client = axiosLibrary.create({
    withCredentials: isServerModeEnabled(),
  });

  client.interceptors.request.use((config) => {
    const localNonce = getRuntimeLocalNonce();
    if (localNonce) config.headers.set(LOCAL_NONCE_HEADER_NAME, localNonce);
    if (!isServerModeEnabled()) return config;
    config.withCredentials = true;
    (config as typeof config & ScopedRequestConfig)._enmotionWorkspaceScope = getWorkspaceStorageScope();
    const method = (config.method || "get").toLowerCase();
    if (!SAFE_METHODS.has(method)) {
      const token = getCsrfToken();
      if (token) config.headers.set(CSRF_HEADER_NAME, token);
    }
    return config;
  });

  client.interceptors.response.use(
    (response) => {
      if (isServerModeEnabled()) {
        const requestScope = (response.config as typeof response.config & ScopedRequestConfig)._enmotionWorkspaceScope;
        const responseScope = response.headers[WORKSPACE_RESPONSE_HEADER] as string | undefined;
        if (
          !isResponseScopeExempt(response.config.url)
          && (requestScope !== getWorkspaceStorageScope() || !responseScope || responseScope !== requestScope)
        ) {
          return Promise.reject(new axiosLibrary.CanceledError("请求执行期间，当前工作区已切换"));
        }
      }
      return response;
    },
    (error: unknown) => {
      if (axiosLibrary.isAxiosError(error)) {
        const requestScope = (error.config as (typeof error.config & ScopedRequestConfig) | undefined)?._enmotionWorkspaceScope;
        if (isServerModeEnabled() && requestScope !== getWorkspaceStorageScope()) {
          return Promise.reject(new axiosLibrary.CanceledError("请求执行期间，当前工作区已切换"));
        }
        if (error.response?.status === 401 && !isAuthProbe(error.config?.url)) {
          announceAuthenticationRequired();
        }
      }
      return Promise.reject(error);
    },
  );

  return client;
}

export const apiClient = createApiClient();

function isBackendUrl(input: RequestInfo | URL): boolean {
  try {
    const value = typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;
    const target = new URL(value, typeof window === "undefined" ? API_URL : window.location.href);
    const backend = new URL(API_URL, target.href);
    return target.origin === backend.origin;
  } catch {
    return true;
  }
}

/** Fetch counterpart used by streaming and file-upload endpoints. */
export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const targetsBackend = isBackendUrl(input);
  const requestUrl = typeof input === "string"
    ? input
    : input instanceof URL
      ? input.toString()
      : input.url;
  const requestScope = getWorkspaceStorageScope();
  const headers = new Headers(init.headers);
  const method = (init.method || "GET").toUpperCase();
  const localNonce = getRuntimeLocalNonce();
  if (targetsBackend && localNonce) headers.set(LOCAL_NONCE_HEADER_NAME, localNonce);
  if (isServerModeEnabled() && targetsBackend && !SAFE_METHODS.has(method.toLowerCase())) {
    const token = getCsrfToken();
    if (token) headers.set(CSRF_HEADER_NAME, token);
  }
  const response = await fetch(input, {
    ...init,
    headers,
    credentials: isServerModeEnabled() && targetsBackend ? "include" : init.credentials,
  });
  if (isServerModeEnabled() && targetsBackend && requestScope !== getWorkspaceStorageScope()) {
    throw new DOMException("请求执行期间，当前工作区已切换", "AbortError");
  }
  // A protected endpoint may omit the workspace header when its session has
  // expired. Revoke local auth state before considering response scoping.
  if (response.status === 401 && targetsBackend && !isAuthProbe(requestUrl)) {
    announceAuthenticationRequired();
    return response;
  }
  if (
    isServerModeEnabled()
    && targetsBackend
    && !isResponseScopeExempt(requestUrl)
    && response.headers.get(WORKSPACE_RESPONSE_HEADER) !== requestScope
  ) {
    throw new DOMException("服务器会话属于其他工作区", "AbortError");
  }
  return response;
}
