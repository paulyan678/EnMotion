// @vitest-environment jsdom

import type { InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  SERIES_CREATE_TIMEOUT_MS,
  WORKSPACE_LIST_TIMEOUT_MS,
} from "@/lib/api";
import { WORKSPACE_RESPONSE_HEADER, apiClient } from "@/lib/httpClient";
import { setWorkspaceStorageScope } from "@/lib/workspaceStorage";

const originalAdapter = apiClient.defaults.adapter;
const workspaceId = "workspace-quick-request-test";

function response(config: InternalAxiosRequestConfig, data: unknown) {
  return {
    data,
    status: 200,
    statusText: "OK",
    headers: { [WORKSPACE_RESPONSE_HEADER]: workspaceId },
    config,
  };
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_SERVER_MODE", "true");
  setWorkspaceStorageScope(workspaceId);
});

afterEach(() => {
  apiClient.defaults.adapter = originalAdapter;
  setWorkspaceStorageScope(null);
  vi.unstubAllEnvs();
});

describe("quick workspace request timeouts", () => {
  it("bounds both Series creation entry points", async () => {
    const requests: InternalAxiosRequestConfig[] = [];
    apiClient.defaults.adapter = async (config) => {
      requests.push(config);
      return response(config, { id: `series-${requests.length}` });
    };

    await api.createSeriesV2("Series V2");
    await api.createSeries("Series");

    expect(requests).toHaveLength(2);
    expect(requests.every((request) => request.timeout === SERIES_CREATE_TIMEOUT_MS)).toBe(true);
  });

  it("bounds project, Series, and episode list requests", async () => {
    const requests: InternalAxiosRequestConfig[] = [];
    apiClient.defaults.adapter = async (config) => {
      requests.push(config);
      return response(config, []);
    };

    await api.getProjects();
    await api.listSeries();
    await api.getSeriesEpisodes("series-1");

    expect(requests).toHaveLength(3);
    expect(requests.every((request) => request.timeout === WORKSPACE_LIST_TIMEOUT_MS)).toBe(true);
  });
});
