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

  it("downloads hybrid activity media directly from the authenticated workspace route", async () => {
    const fetchMock = vi.fn(async () => new Response(
      new Blob(["hybrid video"], { type: "video/mp4" }),
      {
        status: 200,
        headers: { [WORKSPACE_RESPONSE_HEADER]: "workspace-alice" },
      },
    ));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      apiCallsApi.download(
        "hybrid:video-task-1",
        "video-task-1",
        "video/video-task-1.mp4",
      ),
    ).resolves.toMatchObject({ filename: "video-task-1.mp4" });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/files/video/video-task-1.mp4"),
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("lists only generation activity and leaves account settlement in account controls", async () => {
    vi.stubEnv("NEXT_PUBLIC_HYBRID_MODE", "true");
    const requests: string[] = [];
    apiClient.defaults.adapter = async (config) => {
      requests.push(config.url ?? "");
      if (config.url?.includes("/account/usage")) {
        throw new Error("account ledger must not be queried by API Calls");
      }
      if (config.url?.includes("/activity/history")) return response(config, []);
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
        error: "视频服务商拒绝了生成结果，因为输出可能触发内容或版权政策。",
        error_code: "output_video_policy",
        error_diagnostic: "阶段：处理视频任务",
        compiled_request: {
          compiler_version: "1.0",
          compiled_request_id: "genreq-playground-1",
          checksum: "a".repeat(64),
          category: "video",
          mode: "t2v",
          source: "playground",
          user_prompt: "A storm over the harbor",
          prompt_parts: [],
          target: { surface: "playground" },
          provider_requests: [{
            phase: "video",
            model: "doubao-seedance-2-0-fast-260128",
            prompt: "A storm over the harbor",
            parameters: { resolution: "720p", duration: 5 },
            input_media: [],
          }],
        },
        created_at: "2026-07-29T02:00:00Z",
        updated_at: "2026-07-29T02:02:00Z",
      }]);
    };

    const activities = await apiCallsApi.list();

    expect(activities).toHaveLength(1);
    expect(activities[0]).toMatchObject({
      id: "playground:generation-1",
      status: "failed",
      source: "playground",
      error: "视频服务商拒绝了生成结果，因为输出可能触发内容或版权政策。",
      error_code: "output_video_policy",
      error_diagnostic: "阶段：处理视频任务",
      compiled_request: expect.objectContaining({
        compiled_request_id: "genreq-playground-1",
        checksum: "a".repeat(64),
      }),
      source_context: {
        playground_generation_id: "generation-1",
      },
    });
    expect(requests.some((url) => url.includes("/account/usage"))).toBe(false);
  });

  it.each([
    {
      failingSource: "Playground",
      shouldFail: (url: string) => url.includes("/playground/history"),
    },
    {
      failingSource: "workspace activity",
      shouldFail: (url: string) => url.includes("/activity/history"),
    },
  ])("rejects incomplete hybrid history when $failingSource fails", async ({ shouldFail }) => {
    vi.stubEnv("NEXT_PUBLIC_HYBRID_MODE", "true");
    apiClient.defaults.adapter = async (config) => {
      const url = config.url ?? "";
      if (shouldFail(url)) throw new Error("history source unavailable");
      return response(config, []);
    };

    await expect(apiCallsApi.list()).rejects.toThrow("history source unavailable");
  });

  it("sorts merged hybrid activity before enforcing the requested limit", async () => {
    vi.stubEnv("NEXT_PUBLIC_HYBRID_MODE", "true");
    apiClient.defaults.adapter = async (config) => {
      if (config.url?.includes("/activity/history")) {
        return response(config, [{
          id: "hybrid:newest-local",
          task_id: "newest-local",
          type: "storyboard_render",
          status: "completed",
          category: "image",
          source: "workspace",
          progress: 100,
          attempts: 1,
          created_at: "2026-07-29T04:00:00Z",
          updated_at: "2026-07-29T04:01:00Z",
          finished_at: "2026-07-29T04:01:00Z",
          managed_read_only: true,
        }]);
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
    expect(activities[0]?.id).toBe("hybrid:newest-local");
  });

  it("uses terminal lifecycle time for Playground sorting and duration after metadata edits", async () => {
    vi.stubEnv("NEXT_PUBLIC_HYBRID_MODE", "true");
    apiClient.defaults.adapter = async (config) => {
      if (config.url?.includes("/activity/history")) return response(config, []);
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

  it("merges identifiable hybrid asset activity with bounded source requests", async () => {
    vi.stubEnv("NEXT_PUBLIC_HYBRID_MODE", "true");
    const requests: InternalAxiosRequestConfig[] = [];
    apiClient.defaults.adapter = async (config) => {
      requests.push(config);
      if (config.url?.includes("/playground/history")) {
        return response(config, []);
      }
      return response(config, [{
        id: "hybrid:asset-task",
        task_id: "asset-task",
        type: "series_asset",
        status: "completed",
        category: "image",
        source: "workspace",
        progress: 100,
        detail: "守塔人",
        prompt: "全身角色设定图",
        model_name: "gpt-image-2",
        attempts: 1,
        created_at: "2026-07-30T01:00:00Z",
        updated_at: "2026-07-30T01:01:00Z",
        finished_at: "2026-07-30T01:01:00Z",
        managed_read_only: true,
      }]);
    };

    const activities = await apiCallsApi.list();

    expect(activities).toHaveLength(1);
    expect(activities[0]).toMatchObject({
      id: "hybrid:asset-task",
      detail: "守塔人",
      prompt: "全身角色设定图",
    });
    expect(requests).toHaveLength(2);
    expect(requests.every((request) => request.timeout === 15_000)).toBe(true);
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
