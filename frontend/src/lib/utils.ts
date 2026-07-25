import { API_URL } from "./apiUrl";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}

/**
 * Return the stable media reference persisted by the backend. Temporary
 * signatures and the host that happened to serve a file are deliberately
 * excluded so selected variants continue to match after sign-in, refresh,
 * deployment, or URL re-signing.
 */
export function durableMediaReference(path: string | null | undefined): string {
    const raw = path?.trim() ?? "";
    if (!raw || raw.startsWith("data:") || raw.startsWith("blob:")) return raw;

    try {
        const parsed = new URL(raw, "http://enmotion.invalid");
        let pathname = parsed.pathname;
        try {
            pathname = decodeURIComponent(pathname);
        } catch {
            // A legacy filename can contain a literal, malformed `%`. Keep
            // the pathname usable instead of turning the whole absolute URL
            // into an invalid `/files/https://...` media request.
        }
        const normalizedPath = pathname.replaceAll("\\", "/");
        const filesMarker = "/files/";
        const outputMarkers = Array.from(normalizedPath.matchAll(/\/(?:outputs?)\//gi));
        // Absolute server paths may contain both the workspace output root and
        // the persisted media path's own `output/` prefix. The final marker is
        // the one relative to the authenticated `/files` mount.
        const outputMarker = outputMarkers[outputMarkers.length - 1];
        const durablePath = normalizedPath.includes(filesMarker)
            ? normalizedPath.split(filesMarker, 2)[1]
            : outputMarker
                ? normalizedPath.slice((outputMarker.index ?? 0) + outputMarker[0].length)
                : normalizedPath;
        return durablePath
            .replace(/^\/+/, "")
            .replace(/^outputs?\/+/, "");
    } catch {
        return raw
            .split("?", 1)[0]
            .replaceAll("\\", "/")
            .replace(/^\/+/, "")
            .replace(/^files\/+/, "")
            .replace(/^outputs?\/+/, "");
    }
}

export function getAssetUrl(path: string | null | undefined): string {
    if (!path) return "";
    const value = path.trim();
    if (!value) return "";
    if (/^(?:blob:|data:)/i.test(value)) return value;

    // `/files/` is the authenticated application-media contract. Rebase
    // historical absolute URLs (including old hosts and `/api-proxy/files/`)
    // onto the current API origin instead of retaining an expired host or
    // signature. External CDN/OSS URLs which do not use that contract remain
    // untouched.
    if (/^(?:https?:|\/\/)/i.test(value)) {
        try {
            const parsed = new URL(value, API_URL);
            if (!parsed.pathname.includes("/files/")) return value;
        } catch {
            return value;
        }
    }

    // Persisted data exists in several historical forms. Canonicalize all of
    // them to the backend's /files/<path-relative-to-output> contract instead
    // of producing broken /files/output/... or /files/files/... URLs.
    const cleanPath = durableMediaReference(value);
    if (!cleanPath) return "";
    return `${API_URL.replace(/\/+$/, "")}/files/${cleanPath}`;
}

export function getAssetUrlWithTimestamp(path: string | null | undefined, timestamp?: number): string {
    const baseUrl = getAssetUrl(path);
    if (!baseUrl) return "";

    // If URL already has query params, append with & otherwise with ?
    const separator = baseUrl.includes('?') ? '&' : '?';
    return baseUrl + separator + `t=${timestamp || 0}`;
}

export function extractErrorDetail(error: any, fallback = "未知错误"): string {
    return error?.response?.data?.detail
        || error?.response?.data?.message
        || error?.message
        || fallback;
}
