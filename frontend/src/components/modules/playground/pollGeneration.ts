import {
  playgroundApi,
  type PlaygroundGenerationResponse,
} from "@/lib/api";

const DEFAULT_POLL_INTERVAL_MS = 2_000;

function abortReason(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("Operation was aborted", "AbortError");
}

function wait(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(abortReason(signal));
  return new Promise((resolve, reject) => {
    const handleAbort = () => {
      window.clearTimeout(timer);
      reject(abortReason(signal));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", handleAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", handleAbort, { once: true });
  });
}

/**
 * Poll one Playground generation sequentially. The next status request starts
 * only after the previous request has settled, so slow providers cannot create
 * overlapping client work. The full record is fetched exactly once, after the
 * server reports a terminal state.
 */
export async function waitForPlaygroundGeneration(
  generationId: string,
  {
    signal,
    intervalMs = DEFAULT_POLL_INTERVAL_MS,
  }: {
    signal: AbortSignal;
    intervalMs?: number;
  },
): Promise<PlaygroundGenerationResponse> {
  while (true) {
    await wait(intervalMs, signal);
    const status = await playgroundApi.getGenerationStatus(
      generationId,
      { signal },
    );
    if (status.status === "completed" || status.status === "failed") {
      return playgroundApi.getGeneration(generationId, { signal });
    }
  }
}
