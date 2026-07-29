// @vitest-environment jsdom

import type { InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { WORKSPACE_RESPONSE_HEADER, apiClient } from "@/lib/httpClient";
import {
  setWorkspaceStorageScope,
  writeWorkspaceItem,
} from "@/lib/workspaceStorage";

const originalAdapter = apiClient.defaults.adapter;
const workspaceId = "workspace-creation-defaults";

function response(config: InternalAxiosRequestConfig, data: unknown) {
  return {
    data,
    status: 200,
    statusText: "OK",
    headers: { [WORKSPACE_RESPONSE_HEADER]: workspaceId },
    config,
  };
}

function requestBody(config: InternalAxiosRequestConfig): Record<string, unknown> {
  return typeof config.data === "string"
    ? JSON.parse(config.data)
    : config.data as Record<string, unknown>;
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_SERVER_MODE", "true");
  setWorkspaceStorageScope(workspaceId);
  writeWorkspaceItem("enmotion_default_model_settings", JSON.stringify({
    chat_model: "deepseek-v4-pro",
    image_model: "gpt-image-2",
    video_model: "doubao-seedance-2-0-mini-260615",
    storyboard_aspect_ratio: "9:16",
  }));
  writeWorkspaceItem("enmotion_default_prompt_config", JSON.stringify({
    entity_extraction: "Use the workspace extraction contract",
    storyboard_polish: "Keep every shot cinematic",
    video_polish: "",
  }));
});

afterEach(() => {
  apiClient.defaults.adapter = originalAdapter;
  window.localStorage.clear();
  setWorkspaceStorageScope(null);
  vi.unstubAllEnvs();
});

describe("creation defaults API", () => {
  it("sends defaults before standalone creation but leaves Series episodes inheriting", async () => {
    const requests: InternalAxiosRequestConfig[] = [];
    apiClient.defaults.adapter = async (config) => {
      requests.push(config);
      return response(config, {
        id: requestBody(config).series_id ? "episode-1" : "standalone-1",
        original_text: "source",
      });
    };

    await api.createProject("Standalone", "source");
    await api.createProject(
      "Episode",
      "source",
      false,
      "i2v_legacy",
      "series-1",
    );

    const standalone = requestBody(requests[0]);
    expect(standalone.model_settings).toEqual({
      chat_model: "deepseek-v4-pro",
      image_model: "gpt-image-2",
      video_model: "doubao-seedance-2-0-mini-260615",
      storyboard_aspect_ratio: "9:16",
    });
    expect(standalone.prompt_config).toEqual({
      entity_extraction: "Use the workspace extraction contract",
      storyboard_polish: "Keep every shot cinematic",
    });

    const episode = requestBody(requests[1]);
    expect(episode.series_id).toBe("series-1");
    expect(episode).not.toHaveProperty("model_settings");
    expect(episode).not.toHaveProperty("prompt_config");
  });

  it("seeds normal and imported Series with workspace defaults", async () => {
    const requests: InternalAxiosRequestConfig[] = [];
    apiClient.defaults.adapter = async (config) => {
      requests.push(config);
      if (config.url?.includes("/series/import/confirm")) {
        return response(config, {
          series: { id: "imported-series" },
          episodes: [],
        });
      }
      return response(config, { id: "series-1" });
    };

    await api.createSeries("Series");
    await api.importFileConfirm({
      title: "Imported Series",
      text: "Episode source",
      episodes: [],
    });

    for (const config of requests) {
      const body = requestBody(config);
      expect(body.model_settings).toMatchObject({
        chat_model: "deepseek-v4-pro",
        image_model: "gpt-image-2",
        video_model: "doubao-seedance-2-0-mini-260615",
      });
      expect(body.prompt_config).toEqual({
        entity_extraction: "Use the workspace extraction contract",
        storyboard_polish: "Keep every shot cinematic",
      });
    }
  });
});
