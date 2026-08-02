"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useTranslations } from "next-intl";
import { motion, AnimatePresence } from "framer-motion";
import { Wand2, Loader2, User, MapPin, Box, ChevronRight, ChevronLeft, Save, Sparkles, Plus, Trash2, X, ScrollText } from "lucide-react";
import { api, crudApi } from "@/lib/api";
import { useProjectStore } from "@/store/projectStore";
import { toast } from "@/store/toastStore";
import PreviousEpisodeSummary from "@/components/modules/PreviousEpisodeSummary";
import { notifyAssetLibraryChanged } from "@/lib/assetLibrarySync";
import ResizableSidePanel, {
    EPISODE_EDITOR_PANEL_STORAGE_KEYS,
} from "@/components/layout/ResizableSidePanel";
import TextGenerationRequestDialog from "@/components/generation/TextGenerationRequestDialog";

interface ScriptNode {
    type: "character" | "scene" | "prop";
    id?: string;
    name: string;
    desc: string;
    // Extended attributes
    age?: string;
    gender?: string;
    clothing?: string;
    visual_weight?: number;
}

interface ScriptDraft {
    projectId: string;
    projectTitle?: string;
    text: string;
}

export default function ScriptProcessor() {
    const ts = useTranslations("script");
    const tc = useTranslations("common");
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);
    const scriptDrafts = useProjectStore((state) => state.scriptDrafts);
    const markScriptDraft = useProjectStore((state) => state.markScriptDraft);
    const confirmScriptDraft = useProjectStore((state) => state.confirmScriptDraft);
    const isAnalyzing = useProjectStore((state) => state.isAnalyzing);

    // Initialize from project data. Fallback to snake_case original_text
    // in case the API wrapper didn't map it (e.g. raw axios response, or a
    // store update that spread the backend payload without re-mapping).
    const storedDirtyDraft = currentProject ? scriptDrafts[currentProject.id] : undefined;
    const serverProjectText =
        (currentProject?.originalText ?? (currentProject as any)?.original_text) || "";
    const projectText = storedDirtyDraft?.text ?? serverProjectText;
    const [script, setScript] = useState(projectText);
    const persistedTextByProject = useRef(new Map<string, string>(
        currentProject && !storedDirtyDraft ? [[currentProject.id, projectText]] : [],
    ));
    const draftTextByProject = useRef(new Map<string, string>(
        currentProject ? [[currentProject.id, projectText]] : [],
    ));
    const activeDraft = useRef<ScriptDraft | null>(
        currentProject
            ? { projectId: currentProject.id, projectTitle: currentProject.title, text: projectText }
            : null,
    );
    const pendingSaves = useRef(new Map<string, ScriptDraft>());
    const saveLoop = useRef<Promise<void> | null>(null);
    const observedDirtyTextByProject = useRef(new Map<string, string>());
    const [isSavingScript, setIsSavingScript] = useState(false);
    const [scriptScrolled, setScriptScrolled] = useState(false);
    const [textComposerOpen, setTextComposerOpen] = useState(false);
    const [nodes, setNodes] = useState<ScriptNode[]>([]);

    // UI State
    const [selectedNode, setSelectedNode] = useState<ScriptNode | null>(null);
    const [showPanel, setShowPanel] = useState(true);
    const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);

    const queueScriptSave = useCallback((draft: ScriptDraft) => {
        markScriptDraft(draft.projectId, {
            text: draft.text,
            projectTitle: draft.projectTitle,
        });
        pendingSaves.current.set(draft.projectId, draft);
        if (saveLoop.current) return saveLoop.current;

        saveLoop.current = (async () => {
            setIsSavingScript(true);
            const deferredFailures = new Set<string>();
            while (pendingSaves.current.size > 0) {
                const entry = Array.from(pendingSaves.current.entries()).find(
                    ([projectId]) => !deferredFailures.has(projectId),
                );
                if (!entry) break;
                const [projectId, next] = entry;
                pendingSaves.current.delete(projectId);
                if (persistedTextByProject.current.get(next.projectId) === next.text) {
                    confirmScriptDraft(next.projectId, next.text);
                    continue;
                }
                try {
                    const updated = await api.updateScriptText(next.projectId, next.text);
                    const persistedText =
                        (updated as any).original_text ?? updated.originalText ?? next.text;
                    persistedTextByProject.current.set(next.projectId, persistedText);
                    confirmScriptDraft(next.projectId, next.text);
                    const latestDraft = draftTextByProject.current.get(next.projectId);
                    const visibleText = latestDraft === undefined ? persistedText : latestDraft;
                    updateProject(next.projectId, {
                        originalText: visibleText,
                        original_text: visibleText,
                    } as any);
                } catch (error: any) {
                    // Keep the failed project dirty without overwriting a newer
                    // queued draft for that same project.
                    const newerDraft = pendingSaves.current.get(next.projectId);
                    if (!newerDraft) {
                        pendingSaves.current.set(next.projectId, next);
                        deferredFailures.add(next.projectId);
                    }
                    const detail =
                        error?.response?.data?.detail || error?.message || ts("saveFailed");
                    toast.error(ts("saveFailed"), {
                        body: String(detail).slice(0, 240),
                        projectId: next.projectId,
                        projectTitle: next.projectTitle,
                    });
                    // A newer draft queued behind this failed request gets one
                    // immediate attempt. If no newer draft exists, defer this
                    // dirty project until the next user-triggered blur.
                    continue;
                }
            }
        })().finally(() => {
            setIsSavingScript(false);
            saveLoop.current = null;
        });

        return saveLoop.current;
    }, [confirmScriptDraft, markScriptDraft, ts, updateProject]);

    // Project changes save the outgoing owned draft before rehydrating the
    // incoming one. Blur can therefore never pair A's text with B's id, even
    // when the switch happens before the textarea loses focus.
    useEffect(() => {
        const incomingProjectId = currentProject?.id ?? null;
        const outgoing = activeDraft.current;
        if (outgoing && outgoing.projectId !== incomingProjectId) {
            if (persistedTextByProject.current.get(outgoing.projectId) !== outgoing.text) {
                void queueScriptSave(outgoing);
            }
        }
        if (!currentProject) {
            activeDraft.current = null;
            setScript("");
            return;
        }
        const serverText =
            (currentProject as any)?.original_text ?? currentProject.originalText ?? "";
        const dirtyDraft = scriptDrafts[currentProject.id];
        if (dirtyDraft) {
            // The persisted Zustand project snapshot is optimistic. A matching
            // dirty marker means it must not be treated as server-confirmed.
            persistedTextByProject.current.delete(currentProject.id);
        } else if (!persistedTextByProject.current.has(currentProject.id)) {
            persistedTextByProject.current.set(currentProject.id, serverText);
        }
        const incomingText =
            dirtyDraft?.text
            ?? draftTextByProject.current.get(currentProject.id)
            ?? serverText;
        draftTextByProject.current.set(currentProject.id, incomingText);
        const incomingDraft = {
            projectId: currentProject.id,
            projectTitle: dirtyDraft?.projectTitle ?? currentProject.title,
            text: incomingText,
        };
        activeDraft.current = incomingDraft;
        setScript(incomingText);
        if (
            dirtyDraft
            && observedDirtyTextByProject.current.get(currentProject.id) !== dirtyDraft.text
        ) {
            // Retry a dirty draft restored by remount/rehydration exactly once
            // per component lifetime. A failure remains marked for the next
            // remount instead of spinning in the background.
            observedDirtyTextByProject.current.set(currentProject.id, dirtyDraft.text);
            void queueScriptSave(incomingDraft);
        }
    }, [
        currentProject?.id,
        queueScriptSave,
        scriptDrafts,
    ]);

    useEffect(() => {
        if (!currentProject) {
            setNodes([]);
            return;
        }
        const newNodes: ScriptNode[] = [
            ...(currentProject.characters || []).map((c: any) => ({
                type: "character" as const,
                id: c.id,
                name: c.name,
                desc: c.description,
                age: c.age,
                gender: c.gender,
                clothing: c.clothing,
                visual_weight: c.visual_weight
            })),
            ...(currentProject.scenes || []).map((s: any) => ({
                type: "scene" as const,
                id: s.id,
                name: s.name,
                desc: s.description,
                visual_weight: s.visual_weight
            })),
            ...(currentProject.props || []).map((p: any) => ({
                type: "prop" as const,
                id: p.id,
                name: p.name,
                desc: p.description
            }))
        ];
        setNodes(newNodes);
    }, [currentProject?.id, currentProject?.characters, currentProject?.scenes, currentProject?.props]);

    const handleAnalyze = () => {
        if (!script.trim()) {
            toast.warning(ts("scriptEmpty"), {
                projectId: currentProject?.id,
                projectTitle: currentProject?.title,
            });
            return;
        }
        if (!currentProject?.id) return;
        setTextComposerOpen(true);
    };

    const handleExtractionCompleted = (preview: {
        characters: unknown[];
        scenes: unknown[];
        props: unknown[];
        preview_revision?: string;
    }) => {
        useProjectStore.setState({
            pendingExtraction: preview,
            pendingExtractionScript: script,
            isAnalyzing: false,
        });
        toast.success(ts("analysisDone"), {
            projectId: currentProject?.id,
            projectTitle: currentProject?.title,
            body: ts("analysisDoneBody", {
                c: preview.characters.length,
                s: preview.scenes.length,
                p: preview.props.length,
            }),
        });
    };

    const handleDeleteNode = async (node: ScriptNode, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!currentProject) return;
        if (!confirm(ts("confirmDelete", { name: node.name }))) return;
        if (!node.id) return;

        try {
            let updatedProject;
            if (node.type === "character") {
                updatedProject = await crudApi.deleteCharacter(currentProject.id, node.id);
            } else if (node.type === "scene") {
                updatedProject = await crudApi.deleteScene(currentProject.id, node.id);
            } else {
                updatedProject = await crudApi.deleteProp(currentProject.id, node.id);
            }

            updateProject(currentProject.id, updatedProject);
            notifyAssetLibraryChanged({
                projectId: currentProject.id,
                seriesId: currentProject.series_id,
            });
        } catch (error) {
            console.error("Failed to delete node:", error);
            toast.error(ts("deleteFailed"), {
                projectId: currentProject?.id,
                projectTitle: currentProject?.title,
            });
        }
    };

    const handleCreateNode = async (data: any) => {
        if (!currentProject) return;
        try {
            if (data.type === "character") {
                await crudApi.createCharacter(currentProject.id, data);
            } else if (data.type === "scene") {
                await crudApi.createScene(currentProject.id, data);
            } else if (data.type === "prop") {
                await crudApi.createProp(currentProject.id, data);
            }

            const updatedProject = await api.getProject(currentProject.id);
            updateProject(currentProject.id, updatedProject);
            notifyAssetLibraryChanged({
                projectId: currentProject.id,
                seriesId: currentProject.series_id,
            });
            setIsCreateDialogOpen(false);
        } catch (error) {
            console.error("Failed to create node:", error);
            toast.error(ts("createFailed"), {
                projectId: currentProject?.id,
                projectTitle: currentProject?.title,
            });
        }
    };

    const handleNodeUpdate = (updatedNode: ScriptNode) => {
        // Update local state
        setNodes(prev => prev.map(n => n.name === updatedNode.name ? updatedNode : n));
        setSelectedNode(updatedNode);
    };

    const tStep = useTranslations("stepHeader");

    return (
        // R2V v2 Phase 3: Script step = main editor (left) + Previously on... (right).
        // Entity extraction still runs via the trailing "提取实体" button —
        // parsed entities flow to series pools and surface in Cast step.
        <div className="relative flex h-full w-full overflow-hidden">
            {/* Left: main script editor */}
            <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
                <h1 className="sr-only">{tStep("scriptTitle")}</h1>
                <div
                    data-scroll-away-actions="true"
                    aria-hidden={scriptScrolled}
                    inert={scriptScrolled ? true : undefined}
                    className={`shrink-0 overflow-hidden px-6 transition-[max-height,opacity,transform,padding] duration-200 ease-out ${
                        scriptScrolled
                            ? "max-h-0 -translate-y-2 py-0 opacity-0 pointer-events-none"
                            : "max-h-20 translate-y-0 pb-2 pt-4 opacity-100"
                    }`}
                >
                    <div className="flex justify-end">
                        <button
                            type="button"
                            onClick={handleAnalyze}
                            disabled={!script || isAnalyzing}
                            className="inline-flex h-8 items-center gap-1.5 rounded-full bg-primary px-4 py-1.5 font-sans text-[0.8125rem] font-semibold text-on-accent shadow-[var(--btn-pri-glow),inset_0_1.5px_0_rgba(255,255,255,0.14)] transition-all duration-fast ease-out-quart hover:bg-primary-hover disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                        >
                            {isAnalyzing ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                            <span>{isAnalyzing ? ts("analyzingScript") : ts("extractEntities")}</span>
                        </button>
                    </div>
                </div>
                <div className="flex-1 relative p-6 bg-surface overflow-hidden">
                    <textarea
                        value={script}
                        onChange={(e) => {
                            const newText = e.target.value;
                            setScript(newText);
                            if (currentProject) {
                                const draft = {
                                    projectId: currentProject.id,
                                    projectTitle: currentProject.title,
                                    text: newText,
                                };
                                draftTextByProject.current.set(currentProject.id, newText);
                                activeDraft.current = draft;
                                observedDirtyTextByProject.current.set(
                                    currentProject.id,
                                    newText,
                                );
                                markScriptDraft(currentProject.id, {
                                    text: newText,
                                    projectTitle: currentProject.title,
                                });
                            }
                            // Update local Zustand state with BOTH the
                            // camelCase view-model key and the snake_case
                            // backend key, so any consumer that reads
                            // either name (or anything spread from a
                            // future API response) sees the same value.
                            if (currentProject) {
                                updateProject(currentProject.id, {
                                    originalText: newText,
                                    original_text: newText,
                                } as any);
                            }
                        }}
                        onBlur={() => {
                            // Persist through the lightweight text endpoint.
                            // The owned snapshot is captured while editing, so
                            // a project switch cannot rebind its text to the
                            // newly selected project.
                            if (activeDraft.current) {
                                void queueScriptSave(activeDraft.current);
                            }
                        }}
                        onScroll={(event) => {
                            setScriptScrolled(event.currentTarget.scrollTop > 4);
                        }}
                        aria-busy={isSavingScript}
                        placeholder={ts("scriptPlaceholder")}
                        className="w-full h-full bg-transparent text-text-secondary font-mono text-base leading-relaxed resize-none focus:outline-none"
                        spellCheck={false}
                    />
                </div>
            </div>

            {/* Right: Previously on... rail (R2V v2 Phase 3).
                Only renders for series-affiliated projects with an
                episode index > 0; the component handles empty/first
                episode state internally with a placeholder. */}
            <ResizableSidePanel
                side="right"
                storageKey={EPISODE_EDITOR_PANEL_STORAGE_KEYS.right}
                defaultWidth={340}
                minWidth={280}
                maxWidth={560}
                minRemainingWidth={360}
            >
                <PreviousEpisodeSummary scriptId={currentProject?.id ?? null} />
            </ResizableSidePanel>

            {currentProject ? (
                <TextGenerationRequestDialog
                    open={textComposerOpen}
                    scriptId={currentProject.id}
                    operation="entity_extraction"
                    initialSourceText={script}
                    onClose={() => setTextComposerOpen(false)}
                    onCompleted={handleExtractionCompleted}
                />
            ) : null}

        </div>
    );
}

function CreateEntityDialog({ onClose, onCreate }: { onClose: () => void; onCreate: (data: any) => void }) {
    const ts = useTranslations("script");
    const tc = useTranslations("common");
    const [name, setName] = useState("");
    const [desc, setDesc] = useState("");
    const [type, setType] = useState<"character" | "scene" | "prop">("character");

    const handleSubmit = () => {
        if (!name.trim()) {
            toast.warning(ts("nameRequired"));
            return;
        }
        onCreate({ name, description: desc, type });
    };

    return (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-overlay backdrop-blur-sm" onClick={onClose}>
            <div className="mx-4 w-full max-w-[400px] space-y-4 rounded-xl border border-glass-border bg-elevated p-6" onClick={e => e.stopPropagation()}>
                <h3 className="font-bold text-foreground">{ts("addEntity")}</h3>

                <div className="flex gap-2 p-1 bg-surface rounded-lg">
                    {(["character", "scene", "prop"] as const).map(t => (
                        <button
                            key={t}
                            onClick={() => setType(t)}
                            className={`flex-1 py-1.5 text-xs font-bold rounded capitalize ${type === t ? "bg-primary text-foreground" : "text-text-muted hover:text-foreground"}`}
                        >
                            {t}
                        </button>
                    ))}
                </div>

                <div>
                    <label className="text-xs text-text-muted">{ts("nameLabel")}</label>
                    <input
                        className="glass-input w-full"
                        value={name}
                        onChange={e => setName(e.target.value)}
                        placeholder={ts("entityNamePlaceholder")}
                    />
                </div>

                <div>
                    <label className="text-xs text-text-muted">{ts("descriptionLabel")}</label>
                    <textarea
                        className="glass-input w-full h-24 resize-none"
                        value={desc}
                        onChange={e => setDesc(e.target.value)}
                        placeholder={ts("visualDescPlaceholder")}
                    />
                </div>

                <div className="flex justify-end gap-2 pt-2">
                    <button onClick={onClose} className="px-4 py-2 text-xs text-text-secondary hover:text-foreground">{tc("cancel")}</button>
                    <button onClick={handleSubmit} className="px-4 py-2 bg-primary text-foreground rounded text-xs font-bold">{tc("create")}</button>
                </div>
            </div>
        </div>
    );
}
