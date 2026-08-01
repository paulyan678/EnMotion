import { api } from "@/lib/api";
import type { Project } from "@/store/projectStore";

type ProjectSubscriber = {
  onProject: (project: Project) => void;
  onError?: (error: unknown) => void;
};

type ProjectObservation = {
  projectId: string;
  subscribers: Set<ProjectSubscriber>;
  timer: number | null;
  inFlight: boolean;
  startedAt: number;
  consecutiveFailures: number;
};

const observations = new Map<string, ProjectObservation>();
const ACTIVE_INTERVAL_MS = 3_000;
const SETTLED_ACTIVE_INTERVAL_MS = 5_000;
const MAX_FAILURE_INTERVAL_MS = 15_000;
let visibilityListenerInstalled = false;

function isVisible(): boolean {
  return typeof document === "undefined" || document.visibilityState === "visible";
}

function clearTimer(observation: ProjectObservation): void {
  if (observation.timer === null || typeof window === "undefined") return;
  window.clearTimeout(observation.timer);
  observation.timer = null;
}

function nextDelay(observation: ProjectObservation): number {
  if (observation.consecutiveFailures > 0) {
    return Math.min(
      ACTIVE_INTERVAL_MS * 2 ** observation.consecutiveFailures,
      MAX_FAILURE_INTERVAL_MS,
    );
  }
  return Date.now() - observation.startedAt < 60_000
    ? ACTIVE_INTERVAL_MS
    : SETTLED_ACTIVE_INTERVAL_MS;
}

function schedule(observation: ProjectObservation, delay = nextDelay(observation)): void {
  clearTimer(observation);
  if (
    typeof window === "undefined"
    || observation.subscribers.size === 0
    || !isVisible()
  ) {
    return;
  }
  observation.timer = window.setTimeout(() => {
    observation.timer = null;
    void refresh(observation);
  }, delay);
}

async function refresh(observation: ProjectObservation): Promise<void> {
  if (observation.inFlight || observation.subscribers.size === 0 || !isVisible()) return;
  observation.inFlight = true;
  try {
    const project = await api.getProject(observation.projectId);
    observation.consecutiveFailures = 0;
    for (const subscriber of [...observation.subscribers]) subscriber.onProject(project);
  } catch (error) {
    observation.consecutiveFailures += 1;
    for (const subscriber of [...observation.subscribers]) subscriber.onError?.(error);
  } finally {
    observation.inFlight = false;
    schedule(observation);
  }
}

function handleVisibilityChange(): void {
  if (!isVisible()) {
    for (const observation of observations.values()) clearTimer(observation);
    return;
  }
  for (const observation of observations.values()) schedule(observation, 0);
}

function installVisibilityListener(): void {
  if (visibilityListenerInstalled || typeof document === "undefined") return;
  document.addEventListener("visibilitychange", handleVisibilityChange);
  visibilityListenerInstalled = true;
}

/** Share one visibility-aware, non-overlapping refresh loop per active project. */
export function observeProjectTasks(
  projectId: string,
  subscriber: ProjectSubscriber,
): () => void {
  installVisibilityListener();
  let observation = observations.get(projectId);
  if (!observation) {
    observation = {
      projectId,
      subscribers: new Set(),
      timer: null,
      inFlight: false,
      startedAt: Date.now(),
      consecutiveFailures: 0,
    };
    observations.set(projectId, observation);
  }
  observation.subscribers.add(subscriber);
  if (!observation.inFlight && observation.timer === null) schedule(observation, 0);

  return () => {
    observation?.subscribers.delete(subscriber);
    if (observation?.subscribers.size === 0) {
      clearTimer(observation);
      observations.delete(projectId);
    }
  };
}

export function resetProjectTaskObserversForTests(): void {
  for (const observation of observations.values()) clearTimer(observation);
  observations.clear();
}
