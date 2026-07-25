export interface T2IWorkbenchPatch {
    workbench_tab_mode?: "t2i_i2v";
    t2i_image_urls?: string[];
    t2i_selected_index?: number;
    workbench_generate_count?: number;
}

interface T2IFrameState {
    t2i_image_urls?: string[];
    t2i_selected_index?: number;
}

/**
 * Rebase workbench edits made while an image DELETE was in flight onto the
 * server-confirmed post-delete frame. A stale debounced patch must never be
 * able to add the deleted URL back, and an index selection is translated by
 * URL so deleting an earlier candidate does not select the wrong image.
 */
export function reconcileT2IWorkbenchAfterDelete(
    frame: T2IFrameState,
    queued: T2IWorkbenchPatch,
    removedUrl: string | undefined,
    preDeleteUrls: string[],
): T2IWorkbenchPatch {
    const serverUrls = Array.isArray(frame.t2i_image_urls)
        ? frame.t2i_image_urls
        : [];
    const queuedUrlsBeforeDelete = Array.isArray(queued.t2i_image_urls)
        ? queued.t2i_image_urls
        : null;
    const queuedUrls = queuedUrlsBeforeDelete
        ? queuedUrlsBeforeDelete.filter((url) => url !== removedUrl)
        : null;
    const desiredUrls = queuedUrls
        ? Array.from(new Set([...serverUrls, ...queuedUrls]))
        : serverUrls;
    const reconciled: T2IWorkbenchPatch = { ...queued };

    if (queuedUrls) reconciled.t2i_image_urls = desiredUrls;
    if (queued.t2i_selected_index !== undefined || queuedUrlsBeforeDelete) {
        // The queued index belongs to the original list. Resolve it before
        // filtering the deleted URL, then find that same URL in the rebased
        // list. This keeps selection stable when an earlier image is deleted.
        const selectionSource = queuedUrlsBeforeDelete ?? preDeleteUrls;
        const requestedUrl = selectionSource[queued.t2i_selected_index ?? 0];
        const requestedIndex = requestedUrl && requestedUrl !== removedUrl
            ? desiredUrls.indexOf(requestedUrl)
            : -1;
        reconciled.t2i_selected_index = requestedIndex >= 0
            ? requestedIndex
            : Math.max(
                0,
                Math.min(
                    frame.t2i_selected_index ?? 0,
                    Math.max(0, desiredUrls.length - 1),
                ),
            );
    }

    return reconciled;
}
