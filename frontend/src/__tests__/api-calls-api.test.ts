// @vitest-environment jsdom

import type { InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiCallsApi } from "@/lib/api";
import { WORKSPACE_RESPONSE_HEADER, apiClient } from "@/lib/httpClient";
import { setWorkspaceStorageScope } from "@/lib/workspaceStorage";

const originalAdapter = apiClient.defaults.adapter;

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_SERVER_MODE", "true");
  setWorkspaceStorageScope("workspace-alice");
});

function response(config: InternalAxiosRequestConfig, data: unknown, status = 200) {
  return {
    data,
    status,
    statusText: "OK",
    headers: { [WORKSPACE_RESPONSE_HEADER]: "workspace-alice" },
    config,
  };
}

afterEach(() => {
  apiClient.defaults.adapter = originalAdapter;
  setWorkspaceStorageScope(null);
  vi.unstubAllEnvs();
});

describe("central API activity client", () => {
  it("lists activity and requests all available history", async () => {
    let request: InternalAxiosRequestConfig | undefined;
    apiClient.defaults.adapter = async (config) => {
      request = config;
      return response(config, [{ id: "job-1", status: "running" }]);
    };

    await expect(apiCallsApi.list()).resolves.toEqual([{ id: "job-1", status: "running" }]);
    expect(request?.url).toContain("/jobs");
    expect(request?.params).toEqual({ limit: 200 });
  });

  it("uses dedicated cancel, retry, and dismiss job actions", async () => {
    const requests: Array<{ method?: string; url?: string }> = [];
    apiClient.defaults.adapter = async (config) => {
      requests.push({ method: config.method, url: config.url });
      if (config.url?.endsWith("/retry")) {
        return response(config, { id: "job-1", status: "queued" });
      }
      if (config.url?.endsWith("/cancel")) {
        return response(config, {
          id: "job-1",
          status: "failed",
          error: "Provider request timed out",
        });
      }
      return response(config, undefined);
    };

    await expect(apiCallsApi.cancel("job-1")).resolves.toMatchObject({
      status: "failed",
      error: "Provider request timed out",
    });
    await expect(apiCallsApi.retry("job-1")).resolves.toMatchObject({ status: "queued" });
    await apiCallsApi.dismiss("job-1");

    expect(requests).toEqual([
      { method: "post", url: expect.stringContaining("/jobs/job-1/cancel") },
      { method: "post", url: expect.stringContaining("/jobs/job-1/retry") },
      { method: "delete", url: expect.stringContaining("/jobs/job-1") },
    ]);
  });

  it("downloads a persisted output with the authenticated API client and server filename", async () => {
    let request: InternalAxiosRequestConfig | undefined;
    const blob = new Blob(["persisted image"], { type: "image/png" });
    apiClient.defaults.adapter = async (config) => {
      request = config;
      return {
        ...response(config, blob),
        headers: {
          [WORKSPACE_RESPONSE_HEADER]: "workspace-alice",
          "content-disposition": "attachment; filename=\"harbor.png\"",
        },
      };
    };

    await expect(apiCallsApi.download("job-1", "output/1")).resolves.toEqual({
      blob,
      filename: "harbor.png",
    });
    expect(request?.url).toContain("/jobs/job-1/outputs/output%2F1/download");
    expect(request?.responseType).toBe("blob");
  });
});
