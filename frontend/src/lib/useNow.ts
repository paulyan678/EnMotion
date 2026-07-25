"use client";

import { useSyncExternalStore } from "react";

const listeners = new Set<() => void>();
let currentNow = Date.now();
let timer: ReturnType<typeof setInterval> | null = null;

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === null) {
    currentNow = Date.now();
    timer = setInterval(() => {
      currentNow = Date.now();
      for (const notify of listeners) notify();
    }, 1_000);
  }

  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

function getSnapshot(): number {
  return currentNow;
}

function getServerSnapshot(): number {
  return 0;
}

/** A shared clock for elapsed-time UI that is safe during render and SSR. */
export function useNow(): number {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
