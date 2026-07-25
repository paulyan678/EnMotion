import type { InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
    api,
    SERIES_MODEL_SETTINGS_TIMEOUT_MS,
    type ModelSettingsPayload,
} from "@/lib/api";
import { WORKSPACE_RESPONSE_HEADER, apiClient } from "@/lib/httpClient";
import { setWorkspaceStorageScope } from "@/lib/workspaceStorage";

const originalAdapter = apiClient.defaults.adapter;

beforeEach(() => {
    setWorkspaceStorageScope("workspace-settings-test");
});

afterEach(() => {
    apiClient.defaults.adapter = originalAdapter;
    setWorkspaceStorageScope(null);
});

describe("series model settings API", () => {
    it("uses a bounded request and returns the compact settings response", async () => {
        let requestConfig: InternalAxiosRequestConfig | undefined;
        const payload: ModelSettingsPayload = {
            chat_model: "qwen3.7-max",
            image_model: "gpt-image-2",
        };
        apiClient.defaults.adapter = async (config) => {
            requestConfig = config;
            return {
                data: payload,
                status: 200,
                statusText: "OK",
                headers: { [WORKSPACE_RESPONSE_HEADER]: "workspace-settings-test" },
                config,
            };
        };

        await expect(api.updateSeriesModelSettings("series-1", payload)).resolves.toEqual(payload);
        expect(requestConfig?.method).toBe("put");
        expect(requestConfig?.url).toContain("/series/series-1/model_settings");
        expect(requestConfig?.timeout).toBe(SERIES_MODEL_SETTINGS_TIMEOUT_MS);
    });
});
