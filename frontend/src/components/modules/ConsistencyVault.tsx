"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { motion, AnimatePresence } from "framer-motion";
import { User, MapPin, Box, Lock, Unlock, RefreshCw, Upload, Image as ImageIcon, X, Trash2, Plus } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { api, crudApi } from "@/lib/api";
import { primaryAssetDisplayUrl } from "@/lib/assetImage";
import UploadAssetModal from "../modals/UploadAssetModal";
import WorkflowActionButton from "@/components/shared/WorkflowActionButton";
import ScrollFlowActions from "@/components/shared/ScrollFlowActions";
import PreviewImage from "@/components/shared/preview/PreviewImage";
import {
    notifyAssetLibraryChanged,
    notifyProjectAssetChanged,
} from "@/lib/assetLibrarySync";
import SharedAssetEditor from "@/components/assets/SharedAssetEditor";
import {
    assetRefFromResolvedAsset,
    patchResolvedProjectAsset,
    type AssetRef,
    type EditableAsset,
} from "@/components/assets/assetEditorTypes";

export default function ConsistencyVault() {
    const tv = useTranslations("vault");
    const tStep = useTranslations("stepHeader");
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);



    const [activeTab, setActiveTab] = useState<"character" | "scene" | "prop">("character");

    // Store ID and Type instead of full object to ensure reactivity
    const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
    const [selectedAssetType, setSelectedAssetType] = useState<string | null>(null);

    // Create asset dialog state
    const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);

    // Upload modal state
    const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
    const [uploadTarget, setUploadTarget] = useState<{ id: string; type: string; name: string; description: string } | null>(null);

    // Derive selected asset from currentProject
    const selectedAsset = currentProject ? (() => {
        if (!selectedAssetId || !selectedAssetType) return null;
        const list = selectedAssetType === "character" ? currentProject.characters :
            selectedAssetType === "scene" ? currentProject.scenes :
                selectedAssetType === "prop" ? currentProject.props : [];
        return list?.find((a: any) => a.id === selectedAssetId) || null;
    })() : null;
    const selectedAssetRef =
        currentProject && selectedAsset && selectedAssetType
            ? assetRefFromResolvedAsset(
                selectedAsset,
                selectedAssetType as "character" | "scene" | "prop",
                {
                    projectId: currentProject.id,
                    seriesId: currentProject.series_id,
                },
            )
            : null;

    const patchCurrentProjectAsset = (
        updated: EditableAsset,
        nextRef: AssetRef,
        previousRef?: AssetRef,
    ) => {
        if (!currentProject) return;
        const nextProject = patchResolvedProjectAsset(
            currentProject,
            updated,
            nextRef,
            previousRef,
        );
        updateProject(currentProject.id, nextProject);
    };

    // Delete asset handler
    const handleDeleteAsset = async (assetId: string, type: string) => {
        if (!currentProject) return;
        const typeLabel = type === "character" ? tv("characters") : type === "scene" ? tv("scenes") : tv("props");
        if (!confirm(tv("confirmDeleteAsset", { type: typeLabel }))) return;

        try {
            let updatedProject;
            if (type === "character") {
                updatedProject = await crudApi.deleteCharacter(currentProject.id, assetId);
            } else if (type === "scene") {
                updatedProject = await crudApi.deleteScene(currentProject.id, assetId);
            } else if (type === "prop") {
                updatedProject = await crudApi.deleteProp(currentProject.id, assetId);
            } else {
                return;
            }
            updateProject(currentProject.id, updatedProject);
            notifyAssetLibraryChanged({
                source: "project",
                projectId: currentProject.id,
                seriesId: currentProject.series_id,
                assetType: type as "character" | "scene" | "prop",
                assetId,
            });
        } catch (error) {
            console.error("Failed to delete asset:", error);
            alert(tv("deleteAssetFailed"));
        }
    };

    // Create asset handler
    const handleCreateAsset = async (data: { name: string; description: string }) => {
        if (!currentProject) return;

        try {
            if (activeTab === "character") {
                await crudApi.createCharacter(currentProject.id, data);
            } else if (activeTab === "scene") {
                await crudApi.createScene(currentProject.id, data);
            } else if (activeTab === "prop") {
                await crudApi.createProp(currentProject.id, data);
            }
            // Refresh project data
            const updatedProject = await api.getProject(currentProject.id);
            updateProject(currentProject.id, updatedProject);
            notifyAssetLibraryChanged({
                source: "project",
                projectId: updatedProject.id,
                seriesId: updatedProject.series_id,
            });
            setIsCreateDialogOpen(false);
        } catch (error) {
            console.error("Failed to create asset:", error);
            alert(tv("createAssetFailed"));
        }
    };

    // Sync descriptions from Script module to Assets
    const handleSyncDescriptions = async () => {
        if (!currentProject) return;

        const confirmed = confirm(
            tv("syncDescription")
        );

        if (!confirmed) return;

        try {
            const updatedProject = await api.syncDescriptions(currentProject.id);
            updateProject(currentProject.id, updatedProject);
            alert(tv("syncSuccess"));
        } catch (error) {
            console.error("Failed to sync descriptions:", error);
            alert(tv("syncFailed"));
        }
    };

    // Upload handlers
    const handleOpenUploadModal = (asset: any, type: string) => {
        setUploadTarget({
            id: asset.id,
            type: type,
            name: asset.name,
            description: asset.description
        });
        setIsUploadModalOpen(true);
    };

    const handleUploadComplete = async (updatedScript: any) => {
        if (currentProject) {
            updateProject(currentProject.id, updatedScript);
            if (uploadTarget) {
                const collection =
                    uploadTarget.type === "character"
                        ? updatedScript.characters
                        : uploadTarget.type === "scene"
                            ? updatedScript.scenes
                            : updatedScript.props;
                const updatedAsset = collection?.find(
                    (candidate: EditableAsset) => candidate.id === uploadTarget.id,
                );
                notifyProjectAssetChanged(
                    updatedScript,
                    updatedAsset ?? {
                        id: uploadTarget.id,
                        source: "episode",
                        source_id: updatedScript.id,
                    },
                    uploadTarget.type as "character" | "scene" | "prop",
                );
            }
        }
        setIsUploadModalOpen(false);
        setUploadTarget(null);
    };

    const assets = activeTab === "character" ? currentProject?.characters :
        activeTab === "scene" ? currentProject?.scenes :
            activeTab === "prop" ? currentProject?.props : [];

    return (
        <div className="flex flex-col h-full text-foreground">
            <h1 className="sr-only">{tStep("vaultTitle")}</h1>
            <div className="flex-1 overflow-y-auto custom-scrollbar">
                {/* Filters intentionally share the asset list's scroll owner. */}
                <ScrollFlowActions
                    align="between"
                    label={tStep("vaultTitle")}
                    className="gap-3 border-b border-glass-border px-6 py-4"
                >
                    <div className="flex flex-wrap gap-2">
                        <TabButton
                            active={activeTab === "character"}
                            onClick={() => setActiveTab("character")}
                            icon={<User size={14} />}
                            label={tv("characters")}
                            count={currentProject?.characters?.length || 0}
                        />
                        <TabButton
                            active={activeTab === "scene"}
                            onClick={() => setActiveTab("scene")}
                            icon={<MapPin size={14} />}
                            label={tv("scenes")}
                            count={currentProject?.scenes?.length || 0}
                        />
                        <TabButton
                            active={activeTab === "prop"}
                            onClick={() => setActiveTab("prop")}
                            icon={<Box size={14} />}
                            label={tv("props")}
                            count={currentProject?.props?.length || 0}
                        />
                    </div>

                    <WorkflowActionButton
                        variant="secondary"
                        size="sm"
                        leftIcon={<RefreshCw />}
                        onClick={handleSyncDescriptions}
                        title={tv("syncDescHint")}
                    >
                        {tv("syncDesc")}
                    </WorkflowActionButton>
                </ScrollFlowActions>

                <div className="p-6">
                    {!currentProject ? (
                    <div className="flex items-center justify-center h-full text-text-muted">
                        {tv("loadingProject")}
                    </div>
                ) : assets?.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-text-muted gap-4">
                        <div className="w-16 h-16 rounded-full bg-glass flex items-center justify-center">
                            {activeTab === "character" ? <User size={32} /> : activeTab === "scene" ? <MapPin size={32} /> : <Box size={32} />}
                        </div>
                        <p>{tv("noItemsFound", { type: activeTab === "character" ? tv("characters") : activeTab === "scene" ? tv("scenes") : tv("props") })}</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
                        {assets?.map((asset: any) => (
                            <AssetCard
                                key={asset.id}
                                asset={asset}
                                type={activeTab}
                                isGenerating={false}
                                onGenerate={() => {
                                    setSelectedAssetId(asset.id);
                                    setSelectedAssetType(activeTab);
                                }}
                                onToggleLock={() => api.toggleAssetLock(currentProject.id, asset.id, activeTab).then(updated => updateProject(currentProject.id, updated))}
                                onClick={() => {
                                    setSelectedAssetId(asset.id);
                                    setSelectedAssetType(activeTab);
                                }}
                                onDelete={() => handleDeleteAsset(asset.id, activeTab)}
                                onUpload={() => handleOpenUploadModal(asset, activeTab)}
                            />
                        ))}
                        {/* Create New Asset Button */}
                        <motion.div
                            layout
                            initial={{ opacity: 0, scale: 0.9 }}
                            animate={{ opacity: 1, scale: 1 }}
                            onClick={() => setIsCreateDialogOpen(true)}
                            className="group relative aspect-[3/4] bg-surface rounded-2xl border-2 border-dashed border-glass-border hover:border-primary/50 overflow-hidden transition-all cursor-pointer flex items-center justify-center hover:bg-glass"
                        >
                            <div className="flex flex-col items-center gap-3 text-text-secondary group-hover:text-primary transition-colors">
                                <Plus size={40} />
                                <span className="text-sm font-medium">{tv("addAsset", { type: activeTab === "character" ? tv("characters") : activeTab === "scene" ? tv("scenes") : tv("props") })}</span>
                            </div>
                        </motion.div>
                    </div>
                    )}
                </div>
            </div>

            {selectedAssetRef ? (
                <SharedAssetEditor
                    open
                    assetRef={selectedAssetRef}
                    onClose={() => {
                        setSelectedAssetId(null);
                        setSelectedAssetType(null);
                    }}
                    onMutated={(updated, ref) => {
                        patchCurrentProjectAsset(updated, ref);
                    }}
                    onConverted={(updated, previousRef, nextRef) => {
                        patchCurrentProjectAsset(updated, nextRef, previousRef);
                        setSelectedAssetId(updated.id);
                        setSelectedAssetType(nextRef.assetType);
                    }}
                />
            ) : null}



            {/* Create Asset Dialog */}
            <AnimatePresence>
                {isCreateDialogOpen && (
                    <CreateAssetDialog
                        type={activeTab}
                        onClose={() => setIsCreateDialogOpen(false)}
                        onCreate={handleCreateAsset}
                    />
                )}
            </AnimatePresence>

            {/* Upload Asset Modal */}
            {uploadTarget && currentProject && (
                <UploadAssetModal
                    isOpen={isUploadModalOpen}
                    onClose={() => {
                        setIsUploadModalOpen(false);
                        setUploadTarget(null);
                    }}
                    assetId={uploadTarget.id}
                    assetType={uploadTarget.type as "character" | "scene" | "prop"}
                    assetName={uploadTarget.name}
                    defaultDescription={uploadTarget.description}
                    scriptId={currentProject.id}
                    onUploadComplete={handleUploadComplete}
                />
            )}
        </div >
    );
}
function TabButton({ active, onClick, icon, label, count }: any) {
    return (
        <button
            onClick={onClick}
            className={`inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 border transition-colors ${active
                ? "bg-[rgba(100,108,255,0.12)] text-foreground border-primary"
                : "bg-glass text-text-secondary hover:text-foreground border-glass-border hover:border-glass-border-strong"
                }`}
        >
            <span className={active ? "text-primary" : ""}>{icon}</span>
            <span className="font-mono text-[0.65625rem] font-semibold uppercase tracking-[0.14em]">{label}</span>
            <span className={`font-mono text-[0.5625rem] px-1.5 py-0.5 rounded-full border ${
                active
                    ? "text-primary border-primary/40 bg-[rgba(100,108,255,0.08)]"
                    : "text-text-muted border-glass-border bg-black/30"
            }`}>{count}</span>
        </button>
    );
}

function AssetCard({ asset, type, isGenerating, onGenerate, onToggleLock, onClick, onDelete, onUpload }: any) {
    const tv = useTranslations("vault");
    const isLocked = asset.locked || false;
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file || !currentProject) return;

        try {
            // 1. Upload file
            const { url } = await api.uploadFile(file);

            // 2. Update asset image
            const updatedProject = await api.updateAssetImage(currentProject.id, asset.id, type, url);

            // 3. Update local state
            updateProject(currentProject.id, updatedProject);
            notifyProjectAssetChanged(
                currentProject,
                asset,
                type as "character" | "scene" | "prop",
            );
        } catch (error) {
            console.error("Failed to upload asset image:", error);
            alert(tv("uploadImageFailed"));
        }
    };

    const fullImageUrl = primaryAssetDisplayUrl(asset, type);

    return (
        <motion.div
            layout
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            onClick={onClick}
            className={`group relative aspect-[3/4] bg-surface rounded-2xl border overflow-hidden transition-colors cursor-pointer ${isLocked ? 'border-primary/60 border-dashed' : 'border-glass-border hover:border-primary/50'
                }`}
        >
            {/* Image Area */}
            <div className="absolute inset-0 bg-gradient-to-b from-transparent to-black/60 z-10" />

            {fullImageUrl ? (
                <PreviewImage
                    src={fullImageUrl}
                    alt={asset.name}
                    className="h-full w-full"
                    imgClassName="object-cover"
                    noLightbox
                />
            ) : (
                <div className="w-full h-full flex items-center justify-center bg-glass">
                    <ImageIcon className="text-text-muted" size={48} />
                </div>
            )}

            {/* Loading Overlay */}
            {isGenerating && (
                <div className="absolute inset-0 z-20 bg-overlay backdrop-blur-sm flex items-center justify-center flex-col gap-2">
                    <RefreshCw className="animate-spin text-primary" size={32} />
                    <span className="text-xs font-mono text-primary">{tv("generating")}</span>
                </div>
            )}

            {/* Top Actions Overlay */}
            <div className="absolute top-2 right-2 z-30 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                    onClick={(e) => {
                        e.stopPropagation();
                        onDelete();
                    }}
                    className="p-2 rounded-full backdrop-blur-md bg-red-500/20 text-red-400 hover:bg-red-500/40 transition-colors"
                    title={tv("deleteAsset")}
                >
                    <Trash2 size={14} />
                </button>
                <button
                    onClick={(e) => {
                        e.stopPropagation();
                        onToggleLock();
                    }}
                    className={`p-2 rounded-full backdrop-blur-md transition-colors ${isLocked
                        ? "bg-primary/20 text-primary hover:bg-primary/30"
                        : "bg-surface text-foreground hover:bg-hover-bg"
                        }`}
                >
                    {isLocked ? <Lock size={14} /> : <Unlock size={14} />}
                </button>
            </div>

            {/* Bottom Info */}
            <div className="absolute bottom-0 left-0 right-0 p-4 z-30">
                <h3 className="text-lg font-bold text-foreground mb-1 truncate">{asset.name}</h3>
                <p className="text-xs text-foreground/80 line-clamp-2 mb-3 h-8">
                    {asset.description || tv("noDescription")}
                </p>

                <div className="flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity transform translate-y-2 group-hover:translate-y-0">
                    <WorkflowActionButton
                        onClick={(e) => {
                            e.stopPropagation();
                            onGenerate();
                        }}
                        disabled={isLocked || isGenerating}
                        loading={isGenerating}
                        leftIcon={!isGenerating ? <RefreshCw /> : undefined}
                        variant="primary"
                        size="sm"
                        className="flex-1"
                    >
                        {isGenerating ? tv("generating") : tv("generate")}
                    </WorkflowActionButton>
                    <button
                        onClick={(e) => {
                            e.stopPropagation();
                            onUpload?.();
                        }}
                        className="px-2.5 rounded-full bg-glass hover:bg-hover-bg border border-glass-border text-foreground cursor-pointer transition-colors"
                        title={tv("uploadAsset")}
                    >
                        <Upload size={14} />
                    </button>
                </div>
            </div>
        </motion.div>
    );
}



function CreateAssetDialog({ type, onClose, onCreate }: { type: string; onClose: () => void; onCreate: (data: { name: string; description: string }) => void }) {
    const tv = useTranslations("vault");
    const [name, setName] = useState("");
    const [description, setDescription] = useState("");
    const [isSubmitting, setIsSubmitting] = useState(false);

    const handleSubmit = async () => {
        if (!name.trim()) {
            alert(tv("nameRequired"));
            return;
        }
        setIsSubmitting(true);
        try {
            await onCreate({ name: name.trim(), description: description.trim() });
        } finally {
            setIsSubmitting(false);
        }
    };

    const typeLabel = type === "character" ? tv("characters") : type === "scene" ? tv("scenes") : tv("props");

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-overlay backdrop-blur-sm p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-surface border border-glass-border rounded-2xl w-full max-w-md overflow-hidden shadow-lg"
            >
                <div className="p-6 border-b border-glass-border flex justify-between items-center bg-surface">
                    <div className="flex items-center gap-3">
                        <Plus className="text-primary" size={20} />
                        <h2 className="text-lg font-bold text-foreground">{tv("createNew", { type: typeLabel })}</h2>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-hover-bg rounded-lg transition-colors">
                        <X size={20} className="text-text-secondary" />
                    </button>
                </div>

                <div className="p-6 space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-2">{tv("nameLabel")}</label>
                        <input
                            type="text"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            placeholder={tv("namePlaceholder", { type: typeLabel })}
                            className="w-full px-4 py-3 bg-input-bg border border-glass-border rounded-lg text-foreground placeholder-text-muted focus:border-primary/50 focus:outline-none"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-2">{tv("description")}</label>
                        <textarea
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            placeholder={tv("descriptionPlaceholder", { type: typeLabel })}
                            rows={4}
                            className="w-full px-4 py-3 bg-input-bg border border-glass-border rounded-lg text-foreground placeholder-text-muted focus:border-primary/50 focus:outline-none resize-none"
                        />
                    </div>
                </div>

                <div className="p-6 border-t border-glass-border flex justify-end gap-3">
                    <button
                        onClick={onClose}
                        className="px-6 py-2 bg-glass hover:bg-hover-bg text-foreground rounded-lg transition-colors"
                    >
                        {tv("cancel")}
                    </button>
                    <button
                        onClick={handleSubmit}
                        disabled={isSubmitting || !name.trim()}
                        className="px-6 py-2 bg-primary hover:bg-primary/90 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                    >
                        {isSubmitting && <RefreshCw size={16} className="animate-spin" />}
                        {tv("createType", { type: typeLabel })}
                    </button>
                </div>
            </motion.div>
        </div>
    );
}
