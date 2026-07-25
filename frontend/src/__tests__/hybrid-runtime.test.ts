// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { getApiUrl } from "@/lib/apiUrl";
import {
  getRuntimeLocalNonce,
  isHybridModeEnabled,
  isServerModeEnabled,
} from "@/lib/serverMode";

describe("hybrid runtime configuration", () => {
  afterEach(() => {
    window.__ENMOTION_RUNTIME_CONFIG__ = undefined;
    vi.unstubAllEnvs();
  });

  it("uses the per-launch sidecar URL and nonce before build-time defaults", () => {
    window.__ENMOTION_RUNTIME_CONFIG__ = {
      apiUrl: "http://127.0.0.1:43123/",
      localNonce: "nonce-123",
      hybridMode: true,
    };

    expect(getApiUrl()).toBe("http://127.0.0.1:43123");
    expect(getRuntimeLocalNonce()).toBe("nonce-123");
    expect(isHybridModeEnabled()).toBe(true);
    expect(isServerModeEnabled()).toBe(true);
  });

  it("lets per-launch hybrid mode enable login even in a desktop-oriented build", () => {
    vi.stubEnv("NEXT_PUBLIC_SERVER_MODE", "false");
    window.__ENMOTION_RUNTIME_CONFIG__ = { hybridMode: true };

    expect(isServerModeEnabled()).toBe(true);
  });
});
