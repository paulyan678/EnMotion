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

  it("keeps billing settlement separate from local Playground lifecycle and failures", async () => {
    vi.stubEnv("NEXT_PUBLIC_HYBRID_MODE", "true");
    apiClient.defaults.adapter = async (config) => {
      if (config.url?.includes("/account/usage")) {
        return response(config, {
          items: [{
            id: "usage-1",
            operation: "images.generations",
            model: "gpt-image-2",
            status: "settled",
            reserved_units: 10,
            settled_units: 8,
            created_at: "2026-07-29T01:00:00Z",
            settled_at: "2026-07-29T01:01:00Z",
          }],
        });
      }
      return response(config, [{
        id: "generation-1",
        mode: "t2v",
        model_id: "doubao-seedance-2-0-fast-260128",
        prompt: "A storm over the harbor",
        input_media: [],
        parameters: {
          resolution: "720p",
          aspect_ratio: "16:9",
          duration: 5,
        },
        batch_size: 1,
        outputs: [],
        status: "failed",
        error: "视频生成失败，请稍后重试。",
        created_at: "2026-07-29T02:00:00Z",
        updated_at: "2026-07-29T02:02:00Z",
      }]);
    };

    const activities = await apiCallsApi.list();

    expect(activities).toHaveLength(2);
    expect(activities.find((item) => item.activity_kind === "billing")).toMatchObject({
      id: "billing:usage-1",
      status: "completed",
      billing_status: "settled",
      managed_read_only: true,
    });
    expect(activities.find((item) => item.activity_kind === "generation")).toMatchObject({
      id: "playground:generation-1",
      status: "failed",
      source: "playground",
      error: "视频生成失败，请稍后重试。",
      source_context: {
        playground_generation_id: "generation-1",
      },
    });
  });

  it.each([
    {
      failingSource: "billing",
      shouldFail: (url: string) => url.includes("/account/usage"),
    },
    {
      failingSource: "Playground",
      shouldFail: (url: string) => url.includes("/playground/history"),
    },
  ])("rejects incomplete hybrid history when $failingSource fails", async ({ shouldFail }) => {
    vi.stubEnv("NEXT_PUBLIC_HYBRID_MODE", "true");
    apiClient.defaults.adapter = async (config) => {
      const url = config.url ?? "";
      if (shouldFail(url)) throw new Error("history source unavailable");
      if (url.includes("/account/usage")) return response(config, { items: [] });
      return response(config, []);
    };

    await expect(apiCallsApi.list()).rejects.toThrow("history source unavailable");
  });

  it("sorts merged hybrid activity before enforcing the requested limit", async () => {
    vi.stubEnv("NEXT_PUBLIC_HYBRID_MODE", "true");
    apiClient.defaults.adapter = async (config) => {
      if (config.url?.includes("/account/usage")) {
        return response(config, {
          items: [{
            id: "newest-billing",
            operation: "images.generations",
            model: "gpt-image-2",
            status: "settled",
            reserved_units: 10,
            settled_units: 8,
            created_at: "2026-07-29T04:00:00Z",
            settled_at: "2026-07-29T04:01:00Z",
          }],
        });
      }
      return response(config, [{
        id: "older-generation",
        mode: "t2i",
        model_id: "gpt-image-2",
        prompt: "An older harbor",
        input_media: [],
        parameters: { size: "1536x1024", quality: "auto" },
        batch_size: 1,
        outputs: [],
        status: "completed",
        created_at: "2026-07-29T01:00:00Z",
        updated_at: "2026-07-29T01:01:00Z",
      }]);
    };

    const activities = await apiCallsApi.list(1);

    expect(activities).toHaveLength(1);
    expect(activities[0]?.id).toBe("billing:newest-billing");
  });

  it("uses terminal lifecycle time for Playground sorting and duration after metadata edits", async () => {
    vi.stubEnv("NEXT_PUBLIC_HYBRID_MODE", "true");
    apiClient.defaults.adapter = async (config) => {
      if (config.url?.includes("/account/usage")) {
        return response(config, { items: [] });
      }
      const generation = (
        id: string,
        createdAt: string,
        updatedAt: string,
        finishedAt?: string,
      ) => ({
        id,
        mode: "t2i",
        model_id: "gpt-image-2",
        prompt: id,
        input_media: [],
        parameters: { size: "1536x1024", quality: "auto" },
        batch_size: 1,
        outputs: [],
        status: "completed",
        created_at: createdAt,
        updated_at: updatedAt,
        ...(finishedAt ? { finished_at: finishedAt } : {}),
      });
      return response(config, [
        generation(
          "metadata-edited",
          "2026-07-29T01:00:00Z",
          "2026-07-29T10:00:00Z",
          "2026-07-29T01:01:00Z",
        ),
        generation(
          "recent-completion",
          "2026-07-29T02:00:00Z",
          "2026-07-29T03:00:00Z",
          "2026-07-29T03:00:00Z",
        ),
        generation(
          "legacy-terminal",
          "2026-07-29T03:30:00Z",
          "2026-07-29T04:00:00Z",
        ),
      ]);
    };

    const activities = await apiCallsApi.list();

    expect(activities.map((item) => item.id)).toEqual([
      "playground:legacy-terminal",
      "playground:recent-completion",
      "playground:metadata-edited",
    ]);
    expect(activities.find((item) => item.id === "playground:metadata-edited")).toMatchObject({
      updated_at: "2026-07-29T01:01:00Z",
      finished_at: "2026-07-29T01:01:00Z",
      progress_steps: [
        expect.any(Object),
        expect.objectContaining({ finished_at: "2026-07-29T01:01:00Z" }),
      ],
    });
    expect(activities.find((item) => item.id === "playground:legacy-terminal")).toMatchObject({
      updated_at: "2026-07-29T04:00:00Z",
      finished_at: "2026-07-29T04:00:00Z",
    });
  });

  it("preserves failed and canceled billing outcomes instead of marking them completed", async () => {
    vi.stubEnv("NEXT_PUBLIC_HYBRID_MODE", "true");
    apiClient.defaults.adapter = async (config) => {
      if (config.url?.includes("/account/usage")) {
        return response(config, {
          items: [
            {
              id: "usage-failed",
              operation: "video.generations",
              model: "doubao-seedance-2-0-fast-260128",
              status: "failed",
              reserved_units: 10,
              settled_units: 0,
              created_at: "2026-07-29T01:00:00Z",
              settled_at: "2026-07-29T01:01:00Z",
            },
            {
              id: "usage-canceled",
              operation: "images.generations",
              model: "gpt-image-2",
              status: "cancelled",
              reserved_units: 10,
              settled_units: 0,
              created_at: "2026-07-29T02:00:00Z",
              settled_at: "2026-07-29T02:01:00Z",
            },
          ],
        });
      }
      return response(config, []);
    };

    const activities = await apiCallsApi.list();

    expect(activities.find((item) => item.id === "billing:usage-failed")).toMatchObject({
      activity_kind: "billing",
      billing_status: "failed",
      status: "failed",
    });
    expect(activities.find((item) => item.id === "billing:usage-canceled")).toMatchObject({
      activity_kind: "billing",
      billing_status: "cancelled",
      status: "canceled",
    });
  });

  it("downloads hybrid Playground output through authenticated media fetch", async () => {
    vi.stubEnv("NEXT_PUBLIC_HYBRID_MODE", "true");
    apiClient.defaults.adapter = async (config) => response(config, {
      id: "generation-1",
      mode: "t2i",
      model_id: "gpt-image-2",
      prompt: "A harbor",
      input_media: [],
      parameters: { size: "1536x1024", quality: "auto" },
      batch_size: 1,
      outputs: [{
        id: "output-1",
        media_path: "playground/images/harbor.png",
        media_type: "image",
        saved_to_library: false,
      }],
      status: "completed",
      created_at: "2026-07-29T02:00:00Z",
      updated_at: "2026-07-29T02:02:00Z",
    });
    const fetchMock = vi.fn(async () => new Response(
      new Blob(["persisted"], { type: "image/png" }),
      {
        status: 200,
        headers: { [WORKSPACE_RESPONSE_HEADER]: "workspace-alice" },
      },
    ));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      apiCallsApi.download("playground:generation-1", "output-1"),
    ).resolves.toMatchObject({ filename: "harbor.png" });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/files/playground/images/harbor.png"),
      expect.objectContaining({ credentials: "include" }),
    );
  });
});
