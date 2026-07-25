export const STORYBOARD_FRAMES_CHANGED_EVENT = "enmotion:storyboard-frames-changed";

export interface StoryboardFramesChangeDetail {
    projectId: string;
    seriesId?: string;
    frameId?: string;
}

/**
 * Invalidate mounted storyboard consumers after the canonical project record
 * changes. The event carries identifiers only; frame/media data is reloaded
 * through the authenticated API instead of being copied into another cache.
 */
export function notifyStoryboardFramesChanged(detail: StoryboardFramesChangeDetail): void {
    if (typeof window === "undefined") return;
    window.dispatchEvent(
        new CustomEvent<StoryboardFramesChangeDetail>(STORYBOARD_FRAMES_CHANGED_EVENT, {
            detail,
        }),
    );
}

export function subscribeToStoryboardFrameChanges(
    listener: (detail: StoryboardFramesChangeDetail) => void,
): () => void {
    if (typeof window === "undefined") return () => undefined;
    const handler = (event: Event) => {
        listener((event as CustomEvent<StoryboardFramesChangeDetail>).detail);
    };
    window.addEventListener(STORYBOARD_FRAMES_CHANGED_EVENT, handler);
    return () => window.removeEventListener(STORYBOARD_FRAMES_CHANGED_EVENT, handler);
}
