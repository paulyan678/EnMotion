// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { API_URL } from "@/lib/apiUrl";
import {
  AUTH_REQUIRED_EVENT,
  LOCAL_NONCE_HEADER_NAME,
  WORKSPACE_RESPONSE_HEADER,
  apiClient,
  apiFetch,
  createApiClient,
  setCsrfToken,
} from "@/lib/httpClient";
import { setWorkspaceStorageScope } from "@/lib/workspaceStorage";

describe("server-mode HTTP client", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_SERVER_MODE", "true");
    setCsrfToken(null);
    setWorkspaceStorageScope(null);
    document.cookie = "enmotion_csrf=; Max-Age=0; Path=/";
    window.__ENMOTION_RUNTIME_CONFIG__ = undefined;
  });

  it("sends cookies and the CSRF token on mutations", async () => {
    setWorkspaceStorageScope("workspace-alice");
    setCsrfToken("csrf-response-token");
    const client = createApiClient();
    client.defaults.adapter = async (config) => ({
      data: {},
      status: 200,
      statusText: "OK",
      headers: { [WORKSPACE_RESPONSE_HEADER]: "workspace-alice" },
      config,
    });

    const response = await client.post("/projects", { title: "Private" });

    expect(response.config.withCredentials).toBe(true);
    expect(response.config.headers.get("X-CSRF-Token")).toBe("csrf-response-token");
  });

  it("does not add a CSRF header to safe reads", async () => {
    setWorkspaceStorageScope("workspace-alice");
    setCsrfToken("csrf-response-token");
    const client = createApiClient();
    client.defaults.adapter = async (config) => ({
      data: {},
      status: 200,
      statusText: "OK",
      headers: { [WORKSPACE_RESPONSE_HEADER]: "workspace-alice" },
      config,
    });

    const response = await client.get("/projects");
    expect(response.config.headers.get("X-CSRF-Token")).toBeUndefined();
  });

  it("attaches the per-launch local nonce to Axios and fetch sidecar requests", async () => {
    window.__ENMOTION_RUNTIME_CONFIG__ = { localNonce: "launch-nonce" };
    const client = createApiClient();
    client.defaults.adapter = async (config) => ({
      data: {},
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    });

    const axiosResponse = await client.get("/health");
    expect(axiosResponse.config.headers.get(LOCAL_NONCE_HEADER_NAME)).toBe("launch-nonce");

    const fetchMock = vi.fn().mockResolvedValue({
      status: 200,
      headers: new Headers(),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);
    try {
      await apiFetch(`${API_URL}/health`);
      expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get(LOCAL_NONCE_HEADER_NAME)).toBe("launch-nonce");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("allows the public health check to omit a workspace response header", async () => {
    setWorkspaceStorageScope("workspace-alice");
    const previousAdapter = apiClient.defaults.adapter;
    apiClient.defaults.adapter = async (config) => ({
      data: { ok: true, time: 1 },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    });

    try {
      await expect(api.healthCheck()).resolves.toMatchObject({ ok: true });
    } finally {
      apiClient.defaults.adapter = previousAdapter;
    }
  });

  it("broadcasts an expired apiFetch session before checking the missing scope header", async () => {
    setWorkspaceStorageScope("workspace-alice");
    const listener = vi.fn();
    window.addEventListener(AUTH_REQUIRED_EVENT, listener);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      status: 401,
      headers: new Headers(),
    } as Response));

    try {
      const response = await apiFetch(`${API_URL}/projects`);
      expect(response.status).toBe(401);
      expect(listener).toHaveBeenCalledOnce();
    } finally {
      window.removeEventListener(AUTH_REQUIRED_EVENT, listener);
      vi.unstubAllGlobals();
    }
  });

  it("cancels apiFetch responses from a different workspace", async () => {
    setWorkspaceStorageScope("workspace-alice");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      status: 200,
      headers: new Headers({ [WORKSPACE_RESPONSE_HEADER]: "workspace-bob" }),
    } as Response));

    try {
      await expect(apiFetch(`${API_URL}/projects`)).rejects.toMatchObject({ name: "AbortError" });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("cancels Axios responses from a different workspace", async () => {
    setWorkspaceStorageScope("workspace-alice");
    const client = createApiClient();
    client.defaults.adapter = async (config) => ({
      data: {},
      status: 200,
      statusText: "OK",
      headers: { [WORKSPACE_RESPONSE_HEADER]: "workspace-bob" },
      config,
    });

    await expect(client.get("/projects")).rejects.toMatchObject({ code: "ERR_CANCELED" });
  });

  it("broadcasts protected-request 401 responses to the auth gate", async () => {
    const listener = vi.fn();
    window.addEventListener(AUTH_REQUIRED_EVENT, listener);
    const client = createApiClient();
    client.defaults.adapter = async (config) => Promise.reject({
      isAxiosError: true,
      config,
      response: { status: 401 },
    });

    await expect(client.get("/projects")).rejects.toMatchObject({ isAxiosError: true });
    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener(AUTH_REQUIRED_EVENT, listener);
  });
});
