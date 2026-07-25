import { getRuntimeApiUrl } from "@/lib/serverMode";

const BACKEND_PORT = process.env.NEXT_PUBLIC_BACKEND_PORT || "17177";

export function getApiUrl(): string {
  const runtimeOverride = getRuntimeApiUrl();
  if (runtimeOverride) return runtimeOverride;

  const override = process.env.NEXT_PUBLIC_API_URL;
  if (override?.trim()) return override.trim().replace(/\/+$/, "");

  if (typeof window !== "undefined") {
    const { protocol, hostname, port } = window.location;
    if (process.env.NODE_ENV === "development") {
      return `${protocol}//${hostname}:${BACKEND_PORT}`;
    }
    return `${protocol}//${hostname}${port ? `:${port}` : ""}`;
  }

  return `http://localhost:${BACKEND_PORT}`;
}

export const API_URL = getApiUrl();
