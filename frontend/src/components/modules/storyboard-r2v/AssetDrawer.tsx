"use client";

import { motion, AnimatePresence } from "framer-motion";
import { X, User, MapPin, Package, Pencil } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import SharedAssetEditor from "@/components/assets/SharedAssetEditor";
import {
    assetRefFromResolvedAsset,
    patchResolvedProjectAsset,
    type AssetRef,
    type EditableAsset,
} from "@/components/assets/assetEditorTypes";
import PreviewImage from "@/components/shared/preview/PreviewImage";
import { primaryAssetImageUrl, type AssetImageKind } from "@/lib/assetImage";
import type { Character, Prop, Scene } from "@/store/projectStore";
import { useProjectStore } from "@/store/projectStore";

interface AssetDrawerProps {
    isOpen: boolean;
    onClose: () => void;
    characters: Character[];
    scenes: Scene[];
    props: Prop[];
    onSelectAsset: (type: string, name: string, ref: AssetRef) => void;
}

function getAssetThumbnail(item: EditableAsset, type: "character" | "scene" | "prop"): string | null {
    return primaryAssetImageUrl(item, type as AssetImageKind) || null;
}

export default function AssetDrawer({ isOpen, onClose, characters, scenes, props, onSelectAsset }: AssetDrawerProps) {
    const t = useTranslations("storyboardR2V");
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);
    const [editorTarget, setEditorTarget] = useState<AssetRef | null>(null);

    const hasAnyAssets = characters.length > 0 || scenes.length > 0 || props.length > 0;
    const referenceFor = (
        asset: EditableAsset,
        assetType: AssetRef["assetType"],
    ): AssetRef | null => {
        if (!currentProject) return null;
        return assetRefFromResolvedAsset(asset, assetType, {
            projectId: currentProject.id,
            seriesId: currentProject.series_id,
        });
    };
    const patchProject = (
        asset: EditableAsset,
        nextRef: AssetRef,
        previousRef?: AssetRef,
    ) => {
        if (!currentProject) return;
        const patched = patchResolvedProjectAsset(
            currentProject,
            asset,
            nextRef,
            previousRef,
        );
        updateProject(currentProject.id, {
            characters: patched.characters,
            scenes: patched.scenes,
            props: patched.props,
        });
    };

    return (
        <>
            <AnimatePresence>
                {isOpen && (
                    <>
                    {/* Backdrop */}
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 bg-black/30 z-40"
                        onClick={onClose}
                    />
                    {/* Drawer */}
                    <motion.div
                        initial={{ x: "100%" }}
                        animate={{ x: 0 }}
                        exit={{ x: "100%" }}
                        transition={{ type: "spring", damping: 25, stiffness: 300 }}
                        className="fixed inset-y-0 right-0 w-80 z-50 bg-surface border-l border-glass-border shadow-2xl flex flex-col"
                    >
                        {/* Header */}
                        <div className="flex items-center justify-between px-4 py-3 border-b border-glass-border bg-glass backdrop-blur-xl shrink-0 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
                            <h3 className="text-sm font-semibold text-foreground">{t("assetLibrary")}</h3>
                            <button
                                onClick={onClose}
                                className="p-1.5 rounded-lg hover:bg-hover-bg text-text-secondary hover:text-foreground transition-colors"
                            >
                                <X size={16} />
                            </button>
                        </div>

                        {/* Content */}
                        <div className="flex-1 overflow-y-auto p-4 space-y-5">
                            {!hasAnyAssets ? (
                                <div className="text-center py-8">
                                    <p className="text-sm text-text-secondary">{t("noAssetsAvailable")}</p>
                                    <p className="text-xs text-text-secondary/60 mt-1">{t("noAssetsHint")}</p>
                                </div>
                            ) : (
                                <>
                                    {/* Characters */}
                                    {characters.length > 0 && (
                                        <div>
                                            <div className="flex items-center gap-1.5 mb-2">
                                                <User size={12} className="text-blue-400" />
                                                <span className="text-[0.6875rem] font-medium text-text-secondary uppercase tracking-wide">{t("characters")}</span>
                                            </div>
                                            <div className="grid grid-cols-2 gap-2">
                                                {characters.map((c) => {
                                                    const thumb = getAssetThumbnail(c, "character");
                                                    const assetRef = referenceFor(c, "character");
                                                    return (
                                                        <div
                                                            key={c.id}
                                                            className="group relative rounded-xl border border-glass-border bg-glass transition-all duration-200 hover:border-foreground/30 hover:bg-hover-bg"
                                                        >
                                                            <button
                                                                type="button"
                                                                onClick={() => {
                                                                    if (!assetRef) return;
                                                                    onSelectAsset("character", c.name, assetRef);
                                                                    onClose();
                                                                }}
                                                                className="flex w-full flex-col items-center gap-1.5 rounded-xl p-2 pr-8"
                                                            >
                                                                <div className="w-12 h-12 rounded-lg bg-glass overflow-hidden flex items-center justify-center">
                                                                    {thumb ? (
                                                                        <PreviewImage src={thumb} alt={c.name} className="w-full h-full" noLightbox />
                                                                    ) : (
                                                                        <User size={16} className="text-text-secondary/40" />
                                                                    )}
                                                                </div>
                                                                <span className="text-[0.6875rem] text-foreground group-hover:text-primary truncate w-full text-center">{c.name}</span>
                                                            </button>
                                                            {assetRef ? (
                                                                <button
                                                                    type="button"
                                                                    onClick={(event) => {
                                                                        event.stopPropagation();
                                                                        setEditorTarget(assetRef);
                                                                    }}
                                                                    aria-label={t("editAsset", { name: c.name })}
                                                                    title={t("editAsset", { name: c.name })}
                                                                    className="absolute right-1.5 top-1.5 grid h-7 w-7 place-items-center rounded-lg border border-glass-border bg-surface text-text-secondary shadow-sm transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                                                                >
                                                                    <Pencil size={13} aria-hidden="true" />
                                                                </button>
                                                            ) : null}
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                    )}

                                    {/* Scenes */}
                                    {scenes.length > 0 && (
                                        <div>
                                            <div className="flex items-center gap-1.5 mb-2">
                                                <MapPin size={12} className="text-green-400" />
                                                <span className="text-[0.6875rem] font-medium text-text-secondary uppercase tracking-wide">{t("scenes")}</span>
                                            </div>
                                            <div className="grid grid-cols-2 gap-2">
                                                {scenes.map((s) => {
                                                    const thumb = getAssetThumbnail(s, "scene");
                                                    const assetRef = referenceFor(s, "scene");
                                                    return (
                                                        <div
                                                            key={s.id}
                                                            className="group relative rounded-xl border border-glass-border bg-glass transition-all duration-200 hover:border-foreground/30 hover:bg-hover-bg"
                                                        >
                                                            <button
                                                                type="button"
                                                                onClick={() => {
                                                                    if (!assetRef) return;
                                                                    onSelectAsset("scene", s.name, assetRef);
                                                                    onClose();
                                                                }}
                                                                className="flex w-full flex-col items-center gap-1.5 rounded-xl p-2 pr-8"
                                                            >
                                                                <div className="w-12 h-12 rounded-lg bg-glass overflow-hidden flex items-center justify-center">
                                                                    {thumb ? (
                                                                        <PreviewImage src={thumb} alt={s.name} className="w-full h-full" noLightbox />
                                                                    ) : (
                                                                        <MapPin size={16} className="text-text-secondary/40" />
                                                                    )}
                                                                </div>
                                                                <span className="text-[0.6875rem] text-foreground group-hover:text-primary truncate w-full text-center">{s.name}</span>
                                                            </button>
                                                            {assetRef ? (
                                                                <button
                                                                    type="button"
                                                                    onClick={(event) => {
                                                                        event.stopPropagation();
                                                                        setEditorTarget(assetRef);
                                                                    }}
                                                                    aria-label={t("editAsset", { name: s.name })}
                                                                    title={t("editAsset", { name: s.name })}
                                                                    className="absolute right-1.5 top-1.5 grid h-7 w-7 place-items-center rounded-lg border border-glass-border bg-surface text-text-secondary shadow-sm transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                                                                >
                                                                    <Pencil size={13} aria-hidden="true" />
                                                                </button>
                                                            ) : null}
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                    )}

                                    {/* Props */}
                                    {props.length > 0 && (
                                        <div>
                                            <div className="flex items-center gap-1.5 mb-2">
                                                <Package size={12} className="text-orange-400" />
                                                <span className="text-[0.6875rem] font-medium text-text-secondary uppercase tracking-wide">{t("props")}</span>
                                            </div>
                                            <div className="grid grid-cols-2 gap-2">
                                                {props.map((p) => {
                                                    const thumb = getAssetThumbnail(p, "prop");
                                                    const assetRef = referenceFor(p, "prop");
                                                    return (
                                                        <div
                                                            key={p.id}
                                                            className="group relative rounded-xl border border-glass-border bg-glass transition-all duration-200 hover:border-foreground/30 hover:bg-hover-bg"
                                                        >
                                                            <button
                                                                type="button"
                                                                onClick={() => {
                                                                    if (!assetRef) return;
                                                                    onSelectAsset("prop", p.name, assetRef);
                                                                    onClose();
                                                                }}
                                                                className="flex w-full flex-col items-center gap-1.5 rounded-xl p-2 pr-8"
                                                            >
                                                                <div className="w-12 h-12 rounded-lg bg-glass overflow-hidden flex items-center justify-center">
                                                                    {thumb ? (
                                                                        <PreviewImage src={thumb} alt={p.name} className="w-full h-full" noLightbox />
                                                                    ) : (
                                                                        <Package size={16} className="text-text-secondary/40" />
                                                                    )}
                                                                </div>
                                                                <span className="text-[0.6875rem] text-foreground group-hover:text-primary truncate w-full text-center">{p.name}</span>
                                                            </button>
                                                            {assetRef ? (
                                                                <button
                                                                    type="button"
                                                                    onClick={(event) => {
                                                                        event.stopPropagation();
                                                                        setEditorTarget(assetRef);
                                                                    }}
                                                                    aria-label={t("editAsset", { name: p.name })}
                                                                    title={t("editAsset", { name: p.name })}
                                                                    className="absolute right-1.5 top-1.5 grid h-7 w-7 place-items-center rounded-lg border border-glass-border bg-surface text-text-secondary shadow-sm transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                                                                >
                                                                    <Pencil size={13} aria-hidden="true" />
                                                                </button>
                                                            ) : null}
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                    )}
                                </>
                            )}
                        </div>
                    </motion.div>
                    </>
                )}
            </AnimatePresence>
            {editorTarget ? (
                <SharedAssetEditor
                    open
                    assetRef={editorTarget}
                    onClose={() => setEditorTarget(null)}
                    onMutated={(asset, ref) => patchProject(asset, ref)}
                    onConverted={(asset, previousRef, nextRef) => {
                        patchProject(asset, nextRef, previousRef);
                        setEditorTarget(nextRef);
                    }}
                />
            ) : null}
        </>
    );
}
