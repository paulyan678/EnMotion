// @vitest-environment jsdom

import type { InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, GenerationTaskError, waitForDurableJob } from "@/lib/api";
import { WORKSPACE_RESPONSE_HEADER, apiClient } from "@/lib/httpClient";
import { setWorkspaceStorageScope } from "@/lib/workspaceStorage";

const originalAdapter = apiClient.defaults.adapter;

function response(config: InternalAxiosRequestConfig, data: unknown) {
    return {
        data,
        status: 200,
        statusText: "OK",
        headers: { [WORKSPACE_RESPONSE_HEADER]: "workspace-alice" },
        config,
    };
}

describe("durable server job API compatibility", () => {
    beforeEach(() => {
        vi.stubEnv("NEXT_PUBLIC_SERVER_MODE", "true");
        setWorkspaceStorageScope("workspace-alice");
    });

    afterEach(() => {
        delete window.__ENMOTION_RUNTIME_CONFIG__;
        apiClient.defaults.adapter = originalAdapter;
        vi.restoreAllMocks();
        vi.unstubAllGlobals();
    });

    it.each([
        ["generate storyboard", () => api.generateStoryboard("project-1")],
        ["generate video", () => api.generateVideo("project-1")],
        ["render a storyboard frame", () => api.renderFrame("project-1", "frame-1", {}, "prompt")],
        ["merge videos", () => api.mergeVideos("project-1")],
        ["preview a dub", () => api.previewDub("project-1", "frame-1", "video-1")],
    ])("polls and preserves the project response for %s", async (_label, invoke) => {
        const requestedUrls: string[] = [];
        apiClient.defaults.adapter = async (config) => {
            requestedUrls.push(config.url || "");
            if (config.url?.includes("/jobs/job-1")) {
                return response(config, {
                    task_id: "job-1",
                    status: "completed",
                    result: { script_id: "project-1" },
                });
            }
            if (config.method === "get" && config.url?.includes("/projects/project-1")) {
                return response(config, {
                    id: "project-1",
                    title: "Private project",
                    original_text: "source text",
                });
            }
            return response(config, { task_id: "job-1", status: "queued" });
        };

        await expect(invoke()).resolves.toMatchObject({
            id: "project-1",
            originalText: "source text",
        });
        expect(requestedUrls.some((url) => url.includes("/jobs/job-1"))).toBe(true);
        expect(requestedUrls.at(-1)).toContain("/projects/project-1");
    });

    it("returns the export payload produced by a durable job", async () => {
        apiClient.defaults.adapter = async (config) => response(config, {
            task_id: "export-job",
            status: "completed",
            result: { url: "export/project.mp4" },
        });
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
            JSON.stringify({ task_id: "export-job", status: "queued" }),
            {
                status: 200,
                headers: {
                    "Content-Type": "application/json",
                    [WORKSPACE_RESPONSE_HEADER]: "workspace-alice",
                },
            },
        )));

        await expect(api.exportProject("project-1", { format: "mp4" })).resolves.toEqual({
            url: "export/project.mp4",
        });
    });

    it("polls a server-mode refine job without treating JSON as SSE", async () => {
        const requestedUrls: string[] = [];
        apiClient.defaults.adapter = async (config) => {
            requestedUrls.push(config.url || "");
            return response(config, {
                task_id: "refine-job",
                status: "completed",
                result: { script_id: "project-1" },
            });
        };
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
            JSON.stringify({ task_id: "refine-job", status: "queued" }),
            {
                status: 200,
                headers: {
                    "Content-Type": "application/json",
                    [WORKSPACE_RESPONSE_HEADER]: "workspace-alice",
                },
            },
        )));
        const onEvent = vi.fn();

        await api.refineBatchFrames("project-1", onEvent);

        expect(requestedUrls.some((url) => url.includes("/jobs/refine-job"))).toBe(true);
        expect(onEvent).not.toHaveBeenCalled();
    });

    it("keeps desktop refine batches on the SSE parser", async () => {
        vi.stubEnv("NEXT_PUBLIC_SERVER_MODE", "false");
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
            [
                "event: frame_refine_start",
                'data: {"frame_id":"frame-1","total":1}',
                "",
                "event: batch_complete",
                'data: {"total":1,"success":1,"failed":0}',
                "",
            ].join("\n"),
            {
                status: 200,
                headers: { "Content-Type": "text/event-stream" },
            },
        )));
        const onEvent = vi.fn();

        await api.refineBatchFrames("project-1", onEvent);

        expect(onEvent).toHaveBeenCalledWith({
            type: "frame_refine_start",
            frame_id: "frame-1",
            total: 1,
        });
        expect(onEvent).toHaveBeenCalledWith({
            type: "batch_complete",
            total: 1,
            success: 1,
            failed: 0,
        });
    });

    it("keeps desktop-style direct responses unchanged", async () => {
        const project = { id: "project-1", original_text: "desktop" };
        apiClient.defaults.adapter = async (config) => response(config, project);

        await expect(api.mergeVideos("project-1")).resolves.toBe(project);
    });

    it("polls local hybrid activity for a storyboard render marker", async () => {
        window.__ENMOTION_RUNTIME_CONFIG__ = { hybridMode: true };
        const requestedUrls: string[] = [];
        apiClient.defaults.adapter = async (config) => {
            requestedUrls.push(config.url || "");
            if (config.url?.includes("/activity/task/hybrid-storyboard-1")) {
                return response(config, {
                    task_id: "hybrid-storyboard-1",
                    status: "completed",
                });
            }
            if (config.method === "get" && config.url?.includes("/projects/project-1")) {
                return response(config, {
                    id: "project-1",
                    original_text: "hybrid source",
                });
            }
            return response(config, {
                task_id: "hybrid-storyboard-1",
                status: "queued",
            });
        };

        await expect(
            api.renderFrame("project-1", "frame-3", {}, "A brass fox"),
        ).resolves.toMatchObject({
            id: "project-1",
            originalText: "hybrid source",
        });
        expect(requestedUrls.some((url) => url.includes("/activity/task/hybrid-storyboard-1"))).toBe(true);
        expect(requestedUrls.some((url) => url.includes("/jobs/"))).toBe(false);
    });

    it("surfaces a durable job failure to the existing caller promise", async () => {
        apiClient.defaults.adapter = async (config) => response(config, {
            task_id: "failed-job",
            status: "failed",
            error: "FFmpeg failed",
            error_code: "assembly_failed",
            error_diagnostic: "ffmpeg exited with status 1",
        });

        const failure = await waitForDurableJob("failed-job").catch((error) => error);
        expect(failure).toBeInstanceOf(GenerationTaskError);
        expect(failure).toMatchObject({
            message: "FFmpeg failed",
            taskId: "failed-job",
            status: "failed",
            code: "assembly_failed",
            diagnostic: "ffmpeg exited with status 1",
        });
    });

    it("loads multiple transient task states with one bounded request", async () => {
        let request: InternalAxiosRequestConfig | undefined;
        apiClient.defaults.adapter = async (config) => {
            request = config;
            return response(config, {
                tasks: [
                    { task_id: "video-1", status: "processing" },
                    { task_id: "image-1", status: "completed", image_url: "frames/1.webp" },
                ],
                missing: [],
            });
        };

        const statuses = await api.getTaskStatuses(["video-1", "image-1", "video-1"]);

        expect(request?.url).toContain("/tasks/status");
        expect(String(request?.params)).toBe("task_id=video-1&task_id=image-1");
        expect(statuses.get("video-1")?.status).toBe("processing");
        expect(statuses.get("image-1")?.image_url).toBe("frames/1.webp");
    });

    it("stops durable polling when the owning screen aborts its request", async () => {
        const controller = new AbortController();
        let requests = 0;
        apiClient.defaults.adapter = async (config) => {
            requests += 1;
            return response(config, {
                task_id: "running-job",
                status: "running",
            });
        };

        const pending = waitForDurableJob("running-job", {
            pollIntervalMs: 60_000,
            signal: controller.signal,
        });
        await vi.waitFor(() => expect(requests).toBe(1));
        controller.abort();

        await expect(pending).rejects.toMatchObject({ name: "AbortError" });
        expect(requests).toBe(1);
    });
});
