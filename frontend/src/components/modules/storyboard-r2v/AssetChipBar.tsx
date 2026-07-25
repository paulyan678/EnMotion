"use client";

import { Pencil } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import SharedAssetEditor from "@/components/assets/SharedAssetEditor";
import {
    assetRefFromResolvedAsset,
    patchResolvedProjectAsset,
    type AssetRef,
    type EditableAsset,
} from "@/components/assets/assetEditorTypes";
import type { Character, Prop, Scene } from "@/store/projectStore";
import { useProjectStore } from "@/store/projectStore";

interface AssetChipBarProps {
    characters: Character[];
    scenes: Scene[];
    props: Prop[];
    onInsertAsset: (type: string, name: string, ref: AssetRef) => void;
}

export default function AssetChipBar({ characters, scenes, props, onInsertAsset }: AssetChipBarProps) {
    const t = useTranslations("storyboardR2V");
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);
    const [editorTarget, setEditorTarget] = useState<AssetRef | null>(null);

    if (characters.length === 0 && scenes.length === 0 && props.length === 0) {
        return null;
    }

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
    const renderChip = (
        asset: EditableAsset,
        assetType: AssetRef["assetType"],
        dotClass: string,
    ) => {
        const assetRef = referenceFor(asset, assetType);
        return (
            <span
                key={`${assetType}:${asset.id}`}
                className="inline-flex max-w-[240px] items-center rounded-full border border-glass-border bg-surface-inset"
            >
                <button
                    type="button"
                    onClick={() => {
                        if (assetRef) onInsertAsset(assetType, asset.name, assetRef);
                    }}
                    className="inline-flex min-w-0 items-center gap-1.5 rounded-l-full px-3 py-1 text-[13px] text-text-secondary transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                >
                    <span className={`h-[6px] w-[6px] shrink-0 rounded-full ${dotClass}`} />
                    <span className="truncate">{asset.name}</span>
                </button>
                {assetRef ? (
                    <button
                        type="button"
                        onClick={() => setEditorTarget(assetRef)}
                        aria-label={t("editAsset", { name: asset.name })}
                        title={t("editAsset", { name: asset.name })}
                        className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-text-secondary transition-colors hover:bg-hover-bg hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                    >
                        <Pencil size={12} aria-hidden="true" />
                    </button>
                ) : null}
            </span>
        );
    };

    return (
        <>
            <div className="flex flex-wrap items-center gap-2 py-1">
                {characters.map((asset) => renderChip(asset, "character", "bg-blue-400"))}
                {scenes.map((asset) => renderChip(asset, "scene", "bg-teal-400"))}
                {props.map((asset) => renderChip(asset, "prop", "bg-orange-400"))}
            </div>
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
