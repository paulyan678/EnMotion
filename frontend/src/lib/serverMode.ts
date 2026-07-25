/**
 * Server mode is opt-in for the retained browser-development harness. The
 * managed desktop runtime enables the same authenticated storage boundary
 * through `hybridMode`.
 *
 * The runtime hook lets a static export be reused with a small
 * `window.__ENMOTION_RUNTIME_CONFIG__` file when deployment needs to toggle the
 * mode without rebuilding the frontend.
 */
declare global {
  interface Window {
    __ENMOTION_RUNTIME_CONFIG__?: {
      serverMode?: boolean;
      hybridMode?: boolean;
      apiUrl?: string;
      localNonce?: string;
      updater?: {
        enabled?: boolean;
        channel?: string;
      };
    };
  }
}

function parseBoolean(value: string | undefined): boolean | undefined {
  if (!value) return undefined;
  const normalized = value.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  return undefined;
}

export function isServerModeEnabled(): boolean {
  if (typeof window !== "undefined") {
    const runtime = window.__ENMOTION_RUNTIME_CONFIG__;
    if (runtime?.hybridMode === true) return true;
    if (typeof runtime?.serverMode === "boolean") return runtime.serverMode;
  }
  return parseBoolean(process.env.NEXT_PUBLIC_SERVER_MODE) ?? false;
}

export function isHybridModeEnabled(): boolean {
  if (typeof window !== "undefined") {
    const runtimeValue = window.__ENMOTION_RUNTIME_CONFIG__?.hybridMode;
    if (typeof runtimeValue === "boolean") return runtimeValue;
  }
  return parseBoolean(process.env.NEXT_PUBLIC_HYBRID_MODE) ?? false;
}

export function getRuntimeApiUrl(): string | undefined {
  if (typeof window === "undefined") return undefined;
  const value = window.__ENMOTION_RUNTIME_CONFIG__?.apiUrl?.trim();
  return value ? value.replace(/\/+$/, "") : undefined;
}

export function getRuntimeLocalNonce(): string | undefined {
  if (typeof window === "undefined") return undefined;
  const value = window.__ENMOTION_RUNTIME_CONFIG__?.localNonce?.trim();
  return value || undefined;
}
